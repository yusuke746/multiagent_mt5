"""資金管理 & ロット計算モジュール (ATRベースSL専用版)

SL距離は呼び出し元で ATR × 倍率 として計算して渡す。
"""
import math
import logging

import config
import mt5_connector

logger = logging.getLogger(__name__)


def calculate_lot(symbol: str, sl_distance: float) -> float | None:
    """ATRベースのSL幅からリスク管理ロット数を算出する。

    Args:
        symbol:      MT5シンボル名
        sl_distance: SLまでの価格幅 (正の値)

    Returns:
        ロット数 (float) または None (計算不能時)
    """
    if sl_distance <= 0:
        logger.error("SL幅が0以下: %s sl_distance=%s", symbol, sl_distance)
        return None

    account = mt5_connector.get_account_info()
    if account is None:
        logger.error("アカウント情報取得失敗")
        return None

    balance          = account["balance"]
    margin_free      = account["margin_free"]
    account_currency = account["currency"]

    if balance <= 0:
        logger.error("残高が0以下: %.0f", balance)
        return None

    sym_info = mt5_connector.get_symbol_info(symbol)
    if sym_info is None:
        logger.error("シンボル情報取得失敗: %s", symbol)
        return None

    contract_size    = sym_info["trade_contract_size"]
    profit_currency  = sym_info["currency_profit"]
    vol_min          = sym_info["volume_min"]
    vol_max          = sym_info["volume_max"]
    vol_step         = sym_info["volume_step"]

    # 許容損失額 (口座通貨)
    max_loss_account = balance * config.RISK_PER_TRADE

    # 口座通貨 → profit_currency 換算
    if account_currency == profit_currency:
        max_loss_profit = max_loss_account
    else:
        # rate = account_currency で1単位買うと profit_currency がいくらか
        # 例: JPY→USD なら rate≈0.0065 → max_loss_profit = max_loss_account * rate
        rate = mt5_connector._get_conversion_rate(account_currency, profit_currency)
        if rate is None or rate <= 0:
            logger.error("通貨換算レート取得失敗: %s → %s", account_currency, profit_currency)
            return None
        max_loss_profit = max_loss_account * rate

    # ロット計算: 1lot損失 = SL幅 × contract_size
    loss_per_lot = sl_distance * contract_size
    if loss_per_lot <= 0:
        return None

    raw_lot = max_loss_profit / loss_per_lot

    # MT5制約に合わせたクランプ
    lot = _round_lot(raw_lot, vol_step)
    lot = max(lot, vol_min)
    lot = min(lot, vol_max)
    lot = min(lot, config.MAX_LOT)

    # 証拠金チェック
    required_margin = _estimate_required_margin(symbol, lot, sym_info)
    if required_margin is not None and required_margin > margin_free * 0.8:
        logger.warning("[LotCalc] 証拠金不足 (見込み%s > 空き証拠金%.0f×80%%): lot縮小",
                       required_margin, margin_free)
        lot = max(vol_min, lot * 0.5)
        lot = _round_lot(lot, vol_step)

    logger.info("[LotCalc] %s: sl_dist=%.5f lot=%.2f (balance=%.0f risk=%.1f%%)",
                symbol, sl_distance, lot, balance, config.RISK_PER_TRADE * 100)
    return lot


def _round_lot(raw: float, step: float) -> float:
    if step <= 0:
        return round(raw, 2)
    precision = max(0, round(-math.log10(step)))
    return round(math.floor(raw / step) * step, precision)


def _estimate_required_margin(symbol: str, lot: float, sym_info: dict) -> float | None:
    """概算証拠金を返す。計算不能時は None。"""
    try:
        tick = mt5_connector.get_current_price(symbol)
        if tick is None:
            return None
        price = (tick["bid"] + tick["ask"]) / 2
        contract = sym_info["trade_contract_size"]
        return price * contract * lot / 100  # 簡易見積り
    except Exception:
        return None
