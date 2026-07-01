"""ポートフォリオ文脈の注入パッチ（§0.5.2・A'案）。

TradingAgentsGraph.resolve_instrument_context をラップし、生成される
instrument_context の末尾へポートフォリオ文脈を追記する。Trader・PM 両プロンプトへ
公式チャネル（state["instrument_context"]）経由で届く。

警告: agent_utils.build_instrument_context だけを差し替えても、
trading_graph.py がモジュール読込時に名前束縛するため実注入に効かない。
必ず resolve_instrument_context（＝_run_graph が呼ぶメソッド）をラップすること。
"""
from __future__ import annotations

import functools
from typing import Callable

from logger import get_logger

log = get_logger("ta_patch")

_INSTALLED = False


def install_portfolio_injection(get_ticker_context: Callable[[str], str]) -> None:
    """resolve_instrument_context をラップする。プロセス起動時に一度だけ呼ぶ。

    get_ticker_context(ticker: str) -> str : 当該銘柄のポートフォリオ文脈を返す関数
    """
    global _INSTALLED
    if _INSTALLED:
        log.info("Portfolio injection already installed. Skip.")
        return

    from tradingagents.graph.trading_graph import TradingAgentsGraph

    _orig = TradingAgentsGraph.resolve_instrument_context

    @functools.wraps(_orig)
    def wrapped(self, ticker, asset_type="stock"):
        base = _orig(self, ticker, asset_type)
        try:
            extra = get_ticker_context(ticker) or ""
        except Exception as e:  # 注入失敗で分析全体を止めない
            log.warning("get_ticker_context failed for %s: %s", ticker, e)
            extra = ""
        return f"{base}\n\n{extra}" if extra else base

    TradingAgentsGraph.resolve_instrument_context = wrapped
    _INSTALLED = True
    log.info("Portfolio injection installed (resolve_instrument_context wrapped).")
