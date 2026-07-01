"""ロット計算・SL 計算・トレーリング SL 更新（v3.2：銘柄別 volume_min / volume_step）。

v3.2 はロングのみ。TP は一切計算しない。
ロット丸めは必ず銘柄別の volume_min / volume_step を使うこと。
"""
from __future__ import annotations

import math

from logger import get_logger

log = get_logger("risk_manager")


def _round_lot(raw_lot: float, volume_min: float, volume_step: float) -> float:
    """volume_step で floor 丸め。volume_min 未満は 0（発注しない）。"""
    if raw_lot <= 0:
        return 0.0
    steps = math.floor(raw_lot / volume_step)
    lot = round(steps * volume_step, 8)
    if lot < volume_min:
        return 0.0
    return lot


def update_trailing_sl(position_info, current_price: float,
                       atr_value: float, atr_multiplier: float = 2.0):
    """トレーリング SL の新価格を返す。有利方向のみ更新値を返す。

    BUY: new_sl = current_price - (atr × multiplier)
         new_sl > current_sl → new_sl を返す。それ以外 → None。
    SELL: v3.2 は非対応のため ValueError。
    """
    if position_info.direction != "BUY":
        raise ValueError(f"v3.2 supports BUY only. Got: {position_info.direction}")
    new_sl = current_price - (atr_value * atr_multiplier)
    return new_sl if new_sl > position_info.current_sl else None


def calculate_initial_lot(entry_price: float, sl_price: float,
                          account_balance: float, lot_value_per_unit: float,
                          volume_min: float, volume_step: float,
                          max_risk_pct: float = 2.5) -> float:
    """新規エントリーのロット数を計算する。

    許容損失額 = account_balance × max_risk_pct / 100
    価格リスク = abs(entry_price - sl_price)
    raw_lot   = 許容損失額 / (価格リスク × lot_value_per_unit)
    lot       = floor(raw_lot / volume_step) × volume_step、volume_min 未満は 0。

    lot_value_per_unit: 1.0 ロットあたり価格 1 単位の口座通貨価値
                        （米株CFD: contract_size × USDJPY レート）。
    TP は計算しない（v3.2 は TP なし）。
    """
    price_risk = abs(entry_price - sl_price)
    if price_risk <= 0 or lot_value_per_unit <= 0 or account_balance <= 0:
        log.warning("Invalid inputs for lot calc (price_risk=%.4f).", price_risk)
        return 0.0
    allowed_loss = account_balance * max_risk_pct / 100.0
    raw_lot = allowed_loss / (price_risk * lot_value_per_unit)
    return _round_lot(raw_lot, volume_min, volume_step)


def calculate_scale_in_lot(previous_added_lot: float, scale_in_ratio: float,
                           volume_min: float, volume_step: float) -> float:
    """買い増しロット = 前回追加ロット × scale_in_ratio（逓減）。銘柄別丸め。"""
    raw_lot = previous_added_lot * scale_in_ratio
    return _round_lot(raw_lot, volume_min, volume_step)


def can_scale_in(position_info, scaling_cfg: dict,
                 projected_total_risk_pct: float | None = None,
                 max_total_risk_pct: float | None = None) -> tuple[bool, str]:
    """買い増し可否を判定する。拒否条件（含み益なし / 2.5倍上限 / 合計リスク超過）。"""
    if not scaling_cfg.get("allow_scale_in", True):
        return False, "scale-in disabled"

    if scaling_cfg.get("require_unrealized_profit", True) and position_info.unrealized_pnl <= 0:
        return False, "no unrealized profit"

    max_mult = scaling_cfg.get("max_total_multiplier", 2.5)
    if position_info.total_lots >= position_info.initial_lots * max_mult:
        return False, f"max multiplier {max_mult}x reached"

    if (
        projected_total_risk_pct is not None
        and max_total_risk_pct is not None
        and projected_total_risk_pct > max_total_risk_pct
    ):
        return False, (
            f"total risk {projected_total_risk_pct:.1f}% > limit {max_total_risk_pct:.1f}%"
        )

    return True, "OK"


def recalculate_sl_after_scale(new_avg_entry: float, current_sl: float,
                               atr_value: float, atr_multiplier: float = 2.0) -> float | None:
    """買い増し後 SL を平均取得単価ベースで再計算する。有利方向のみ返す。"""
    new_sl = new_avg_entry - (atr_value * atr_multiplier)
    return new_sl if new_sl > current_sl else None
