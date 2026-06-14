"""週次銘柄スクリーニングモジュール

毎週月曜日の起動時に実行し、UNIVERSE（yfinanceティッカー）から
最も有望な1銘柄を選定して config.SYMBOLS を更新する。

フロー:
  1. yfinance でモメンタム/RSI/出来高スコアを算出 → 上位 TOP_N 候補に絞り込む
  2. TAAnalyzer で各候補を TradingAgents 分析 (既存インスタンスを再利用)
  3. BUY/Overweight 優先でランキングし、今週の監視銘柄 1 銘柄を決定
  4. config.SYMBOLS をメモリ上で更新 → H1スレッドが次のイテレーションから新銘柄を処理
  5. 履歴を logs/weekly_history.json に保存
  6. Discord に週次レポートを送信
"""

from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Optional

import pandas as pd
import yfinance as yf

import config
import discord_notifier
import symbol_map

if TYPE_CHECKING:
    from ta_analyzer import TAAnalyzer

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────
# スクリーナー (yfinance / 無料)
# ──────────────────────────────────────────────────────────────

def _calc_rsi(close: pd.Series, period: int = 14) -> float:
    """直近RSI値をpandasのみで計算する。"""
    delta = close.diff().dropna()
    gain  = delta.clip(lower=0).rolling(period).mean()
    loss  = (-delta.clip(upper=0)).rolling(period).mean()
    rs    = gain / loss.replace(0, float("nan"))
    rsi   = 100 - (100 / (1 + rs))
    return float(rsi.iloc[-1])


def _score_ticker(ticker: str) -> Optional[dict]:
    """モメンタム・RSI・出来高比率の複合スコアを算出する。失敗時はNone。"""
    try:
        df = yf.download(ticker, period="3mo", interval="1d", progress=False, auto_adjust=True)
        if df is None or len(df) < 20:
            logger.warning("[Screener] %s: データ不足でスキップ", ticker)
            return None

        close = df["Close"].squeeze()

        momentum_20d = float(close.iloc[-1] / close.iloc[-20] - 1)
        lookback_60  = min(60, len(close) - 1)
        momentum_60d = float(close.iloc[-1] / close.iloc[-lookback_60] - 1)
        rsi          = _calc_rsi(close, period=14)
        vol          = df["Volume"].squeeze()
        vol_ratio    = float(vol.iloc[-5:].mean() / vol.iloc[-20:].mean())
        price        = float(close.iloc[-1])

        # RSIが30未満（売られすぎ）または70超（過熱）は減点
        rsi_penalty = max(0.0, rsi - 70) * 0.02 + max(0.0, 30 - rsi) * 0.02

        score = (
            momentum_20d * 2.0         # 短期モメンタム重視
            + momentum_60d * 1.0       # 中期モメンタム
            + (vol_ratio - 1.0) * 0.5  # 出来高増加
            - rsi_penalty
        )

        return {
            "ticker":       ticker,
            "score":        round(score, 4),
            "momentum_20d": round(momentum_20d * 100, 2),
            "momentum_60d": round(momentum_60d * 100, 2),
            "rsi":          round(rsi, 1),
            "vol_ratio":    round(vol_ratio, 2),
            "price":        round(price, 2),
        }

    except Exception as e:
        logger.error("[Screener] %s: スコア計算エラー: %s", ticker, e)
        return None


def _screen_universe(universe: list[str], top_n: int) -> list[dict]:
    """universeをスクリーニングし、スコア降順の上位top_n銘柄リストを返す。"""
    results = [r for t in universe if (r := _score_ticker(t)) is not None]
    results.sort(key=lambda x: x["score"], reverse=True)
    top = results[:top_n]
    logger.info("[Screener] スクリーニング完了: 上位%d銘柄 = %s",
                top_n, [r["ticker"] for r in top])
    return top


# ──────────────────────────────────────────────────────────────
# TASignal → アクション変換
# ──────────────────────────────────────────────────────────────

_ACTION_PRIORITY: dict[str, int] = {
    "BUY":        0,
    "OVERWEIGHT": 1,
    "HOLD":       2,
    "SELL":       3,
    "UNKNOWN":    4,
}


def _signal_to_action(signal) -> str:
    """TASignal のレーティングを統一アクション文字列に変換する。"""
    if signal is None:
        return "UNKNOWN"
    rating = str(signal.rating).strip().upper()
    if rating in ("BUY", "STRONG BUY"):
        return "BUY"
    if rating == "OVERWEIGHT":
        return "OVERWEIGHT"
    if rating == "HOLD":
        return "HOLD"
    if rating in ("SELL", "UNDERWEIGHT"):
        return "SELL"
    # direction フォールバック
    return signal.direction if signal.direction in ("BUY", "SELL") else "UNKNOWN"


# ──────────────────────────────────────────────────────────────
# メイン: 週次スクリーニング実行
# ──────────────────────────────────────────────────────────────

def run_weekly_screening(ta: "TAAnalyzer") -> str | None:
    """週次スクリーニングを実行し、今週の監視銘柄 (MT5シンボル名) を返す。

    副作用: config.SYMBOLS をメモリ上で更新する。
    失敗時は None を返し config.SYMBOLS は変更しない。

    Args:
        ta: main.py が保持する TAAnalyzer シングルトン (再利用)
    """
    analysis_date = date.today().strftime("%Y-%m-%d")
    universe      = config.WEEKLY_UNIVERSE
    top_n         = config.WEEKLY_SCREENER_TOP_N

    logger.info("[Weekly] ===== 週次スクリーニング開始 date=%s universe=%d銘柄 =====",
                analysis_date, len(universe))

    # Step 1: yfinance スクリーニング
    candidates = _screen_universe(universe, top_n)
    if not candidates:
        logger.error("[Weekly] スクリーニング候補0件 → 銘柄変更なし")
        return None

    # Step 2: TradingAgents 分析 (既存 TAAnalyzer を再利用)
    ta_results: list[dict] = []
    for cand in candidates:
        yf_ticker  = cand["ticker"]
        mt5_symbol = symbol_map.YF_TO_MT5.get(yf_ticker, yf_ticker)
        logger.info("[Weekly] TA分析: %s (%s)", mt5_symbol, yf_ticker)

        signal = ta.run_analysis(mt5_symbol, yf_ticker, analysis_date, "stock")
        action = _signal_to_action(signal)
        logger.info("[Weekly] %s → action=%s", mt5_symbol, action)

        ta_results.append({
            **cand,
            "mt5_symbol": mt5_symbol,
            "action":     action,
            "reasoning":  signal.reasoning[:300] if signal else "",
        })

    # Step 3: 最優先銘柄を選定
    ta_results.sort(key=lambda x: (_ACTION_PRIORITY.get(x["action"], 4), -x["score"]))
    selected   = ta_results[0]
    mt5_symbol = selected["mt5_symbol"]

    logger.info("=" * 55)
    logger.info("[Weekly] 今週の監視銘柄: %s", mt5_symbol)
    logger.info("         action=%s  score=%.4f  momentum_20d=%+.1f%%  RSI=%.1f",
                selected["action"], selected["score"],
                selected["momentum_20d"], selected["rsi"])
    logger.info("=" * 55)

    # Step 4: config.SYMBOLS をメモリ上で更新
    prev_symbols    = list(config.SYMBOLS)
    config.SYMBOLS  = [mt5_symbol]
    logger.info("[Weekly] config.SYMBOLS 更新: %s → %s", prev_symbols, config.SYMBOLS)

    # Step 4b: .env にも書き戻す (週中に再起動しても同じ銘柄を使い続けるため)
    _persist_symbol_to_env(mt5_symbol)

    # Step 5: 履歴ログ保存
    _save_history(selected, ta_results, analysis_date)

    # Step 6: Discord 通知
    discord_notifier.send_weekly_screening(selected, ta_results, analysis_date, prev_symbols)

    return mt5_symbol


# ──────────────────────────────────────────────────────────────
# 履歴ログ
# ──────────────────────────────────────────────────────────────

def _persist_symbol_to_env(mt5_symbol: str) -> None:
    """.env の SYMBOLS 行を選定銘柄に書き戻す。週中に再起動しても引き継がれるようにする。"""
    import re
    env_path = Path(config._CURRENT_DIR) / ".env"
    if not env_path.exists():
        logger.warning("[Weekly] .env が見つからないため SYMBOLS の永続化をスキップ")
        return
    try:
        with open(env_path, encoding="utf-8") as f:
            lines = f.readlines()
        new_lines = []
        updated = False
        for line in lines:
            if re.match(r"^SYMBOLS\s*=", line):
                new_lines.append(f"SYMBOLS={mt5_symbol}\n")
                updated = True
            else:
                new_lines.append(line)
        if not updated:
            new_lines.append(f"\nSYMBOLS={mt5_symbol}\n")
        with open(env_path, "w", encoding="utf-8") as f:
            f.writelines(new_lines)
        logger.info("[Weekly] .env に SYMBOLS=%s を書き戻しました", mt5_symbol)
    except OSError as e:
        logger.error("[Weekly] .env 書き込みエラー: %s", e)


def _save_history(selected: dict, all_results: list[dict], analysis_date: str) -> None:
    log_dir      = Path(config.LOG_DIR)
    history_file = log_dir / "weekly_history.json"

    history: list[dict] = []
    if history_file.exists():
        try:
            with open(history_file, encoding="utf-8") as f:
                history = json.load(f)
        except (json.JSONDecodeError, OSError):
            pass

    # 同週のレコードは上書き
    history = [h for h in history if h.get("week") != analysis_date]
    history.append({
        "week":       analysis_date,
        "selected":   selected,
        "candidates": all_results,
    })

    with open(history_file, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)

    logger.info("[Weekly] 履歴保存: %s", history_file)
