"""MT5 からの価格・ATR・スプレッド・出来高データ取得。

日足（D1）ベースで ATR(14) を算出する（SL 計算・トレーリングに使用）。
Phase 0 で D1×20 本の取得可能を確認済み。
"""
from __future__ import annotations

from dataclasses import dataclass

import MetaTrader5 as mt5

from core import mt5_client
from logger import get_logger

log = get_logger("market_data")


@dataclass
class MarketSnapshot:
    mt5_symbol: str
    bid: float
    ask: float
    spread: float          # ask - bid（価格単位）
    atr: float             # ATR(period)（価格単位）
    last_change_pct: float  # 直近1日の変動率（%）
    avg_volume_5d: float   # 直近5日平均出来高（tick_volume）
    prev_avg_volume: float  # それ以前の平均出来高（比較用）


def _rates(mt5_symbol: str, count: int):
    if not mt5_client.ensure_symbol(mt5_symbol):
        return None
    rates = mt5.copy_rates_from_pos(mt5_symbol, mt5.TIMEFRAME_D1, 0, count)
    if rates is None or len(rates) == 0:
        log.warning("No rates for %s: %s", mt5_symbol, mt5.last_error())
        return None
    return rates


def get_atr(mt5_symbol: str, period: int = 14) -> float | None:
    """D1 True Range の単純移動平均で ATR を算出する。"""
    rates = _rates(mt5_symbol, period + 1)
    if rates is None or len(rates) < period + 1:
        return None
    trs = []
    for i in range(1, len(rates)):
        high = rates[i]["high"]
        low = rates[i]["low"]
        prev_close = rates[i - 1]["close"]
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        trs.append(tr)
    if len(trs) < period:
        return None
    return sum(trs[-period:]) / period


def get_tick(mt5_symbol: str):
    if not mt5_client.ensure_symbol(mt5_symbol):
        return None
    tick = mt5.symbol_info_tick(mt5_symbol)
    if tick is None:
        log.warning("No tick for %s: %s", mt5_symbol, mt5.last_error())
    return tick


def get_ask(mt5_symbol: str) -> float | None:
    tick = get_tick(mt5_symbol)
    return float(tick.ask) if tick else None


def get_bid(mt5_symbol: str) -> float | None:
    tick = get_tick(mt5_symbol)
    return float(tick.bid) if tick else None


def get_snapshot(mt5_symbol: str, atr_period: int = 14) -> MarketSnapshot | None:
    """スクリーナー・SL 計算が必要とする指標をまとめて返す。"""
    tick = get_tick(mt5_symbol)
    if tick is None:
        return None
    atr = get_atr(mt5_symbol, atr_period)
    if atr is None:
        return None

    rates = _rates(mt5_symbol, atr_period + 6)
    if rates is None or len(rates) < 7:
        return None

    last = rates[-1]
    prev = rates[-2]
    last_change_pct = (
        abs(last["close"] - prev["close"]) / prev["close"] * 100.0
        if prev["close"]
        else 0.0
    )
    vols = [float(r["tick_volume"]) for r in rates]
    avg_volume_5d = sum(vols[-5:]) / 5.0
    prev_slice = vols[:-5] or vols
    prev_avg_volume = sum(prev_slice) / len(prev_slice)

    return MarketSnapshot(
        mt5_symbol=mt5_symbol,
        bid=float(tick.bid),
        ask=float(tick.ask),
        spread=float(tick.ask - tick.bid),
        atr=float(atr),
        last_change_pct=last_change_pct,
        avg_volume_5d=avg_volume_5d,
        prev_avg_volume=prev_avg_volume,
    )
