"""口座レベルのサーキットブレーカー（§5 優先度 1〜3）。

- 週次損失リミット超過 → 即時全処理停止（週次リセットまで）
- 日次損失リミット超過 → 当日の新規発注を全停止（既存 SL 更新は継続）
- 証拠金維持率不足     → 新規発注を全停止（既存 SL 更新は継続）
"""
from __future__ import annotations

from dataclasses import dataclass

from core import config_loader as cfg
from logger import get_logger

log = get_logger("account_guard")


@dataclass
class GuardResult:
    halt_all: bool          # True: SL 更新含め全処理を停止
    allow_new_orders: bool  # True: 新規/買い増し発注を許可
    reasons: list[str]


def check(portfolio_state) -> GuardResult:
    risk = cfg.load_risk()["circuit_breaker"]
    balance = portfolio_state.account_balance
    daily_limit = balance * risk["daily_loss_limit_pct"] / 100.0
    weekly_limit = balance * risk["weekly_loss_limit_pct"] / 100.0
    min_margin = risk["min_margin_level_pct"]

    reasons: list[str] = []
    halt_all = False
    allow_new_orders = True

    # 優先度1: 週次損失リミット → 全停止
    if portfolio_state.weekly_pnl < -weekly_limit:
        halt_all = True
        allow_new_orders = False
        reasons.append(
            f"weekly loss limit hit: ¥{portfolio_state.weekly_pnl:+.0f} "
            f"< -¥{weekly_limit:.0f} ({risk['weekly_loss_limit_pct']}%)"
        )

    # 優先度2: 日次損失リミット → 新規停止
    if portfolio_state.daily_pnl < -daily_limit:
        allow_new_orders = False
        reasons.append(
            f"daily loss limit hit: ¥{portfolio_state.daily_pnl:+.0f} "
            f"< -¥{daily_limit:.0f} ({risk['daily_loss_limit_pct']}%)"
        )

    # 優先度3: 証拠金維持率 → 新規停止
    if portfolio_state.margin_level_pct < min_margin:
        allow_new_orders = False
        reasons.append(
            f"margin level {portfolio_state.margin_level_pct:.0f}% < {min_margin}%"
        )

    if halt_all:
        log.error("HALT ALL. %s", "; ".join(reasons))
    elif not allow_new_orders:
        log.warning("New orders blocked. %s", "; ".join(reasons))
    else:
        log.info("Limits OK. Trading allowed.")

    return GuardResult(halt_all=halt_all, allow_new_orders=allow_new_orders, reasons=reasons)
