"""相関リスク制御モジュール"""

import logging
import config
import mt5_connector

logger = logging.getLogger(__name__)


def can_open_position(symbol: str) -> tuple[bool, str]:
    """新規ポジションを建ててよいかチェックする。"""
    groups = config.CURRENCY_GROUPS.get(symbol) or config.CURRENCY_GROUPS.get(symbol.rstrip("#."), [])
    if not groups:
        logger.warning("CURRENCY_GROUPS未定義: %s → チェックスキップ", symbol)
        return True, ""

    open_symbols = mt5_connector.get_all_open_symbols()

    if symbol in open_symbols:
        return False, f"{symbol} は既にポジション保有中"

    for currency in groups:
        count = sum(
            1 for sym in open_symbols
            if currency in (
                config.CURRENCY_GROUPS.get(sym)
                or config.CURRENCY_GROUPS.get(sym.rstrip("#."), [])
            )
        )
        if count >= config.MAX_CORRELATED_POSITIONS:
            return False, (
                f"{currency}グループ: 既に{count}ポジション保有中 "
                f"(上限{config.MAX_CORRELATED_POSITIONS})"
            )

    return True, ""
