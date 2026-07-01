"""スケジューラ（1サイクルのオーケストレーション）。

データフロー（§1.2）の順序を厳守する。順序を変えるとリスク管理が機能しない。
[0] 注入パッチ適用 → [1] account_guard → [2] sync → [3] トレーリングSL →
[4] screener → [5] 並列分析 → [6] parse → [7] filter → [8] 発注 → [9] ログ/通知
"""
from __future__ import annotations

import asyncio
from datetime import datetime

import MetaTrader5 as mt5

import notifier
from core import account_guard
from core import config_loader as cfg
from core import market_clock
from core import market_data
from core import mt5_client
from core import order_executor
from core import persistence
from core import portfolio_state as ps_mod
from core import risk_manager
from core import screener
from core import signal_filter
from core import tradingagents_runner as runner
from core.tradingagents_patch import install_portfolio_injection
from logger import get_logger, setup_logging

log = get_logger("scheduler")

_HISTORY_KEEP = 10


def _lot_value_per_unit(mt5_symbol: str, price: float) -> float | None:
    """1.0 ロットあたり価格 1 単位（$1）の口座通貨(JPY)価値。

    XM 米株CFD の symbol_info.trade_tick_value は【利益通貨=USD 建て】で返るため、
    tick_value/tick_size をそのまま使うと USD/JPY 分（約160倍）過大なロットになる。
    通貨変換を含む order_calc_profit を権威として使う（$1 下落時の JPY 損失の絶対値）。
    """
    p = mt5.order_calc_profit(mt5.ORDER_TYPE_BUY, mt5_symbol, 1.0, price, price - 1.0)
    if p is None or p == 0:
        return None
    return abs(p)


def _record_history(history: dict, ticker: str, decision: dict, executed: bool) -> None:
    entry = {
        "date": datetime.now().strftime("%Y-%m-%d"),
        "action": decision["action"],
        "rating": decision.get("rating"),
        "converted_from": decision.get("converted_from"),
        "rationale": decision.get("rationale", ""),
        "filter_applied": decision.get("filter_reason") is not None,
        "filter_reason": decision.get("filter_reason"),
        "executed": executed,
    }
    history.setdefault(ticker, []).insert(0, entry)
    history[ticker] = history[ticker][:_HISTORY_KEEP]


# --- [3] トレーリング SL 更新 ---------------------------------------------

def _update_trailing_sls(state, atr_period: int, atr_mult: float, summary: list[str]) -> None:
    for pos in state.positions:
        atr = market_data.get_atr(pos.mt5_symbol, atr_period)
        price = market_data.get_bid(pos.mt5_symbol)
        if atr is None or price is None:
            log.warning("%s: skip trailing (no atr/price)", pos.mt5_symbol)
            continue
        for t in pos.tickets:
            # チケット個別の SL を基準に有利方向のみ更新
            class _P:
                direction = "BUY"
                current_sl = t.sl
            new_sl = risk_manager.update_trailing_sl(_P(), price, atr, atr_mult)
            if new_sl is None:
                log.info("%s: trailing SL not updated (new_sl <= current_sl)", pos.mt5_symbol)
                continue
            res = order_executor.update_sl_only(pos.mt5_symbol, t.ticket, new_sl)
            if res["success"]:
                summary.append(f"🔧 {pos.ticker}: SL → ${new_sl:.2f}")


# --- [8] アクション実行 ----------------------------------------------------

def _execute_buy(ticker, spec, state, risk, history, summary) -> None:
    atr = market_data.get_atr(spec.mt5_symbol, risk["stop_loss"]["atr_period"])
    ask = market_data.get_ask(spec.mt5_symbol)
    lot_value = _lot_value_per_unit(spec.mt5_symbol, ask) if ask is not None else None
    if atr is None or ask is None or lot_value is None:
        log.warning("%s: BUY skipped (missing atr/price/lot_value)", ticker)
        return
    sl = ask - atr * risk["stop_loss"]["atr_multiplier"]
    lot = risk_manager.calculate_initial_lot(
        entry_price=ask, sl_price=sl,
        account_balance=state.account_balance, lot_value_per_unit=lot_value,
        volume_min=spec.volume_min, volume_step=spec.volume_step,
        max_risk_pct=risk["portfolio"]["max_risk_per_trade_pct"],
    )
    if lot <= 0:
        log.info("%s: BUY skipped (lot rounded to 0)", ticker)
        return

    order_params = {
        "action": "BUY", "mt5_symbol": spec.mt5_symbol, "ticker": ticker,
        "lot_size": lot, "sl": sl, "tp": 0.0,
    }
    ok, reason = order_executor.validate_order(order_params, state)
    if not ok:
        log.warning("%s: BUY rejected by validate_order - %s", ticker, reason)
        return

    res = order_executor.open_position(spec.mt5_symbol, lot, sl)
    if res["success"]:
        meta = persistence.load_position_meta()
        meta[spec.mt5_symbol] = {
            "ticker": ticker, "initial_lots": lot, "scale_count": 0,
            "entry_date": datetime.now().strftime("%Y-%m-%d"), "scale_history": [],
        }
        persistence.save_position_meta(meta)
        _record_history(history, ticker, {"action": "BUY", "rating": "Buy"}, True)
        summary.append(f"🟢 {ticker}: BUY {lot} lot @ ${res['price']:.2f} SL=${sl:.2f}")


def _execute_increase(ticker, spec, pos, state, risk, history, summary) -> None:
    ok, reason = risk_manager.can_scale_in(pos, risk["scaling"])
    if not ok:
        log.warning("%s: scale-in rejected. %s", ticker, reason)
        return

    meta = persistence.load_position_meta()
    m = meta.get(spec.mt5_symbol, {})
    scale_history = m.get("scale_history", [])
    previous_added = scale_history[-1]["lots"] if scale_history else pos.initial_lots
    lot = risk_manager.calculate_scale_in_lot(
        previous_added, risk["scaling"]["scale_in_ratio"], spec.volume_min, spec.volume_step
    )
    if lot <= 0:
        log.info("%s: INCREASE skipped (lot rounded to 0)", ticker)
        return

    ask = market_data.get_ask(spec.mt5_symbol)
    atr = market_data.get_atr(spec.mt5_symbol, risk["stop_loss"]["atr_period"])
    if ask is None or atr is None:
        return
    new_sl_candidate = ask - atr * risk["stop_loss"]["atr_multiplier"]
    order_params = {
        "action": "INCREASE", "mt5_symbol": spec.mt5_symbol, "ticker": ticker,
        "lot_size": lot, "sl": new_sl_candidate, "tp": 0.0,
    }
    ok, reason = order_executor.validate_order(order_params, state)
    if not ok:
        log.warning("%s: INCREASE rejected by validate_order - %s", ticker, reason)
        return

    res = order_executor.add_position(spec.mt5_symbol, lot, new_sl_candidate)
    if not res["success"]:
        return

    # 平均取得単価と SL を再計算
    new_total = pos.total_lots + lot
    new_avg = (pos.avg_entry_price * pos.total_lots + res["price"] * lot) / new_total
    new_sl = risk_manager.recalculate_sl_after_scale(
        new_avg, pos.current_sl, atr, risk["stop_loss"]["atr_multiplier"]
    )
    if risk["scaling"].get("recalculate_sl_on_scale", True) and new_sl is not None:
        for t in pos.tickets:
            order_executor.update_sl_only(spec.mt5_symbol, t.ticket, new_sl)

    scale_history.append({"date": datetime.now().strftime("%Y-%m-%d"), "lots": lot, "price": res["price"]})
    meta[spec.mt5_symbol] = {
        "ticker": ticker,
        "initial_lots": m.get("initial_lots", pos.initial_lots),
        "scale_count": m.get("scale_count", pos.scale_count) + 1,
        "entry_date": m.get("entry_date", pos.entry_date),
        "scale_history": scale_history,
    }
    persistence.save_position_meta(meta)
    _record_history(history, ticker, {"action": "INCREASE", "rating": "Overweight"}, True)
    summary.append(f"➕ {ticker}: INCREASE +{lot} lot @ ${res['price']:.2f}")


def _execute_close(ticker, spec, pos, history, summary, partial: bool) -> None:
    if partial:
        results = order_executor.partial_close(pos, 0.5)
        label, emoji = "DECREASE 50%", "🟠"
    else:
        results = order_executor.close_position(pos)
        label, emoji = "CLOSE", "🔴"

    if any(r["success"] for r in results):
        if not partial:
            meta = persistence.load_position_meta()
            meta.pop(spec.mt5_symbol, None)
            persistence.save_position_meta(meta)
        action = "DECREASE" if partial else "CLOSE"
        _record_history(history, ticker, {"action": action, "rating": "Underweight" if partial else "Sell"}, True)
        summary.append(f"{emoji} {ticker}: {label}")


# --- サイクル本体 ----------------------------------------------------------

def run_cycle() -> None:
    setup_logging()
    log.info("===== Cycle start %s =====", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    summary: list[str] = []
    risk = cfg.load_risk()

    # [0] 注入パッチ（プロセス内で一度だけ）
    try:
        install_portfolio_injection(runner.get_ticker_context)
    except Exception as e:
        log.error("Injection install failed: %s. Aborting cycle.", e)
        notifier.send("起動失敗", f"注入パッチ適用失敗: {e}", "ERROR")
        return

    # MT5 接続
    try:
        mt5_client.connect()
    except Exception as e:
        log.error("MT5 connect failed: %s", e)
        notifier.send("起動失敗", f"MT5接続失敗: {e}", "ERROR")
        return

    try:
        # [2] ポートフォリオ同期（先に取得して残高等を得る）
        try:
            state = ps_mod.sync_from_mt5()
        except Exception as e:
            log.error("sync_from_mt5 failed: %s. Skip cycle.", e)
            notifier.send("同期失敗", f"ポジション同期失敗のため当サイクルをスキップ: {e}", "ERROR")
            return

        # [1] サーキットブレーカー
        guard = account_guard.check(state)
        if guard.halt_all:
            notifier.send("取引全停止", "; ".join(guard.reasons), "ERROR")
            return

        # [3] トレーリング SL 更新（分析より必ず先）
        _update_trailing_sls(
            state, risk["stop_loss"]["atr_period"], risk["stop_loss"]["atr_multiplier"], summary
        )

        # [4] スクリーニング
        to_analyze, skipped = screener.run(cfg.all_tickers(), state)
        if not to_analyze:
            log.info("No tickers to analyze this cycle.")
            notifier.send_cycle_summary(summary or ["分析対象なし"], "INFO")
            return

        # [5][6] 並列分析 → parse_decision（runner 内で実施）
        # trade_date は実行時刻ではなく「直近に確定した米国取引日」を使う（夏冬時間を自動対応）。
        date_str = market_clock.last_completed_us_trading_day()
        log.info("Analysis trade_date (last completed US session): %s", date_str)
        max_parallel = risk["execution"]["max_parallel_analysis"]
        results = asyncio.run(runner.run_parallel(to_analyze, date_str, state, max_parallel))

        # [7][8] フィルタ → アクション実行
        history = persistence.load_signal_history()
        gate_cfg = risk["signal_gate"]

        for r in results:
            ticker = r["ticker"]
            decision = signal_filter.apply(ticker, r["decision"], history, gate_cfg)
            action = decision["action"]
            spec = cfg.spec_for_ticker(ticker)
            pos = state.get_position(ticker)

            if action == "HOLD":
                _record_history(history, ticker, decision, False)
                continue

            # 新規/買い増しは new_orders 許可時のみ。決済系は常に許可。
            if action in ("BUY", "INCREASE") and not guard.allow_new_orders:
                log.warning("%s: %s blocked (new orders disabled).", ticker, action)
                _record_history(history, ticker, decision, False)
                continue

            if action == "BUY":
                _execute_buy(ticker, spec, state, risk, history, summary)
            elif action == "INCREASE" and pos is not None:
                _execute_increase(ticker, spec, pos, state, risk, history, summary)
            elif action == "DECREASE" and pos is not None:
                _execute_close(ticker, spec, pos, history, summary, partial=True)
            elif action == "CLOSE" and pos is not None:
                _execute_close(ticker, spec, pos, history, summary, partial=False)

        persistence.save_signal_history(history)

        # [9] 通知
        notifier.send_cycle_summary(summary or ["アクションなし（全HOLD）"], "INFO")
        log.info("===== Cycle end =====")

    finally:
        mt5_client.shutdown()


if __name__ == "__main__":
    run_cycle()
