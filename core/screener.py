"""一次スクリーニング（軽量・§4.2）。

除外条件1（コスト異常）: spread / ATR(14) > 25%
  ⇒ XM 米株CFD の実測分布（39サンプル）から統計的に決定。
    中央値 20%, Q3 25% であり、上位 10-15% の外れ値を除外しつつ、
    AI分析価値のある 87% の銘柄を通す（Q1〜Q3はほぼ全通過）。
除外条件2（流動性異常）: 直近5日平均出来高 < 通常の50%
除外条件3（動きなし）  : 直近1日の価格変動 < ±0.5% → HOLD 維持

保有中の銘柄は対象外（必ず分析する）。
"""
from __future__ import annotations

from core import config_loader as cfg
from core import market_data
from logger import get_logger

log = get_logger("screener")

_SPREAD_ATR_MAX = 0.25     # 25% (XM CFD 実測統計ベース)
_VOLUME_MIN_RATIO = 0.50   # 通常の50%
_NO_MOVE_PCT = 0.5         # ±0.5%


def run(tickers: list[str], portfolio_state) -> tuple[list[str], dict[str, str]]:
    """分析すべき ticker のリストと、除外した ticker→理由 の dict を返す。"""
    to_analyze: list[str] = []
    skipped: dict[str, str] = {}

    for ticker in tickers:
        spec = cfg.spec_for_ticker(ticker)
        if spec is None:
            skipped[ticker] = "unknown ticker"
            continue

        # 保有中は必ず分析（スクリーニング対象外）
        if portfolio_state.get_position(ticker) is not None:
            to_analyze.append(ticker)
            continue

        snap = market_data.get_snapshot(spec.mt5_symbol)
        if snap is None:
            skipped[ticker] = "no market data"
            log.warning("%s: no market data, screened out", ticker)
            continue

        # 条件1: spread / ATR
        if snap.atr > 0 and (snap.spread / snap.atr) > _SPREAD_ATR_MAX:
            ratio = snap.spread / snap.atr * 100
            skipped[ticker] = f"spread/ATR {ratio:.1f}%"
            log.info("%s: screened out (spread/ATR: %.1f%%)", spec.mt5_symbol, ratio)
            continue

        # 条件2: 流動性
        if snap.prev_avg_volume > 0 and (snap.avg_volume_5d / snap.prev_avg_volume) < _VOLUME_MIN_RATIO:
            ratio = snap.avg_volume_5d / snap.prev_avg_volume * 100
            skipped[ticker] = f"low volume {ratio:.0f}%"
            log.info("%s: screened out (volume: %.0f%% of normal)", spec.mt5_symbol, ratio)
            continue

        # 条件3: 動きなし → HOLD 維持（分析しない）
        if snap.last_change_pct < _NO_MOVE_PCT:
            skipped[ticker] = f"no movement {snap.last_change_pct:.2f}%"
            log.info("%s: no movement (%.2f%%), HOLD", spec.mt5_symbol, snap.last_change_pct)
            continue

        to_analyze.append(ticker)

    log.info("Screener: %d to analyze, %d skipped.", len(to_analyze), len(skipped))
    return to_analyze, skipped
