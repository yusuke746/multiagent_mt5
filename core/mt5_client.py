"""MT5 接続管理。

initialize → login を行い、以降のモジュールは接続済み前提で mt5 API を呼ぶ。
ヘッジング口座（margin_mode=2）のため、決済・SLTP はチケット単位で操作する
（各モジュール側の責務）。ここでは接続ライフサイクルのみを扱う。
"""
from __future__ import annotations

import MetaTrader5 as mt5

from core import config_loader as cfg
from logger import get_logger

log = get_logger("mt5_client")


class MT5ConnectionError(RuntimeError):
    pass


def connect() -> None:
    """MT5 端末を初期化しログインする。失敗時は MT5ConnectionError。"""
    creds = cfg.mt5_credentials()
    path = creds["path"]

    init_ok = (
        mt5.initialize(
            path,
            login=creds["login"],
            password=creds["password"],
            server=creds["server"],
        )
        if path
        else mt5.initialize(
            login=creds["login"],
            password=creds["password"],
            server=creds["server"],
        )
    )
    if not init_ok:
        raise MT5ConnectionError(f"mt5.initialize failed: {mt5.last_error()}")

    info = mt5.account_info()
    if info is None:
        mt5.shutdown()
        raise MT5ConnectionError(f"account_info() returned None: {mt5.last_error()}")

    log.info(
        "Connected. login=%s server=%s currency=%s balance=%.0f margin_mode=%s",
        info.login, info.server, info.currency, info.balance, info.margin_mode,
    )


def shutdown() -> None:
    mt5.shutdown()
    log.info("MT5 connection closed.")


def health_check() -> bool:
    """接続が生きているか（account_info が取れるか）を確認する。"""
    return mt5.account_info() is not None


def ensure_symbol(mt5_symbol: str) -> bool:
    """シンボルを気配板へ追加し可視化する。失敗時 False。"""
    info = mt5.symbol_info(mt5_symbol)
    if info is None:
        log.error("Symbol not found: %s", mt5_symbol)
        return False
    if not info.visible and not mt5.symbol_select(mt5_symbol, True):
        log.error("symbol_select failed: %s (%s)", mt5_symbol, mt5.last_error())
        return False
    return True
