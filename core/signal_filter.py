"""シグナルフィルタ（v3.2：レーティング階層ゲート＋フリップフロップ防止）。

重要: signal_history.json（本フィルタ用）と TradingAgents 本体の
TradingMemoryLog（LLM 文脈用）は別物。本フィルタは signal_history.json のみ参照する（§6）。
"""
from __future__ import annotations

from logger import get_logger

log = get_logger("signal_filter")


def apply(ticker: str, decision: dict, signal_history: dict, gate_cfg: dict) -> dict:
    """シグナルにフィルタを適用する。引っかかれば HOLD に変換して返す。

    signal_history: {ticker: [最新の decision, ...]} 新しい順
    """
    consecutive = gate_cfg.get("direction_change_consecutive_required", 2)
    action = decision["action"]
    rating = decision.get("rating", "Hold")

    # ルール1: レーティング階層ゲート（旧 confidence フィルタの置換）
    if action == "BUY" and rating not in gate_cfg["new_entry_ratings"]:
        log.info("%s: rating %s not allowed for new entry → HOLD", ticker, rating)
        return {**decision, "action": "HOLD",
                "filter_reason": f"rating {rating} not allowed for new entry"}
    if action == "INCREASE" and rating not in gate_cfg["scale_in_ratings"]:
        log.info("%s: rating %s not allowed for scale-in → HOLD", ticker, rating)
        return {**decision, "action": "HOLD",
                "filter_reason": f"rating {rating} not allowed for scale-in"}

    # ルール2: フリップフロップ防止
    history = signal_history.get(ticker, [])
    if not history:
        return decision

    buy_side = {"BUY", "INCREASE"}
    exit_side = {"DECREASE", "CLOSE"}
    last_action = history[0]["action"]
    cur_buy = action in buy_side
    cur_exit = action in exit_side
    last_buy = last_action in buy_side
    last_exit = last_action in exit_side

    is_change = (cur_buy and last_exit) or (cur_exit and last_buy)
    if is_change:
        needed = consecutive - 1
        same = sum(
            1 for h in history[:needed]
            if (cur_buy and h["action"] in buy_side)
            or (cur_exit and h["action"] in exit_side)
        )
        if same < needed:
            log.warning(
                "%s: direction change filtered. Need %d consecutive.",
                ticker, consecutive,
            )
            return {**decision, "action": "HOLD",
                    "filter_reason": f"direction change needs {consecutive} consecutive signals"}

    return decision
