"""TradingAgentsGraph 呼び出し（最重要・v3.2）。

Phase 0 確定事実：
- propagate(company_name, trade_date, asset_type) は【同期】メソッド。
  並列化は asyncio.to_thread で行う（await propagate は禁止）。
- 戻り値は (final_state, signal) のタプル。signal は5段階レーティング【文字列】
  （Buy/Overweight/Hold/Underweight/Sell）。dict でも BUY/SELL/HOLD でもない。
- 最終判断に confidence は存在しない（confidence フィルタは廃止）。
- config は素の dict。独自キー（portfolio_context 等）は黙殺される
  → 文脈注入は tradingagents_patch.install_portfolio_injection を使う。
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from core import config_loader as cfg
from logger import get_logger

log = get_logger("ta_runner")

# ローカル clone（multiagent_mt5/TradingAgents）を import パスへ追加。
# pip install -e ./TradingAgents 済みなら不要だが、二重登録は無害。
_CLONE = Path(__file__).resolve().parent.parent / "TradingAgents"
if _CLONE.exists() and str(_CLONE) not in sys.path:
    sys.path.insert(0, str(_CLONE))


# --- TA_CONFIG は risk.yaml から遅延生成（import 時に本体依存を発生させない）---

def get_ta_config() -> dict:
    from tradingagents.default_config import DEFAULT_CONFIG

    ta_cfg = DEFAULT_CONFIG.copy()
    conf = cfg.load_risk().get("tradingagents", {})
    ta_cfg["llm_provider"] = conf.get("llm_provider", "openai")
    ta_cfg["deep_think_llm"] = conf.get("deep_think_llm", "gpt-5.5")
    ta_cfg["quick_think_llm"] = conf.get("quick_think_llm", "gpt-5.4-mini")
    ta_cfg["max_debate_rounds"] = conf.get("max_debate_rounds", 1)
    ta_cfg["max_risk_discuss_rounds"] = conf.get("max_risk_discuss_rounds", 1)
    ta_cfg["checkpoint_enabled"] = conf.get("checkpoint_enabled", True)
    ta_cfg["output_language"] = conf.get("output_language", "Japanese")
    return ta_cfg


# --- ポートフォリオ文脈（注入パッチが参照）---

_current_portfolio_state = None  # 各サイクル開始時にセット


def set_current_portfolio_state(portfolio_state) -> None:
    global _current_portfolio_state
    _current_portfolio_state = portfolio_state


def get_ticker_context(ticker: str) -> str:
    """resolve_instrument_context のラッパーから呼ばれる（§0.5.2）。"""
    ps = _current_portfolio_state
    if ps is None:
        return ""
    return build_ticker_context(ps, ticker)


def build_ticker_context(portfolio_state, ticker: str) -> str:
    """1銘柄分のプロンプト注入テキスト。保有中か否かで指示を変える。"""
    base_context = portfolio_state.to_llm_context()
    position = portfolio_state.get_position(ticker)

    if position:
        ticker_context = (
            f"\n=== POSITION STATUS FOR {ticker} ===\n"
            f"Status: CURRENTLY HOLDING (LONG)\n"
            f"Total lots: {position.total_lots} (initial: {position.initial_lots})\n"
            f"Average entry price: ${position.avg_entry_price:.2f}\n"
            f"Current SL: ${position.current_sl:.2f}\n"
            f"Unrealized P&L: ¥{position.unrealized_pnl:+.0f}\n"
            f"Scale-ins done: {position.scale_count}\n"
            f"\nRating guidance for a position you ALREADY HOLD (long-only book):\n"
            f"- Buy/Overweight: conviction to ADD (only if outlook improved AND unrealized P&L > 0)\n"
            f"- Hold: maintain the position\n"
            f"- Underweight: reduce / take partial profit\n"
            f"- Sell: exit the position fully\n"
            f"=================================="
        )
    else:
        slots = portfolio_state.available_slots()
        if slots <= 0:
            ticker_context = (
                f"\n=== POSITION STATUS FOR {ticker} ===\n"
                f"Status: NOT HOLDING. Portfolio is FULL.\n"
                f"Prefer Hold. A new long can only be opened if an existing "
                f"position is closed this cycle.\n"
                f"=================================="
            )
        else:
            ticker_context = (
                f"\n=== POSITION STATUS FOR {ticker} ===\n"
                f"Status: NOT HOLDING. Available slots: {slots}.\n"
                f"This is a LONG-ONLY book: Sell/Underweight for an instrument "
                f"you do not hold means 'stay flat', not 'go short'.\n"
                f"=================================="
            )
    return base_context + ticker_context


# --- parse_decision（5段階レーティング → システムアクション変換）---

_VALID = {"BUY", "HOLD", "INCREASE", "DECREASE", "CLOSE"}
_RATING_SET = {"buy", "overweight", "hold", "underweight", "sell"}


def _hold(raw_signal, reason: str) -> dict:
    return {
        "action": "HOLD",
        "rating": "Hold",
        "rationale": f"defaulting to HOLD - {reason}",
        "raw": raw_signal,
        "converted_from": None,
    }


def parse_decision(raw_signal, ticker: str, portfolio_state) -> dict:
    """propagate 第2戻り値（レーティング文字列）を統一アクションへ変換する。"""
    try:
        rating_raw = str(raw_signal).strip().strip("*").strip()
        rating_norm = rating_raw.lower()
        if rating_norm not in _RATING_SET:
            return _hold(raw_signal, f"Unrecognized rating: {rating_raw!r}")

        rating = rating_norm.capitalize()
        holding = portfolio_state.get_position(ticker) is not None

        if holding:
            mapping = {
                "buy": "INCREASE", "overweight": "INCREASE",
                "hold": "HOLD",
                "underweight": "DECREASE",
                "sell": "CLOSE",
            }
        else:
            mapping = {
                "buy": "BUY", "overweight": "BUY",
                "hold": "HOLD",
                "underweight": "HOLD",
                "sell": "HOLD",
            }
        action = mapping[rating_norm]
        if action not in _VALID:
            action = "HOLD"

        return {
            "action": action,
            "rating": rating,
            "rationale": "",
            "raw": raw_signal,
            "converted_from": rating,
        }
    except Exception as e:
        return _hold(raw_signal, f"PARSE ERROR: {e}")


def extract_rationale(final_state) -> str:
    """final_state['final_trade_decision'] の markdown から根拠テキストを抽出する。
    取得不能なら空文字（フェイルセーフ）。"""
    try:
        if not isinstance(final_state, dict):
            return ""
        text = final_state.get("final_trade_decision") or ""
        if not isinstance(text, str):
            text = str(text)
        wanted = ("executive summary", "investment thesis")
        picked = [
            line.strip()
            for line in text.splitlines()
            if any(w in line.lower() for w in wanted)
        ]
        if picked:
            return " | ".join(picked)[:1000]
        return text.strip()[:500]
    except Exception:
        return ""


# --- 同期→非同期実行（Tenacity で同期リトライ）---

def _retry_decorator():
    from tenacity import (
        retry, retry_if_exception_type, stop_after_attempt, wait_exponential,
    )

    exe = cfg.load_risk().get("execution", {})
    return retry(
        retry=retry_if_exception_type(Exception),
        wait=wait_exponential(
            multiplier=exe.get("retry_multiplier", 2),
            min=exe.get("retry_wait_min_seconds", 4),
            max=exe.get("retry_wait_max_seconds", 60),
        ),
        stop=stop_after_attempt(exe.get("retry_max_attempts", 5)),
        reraise=True,
    )


def _call_trading_agents_sync(ta, ticker: str, date_str: str):
    """propagate は同期。Tenacity で同期リトライする。"""
    wrapped = _retry_decorator()(lambda: ta.propagate(ticker, date_str))
    return wrapped()  # -> (final_state, rating_str)


async def run_single(ticker: str, date_str: str, portfolio_state) -> dict:
    """1銘柄の分析。propagate は同期のため to_thread でオフロードする。
    エラー時は必ず HOLD を返す。"""
    try:
        from tradingagents.graph.trading_graph import TradingAgentsGraph

        ta = TradingAgentsGraph(debug=False, config=get_ta_config())
        final_state, rating_str = await asyncio.to_thread(
            _call_trading_agents_sync, ta, ticker, date_str
        )
        decision = parse_decision(rating_str, ticker, portfolio_state)
        decision["rationale"] = extract_rationale(final_state)
        holding = "holding" if portfolio_state.get_position(ticker) else "flat"
        log.info("%s: rating=%s (%s) → %s", ticker, decision["rating"], holding, decision["action"])
        return {"ticker": ticker, "decision": decision, "error": None}
    except Exception as e:
        log.error("%s: runner failed after retries: %s", ticker, e)
        return {
            "ticker": ticker,
            "decision": _hold(None, f"Runner failed after retries: {e}"),
            "error": str(e),
        }


async def run_parallel(tickers, date_str, portfolio_state, max_parallel: int = 4) -> list[dict]:
    """複数銘柄を並列実行。max_parallel で API レート制限を回避する。"""
    set_current_portfolio_state(portfolio_state)
    semaphore = asyncio.Semaphore(max_parallel)

    async def _run(ticker):
        async with semaphore:
            return await run_single(ticker, date_str, portfolio_state)

    return await asyncio.gather(*[_run(t) for t in tickers])
