"""MT5 接続・データ取得・注文管理モジュール (SMC機能なし版)"""

import time
import logging
from datetime import datetime, timezone

import MetaTrader5 as mt5
import numpy as np
import pandas as pd

import config
import discord_notifier

logger = logging.getLogger(__name__)

TF_MAP = {
    "M1":  mt5.TIMEFRAME_M1,
    "M5":  mt5.TIMEFRAME_M5,
    "M15": mt5.TIMEFRAME_M15,
    "M30": mt5.TIMEFRAME_M30,
    "H1":  mt5.TIMEFRAME_H1,
    "H4":  mt5.TIMEFRAME_H4,
    "D1":  mt5.TIMEFRAME_D1,
}


def _symbol_candidates(base_symbol: str) -> list[str]:
    """ブローカー依存のサフィックス (#, .) を吸収した候補を返す。"""
    base = (base_symbol or "").strip()
    if not base:
        return []
    candidates: list[str] = [base]
    stripped = base.rstrip("#.")
    if stripped and stripped not in candidates:
        candidates.append(stripped)
    for suffix in ("#", "."):
        sym = stripped + suffix
        if stripped and sym not in candidates:
            candidates.append(sym)
    for watched in config.SYMBOLS:
        if watched.rstrip("#.") == stripped and watched not in candidates:
            candidates.append(watched)
    return candidates


def _get_tick_with_fallback(base_symbol: str):
    for candidate in _symbol_candidates(base_symbol):
        mt5.symbol_select(candidate, True)
        tick = mt5.symbol_info_tick(candidate)
        if tick is not None:
            return tick, candidate
    return None, None


# ── 接続管理 ────────────────────────────

def initialize() -> bool:
    if not mt5.initialize(path=config.MT5_PATH):
        err = mt5.last_error()
        logger.error("MT5初期化失敗: %s", err)
        discord_notifier.send_error("MT5初期化失敗", str(err))
        return False
    if config.MT5_LOGIN:
        ok = mt5.login(config.MT5_LOGIN, password=config.MT5_PASSWORD, server=config.MT5_SERVER)
        if not ok:
            err = mt5.last_error()
            logger.error("MT5ログイン失敗: %s", err)
            discord_notifier.send_error("MT5ログイン失敗", str(err))
            return False
    info = mt5.account_info()
    if info is None:
        logger.error("アカウント情報取得失敗")
        return False
    logger.info("MT5接続成功: server=%s login=%s balance=%.0f %s",
                info.server, info.login, info.balance, info.currency)
    return True


def shutdown():
    mt5.shutdown()
    logger.info("MT5シャットダウン完了")


def ensure_connected() -> bool:
    if mt5.account_info() is not None:
        return True
    logger.warning("MT5接続断検知 → 再接続試行")
    for attempt in range(3):
        if initialize():
            return True
        time.sleep(2 ** attempt)
    discord_notifier.send_error("MT5接続断", "3回の再接続試行すべて失敗")
    return False


# ── アカウント情報 ──────────────────────

def get_account_info() -> dict | None:
    info = mt5.account_info()
    if info is None:
        return None
    return {
        "balance": info.balance,
        "equity": info.equity,
        "margin": info.margin,
        "margin_free": info.margin_free,
        "currency": info.currency,
        "login": info.login,
    }


# ── シンボル情報 ────────────────────────

def get_symbol_info(symbol: str) -> dict | None:
    # KIWAMI口座など # suffix を自動補正
    resolved_sym = symbol
    for candidate in _symbol_candidates(symbol):
        mt5.symbol_select(candidate, True)
        info = mt5.symbol_info(candidate)
        if info is not None:
            resolved_sym = candidate
            break
    else:
        logger.error("シンボル情報取得失敗: %s", symbol)
        return None
    return {
        "name": info.name,
        "bid": info.bid,
        "ask": info.ask,
        "spread": info.spread,
        "digits": info.digits,
        "trade_contract_size": info.trade_contract_size,
        "volume_min": info.volume_min,
        "volume_max": info.volume_max,
        "volume_step": info.volume_step,
        "currency_base": info.currency_base,
        "currency_profit": info.currency_profit,
        "currency_margin": info.currency_margin,
        "trade_tick_size": info.trade_tick_size,
        "trade_tick_value": info.trade_tick_value,
    }


# ── レート取得 ──────────────────────────

def get_rates(symbol: str, timeframe: str, count: int = 200) -> pd.DataFrame | None:
    tf = TF_MAP.get(timeframe)
    if tf is None:
        logger.error("不明なタイムフレーム: %s", timeframe)
        return None
    # KIWAMI口座など # suffix を自動補正
    for candidate in _symbol_candidates(symbol):
        mt5.symbol_select(candidate, True)
        rates = mt5.copy_rates_from_pos(candidate, tf, 0, count)
        if rates is not None and len(rates) > 0:
            df = pd.DataFrame(rates)
            df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
            df.set_index("time", inplace=True)
            return df
    logger.error("レート取得失敗: %s %s", symbol, timeframe)
    return None


def get_current_price(symbol: str) -> dict | None:
    tick, resolved = _get_tick_with_fallback(symbol)
    if tick is None:
        return None
    if resolved != symbol:
        logger.warning("価格取得でシンボル補正: %s -> %s", symbol, resolved)
    return {"bid": tick.bid, "ask": tick.ask, "time": tick.time}


def is_symbol_market_active(symbol: str, stale_seconds: int | None = None) -> bool:
    # _get_tick_with_fallback で # suffix を自動補正
    tick, resolved = _get_tick_with_fallback(symbol)
    if tick is None:
        return False
    max_age = stale_seconds if stale_seconds is not None else config.MARKET_DATA_STALE_SEC
    if time.time() - tick.time > max_age:
        return False
    if tick.bid <= 0 and tick.ask <= 0:
        return False
    return True


# ── テクニカル指標 ──────────────────────

def calculate_atr(df: pd.DataFrame, period: int = 14) -> float:
    high  = df["high"].values
    low   = df["low"].values
    close = df["close"].values
    tr = np.maximum(
        high[1:] - low[1:],
        np.maximum(np.abs(high[1:] - close[:-1]), np.abs(low[1:] - close[:-1])),
    )
    if len(tr) < period:
        return float(np.mean(tr)) if len(tr) > 0 else 0.0
    return float(np.mean(tr[-period:]))


def calculate_atr_sma(df: pd.DataFrame, atr_period: int = 14, sma_period: int = 50) -> float | None:
    needed = atr_period + sma_period
    if len(df) < needed:
        return None
    high  = df["high"].values
    low   = df["low"].values
    close = df["close"].values
    tr = np.maximum(
        high[1:] - low[1:],
        np.maximum(np.abs(high[1:] - close[:-1]), np.abs(low[1:] - close[:-1])),
    )
    n = len(tr)
    atr_arr = np.full(n, np.nan)
    for i in range(atr_period - 1, n):
        atr_arr[i] = np.mean(tr[i - atr_period + 1: i + 1])
    valid = atr_arr[~np.isnan(atr_arr)]
    if len(valid) < sma_period:
        return None
    return float(np.mean(valid[-sma_period:]))


def calculate_ma(df: pd.DataFrame, period: int = 20) -> pd.Series:
    return df["close"].rolling(window=period).mean()


def calculate_ema(df: pd.DataFrame, period: int, col: str = "close") -> pd.Series:
    """EMA (Exponential Moving Average) を計算する。"""
    return df[col].ewm(span=period, adjust=False).mean()


def calculate_adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """ADX (Average Directional Index) を計算する。

    MT5 Python API は OHLCV のみ提供するため、Wilderスムージングで自前計算する。
    戻り値: ADXの Series (値準：0　25以上=トレンドあり、強さの目安)
    """
    high  = df["high"]
    low   = df["low"]
    close = df["close"]

    # True Range
    tr = pd.concat([
        high - low,
        (high - close.shift(1)).abs(),
        (low  - close.shift(1)).abs(),
    ], axis=1).max(axis=1)

    # Directional Movement
    up   = high.diff()
    down = -low.diff()
    plus_dm  = np.where((up > down) & (up > 0),   up,   0.0)
    minus_dm = np.where((down > up) & (down > 0), down, 0.0)

    alpha = 1.0 / period
    atr_w     = pd.Series(tr.values,        index=df.index, dtype=float).ewm(alpha=alpha, adjust=False).mean()
    plus_di   = 100 * pd.Series(plus_dm,    index=df.index, dtype=float).ewm(alpha=alpha, adjust=False).mean() / atr_w
    minus_di  = 100 * pd.Series(minus_dm,   index=df.index, dtype=float).ewm(alpha=alpha, adjust=False).mean() / atr_w

    di_sum  = (plus_di + minus_di).replace(0, np.nan)
    dx      = 100 * (plus_di - minus_di).abs() / di_sum
    adx     = dx.ewm(alpha=alpha, adjust=False).mean()
    return adx


# ── ポジション管理 ──────────────────────

def get_positions(symbol: str | None = None) -> list[dict]:
    positions = mt5.positions_get(symbol=symbol) if symbol else mt5.positions_get()
    if positions is None:
        return []
    result = []
    for p in positions:
        result.append({
            "ticket":        p.ticket,
            "symbol":        p.symbol,
            "type":          "BUY" if p.type == mt5.ORDER_TYPE_BUY else "SELL",
            "volume":        p.volume,
            "price_open":    p.price_open,
            "price_current": p.price_current,
            "sl":            p.sl,
            "tp":            p.tp,
            "profit":        p.profit,
            "time":          datetime.utcfromtimestamp(p.time),
        })
    return result


def get_all_open_symbols() -> list[str]:
    return list({p["symbol"] for p in get_positions()})


def get_closed_deal_by_ticket(ticket: int) -> dict | None:
    from datetime import timedelta
    date_from = datetime.utcnow() - timedelta(days=180)
    date_to   = datetime.utcnow() + timedelta(days=1)
    deals = mt5.history_deals_get(
        date_from.replace(tzinfo=timezone.utc),
        date_to.replace(tzinfo=timezone.utc),
        position=ticket,
    )
    if not deals:
        return None
    close_candidates = [
        d for d in deals
        if getattr(d, "position_id", None) == ticket
        and d.entry in (mt5.DEAL_ENTRY_OUT, mt5.DEAL_ENTRY_INOUT)
    ]
    if not close_candidates:
        return None
    close_deal = max(close_candidates,
                     key=lambda d: (getattr(d, "time_msc", 0), getattr(d, "time", 0)))
    _REASON_MAP = {
        mt5.DEAL_REASON_TP:     "TP_HIT",
        mt5.DEAL_REASON_SL:     "SL_HIT",
        mt5.DEAL_REASON_CLIENT: "MANUAL",
        mt5.DEAL_REASON_EXPERT: "EA",
        mt5.DEAL_REASON_SO:     "STOP_OUT",
    }
    exit_reason = _REASON_MAP.get(close_deal.reason, f"MT5_REASON_{close_deal.reason}")
    _ts_sec = getattr(close_deal, "time_msc", None)
    if _ts_sec:
        closed_at = datetime.fromtimestamp(_ts_sec / 1000, tz=timezone.utc).replace(tzinfo=None).isoformat()
    else:
        closed_at = datetime.utcfromtimestamp(close_deal.time).isoformat()
    return {
        "exit_price":  close_deal.price,
        "profit":      close_deal.profit,
        "closed_at":   closed_at,
        "exit_reason": exit_reason,
        "symbol":      close_deal.symbol,
    }


# ── 注文実行 ────────────────────────────

def _send_order_with_filling_fallback(request: dict, symbol: str, context: str):
    _NAMES = {mt5.ORDER_FILLING_FOK: "FOK", mt5.ORDER_FILLING_IOC: "IOC", mt5.ORDER_FILLING_RETURN: "RETURN"}
    # シンボルの filling_mode ビットマスクを確認して対応モードを優先する
    # SYMBOL_FILLING_FOK=1(bit0), SYMBOL_FILLING_IOC=2(bit1)
    # どちらも立っていない場合は取引所形式 → RETURN のみ有効
    req_symbol = request.get("symbol", symbol)
    sym = mt5.symbol_info(req_symbol)
    fm_flags = getattr(sym, "filling_mode", 0) if sym else 0
    modes_to_try: list[int] = []
    if fm_flags & 1:                            # FOK 対応
        modes_to_try.append(mt5.ORDER_FILLING_FOK)
    if fm_flags & 2:                            # IOC 対応
        modes_to_try.append(mt5.ORDER_FILLING_IOC)
    if not modes_to_try:                        # どちらも非対応 → RETURN
        modes_to_try.append(mt5.ORDER_FILLING_RETURN)
    # フォールバック: 未追加のモードを末尾に
    for _m in [mt5.ORDER_FILLING_FOK, mt5.ORDER_FILLING_IOC, mt5.ORDER_FILLING_RETURN]:
        if _m not in modes_to_try:
            modes_to_try.append(_m)
    last_result = None
    for mode in modes_to_try:
        req = dict(request)
        req["type_filling"] = mode
        result = mt5.order_send(req)
        if result is not None and result.retcode == mt5.TRADE_RETCODE_DONE:
            logger.info("%s: %sで注文成功: %s", context, _NAMES.get(mode, str(mode)), symbol)
            return result
        rc = result.retcode if result else None
        logger.debug("%s: %s失敗 retcode=%s comment=%s",
                     context, _NAMES.get(mode, str(mode)), rc,
                     result.comment if result else None)
        last_result = result if result is not None else last_result
    return last_result


def place_order(symbol: str, direction: str, lot: float,
                sl: float, tp: float | None = None) -> int | None:
    # KIWAMI口座など # suffix を自動補正
    resolved = symbol
    sym_info = None
    for candidate in _symbol_candidates(symbol):
        mt5.symbol_select(candidate, True)
        info = mt5.symbol_info(candidate)
        if info is not None:
            resolved = candidate
            sym_info = info
            break
    if sym_info is None:
        logger.error("シンボル情報なし: %s", symbol)
        return None
    trade_mode = getattr(sym_info, "trade_mode", 4)
    if trade_mode != 4:
        logger.warning("注文スキップ: %s trade_mode=%s", symbol, trade_mode)
        discord_notifier.send_error(f"注文スキップ: {symbol}", f"trade_mode={trade_mode}")
        return None

    price      = sym_info.ask if direction == "BUY" else sym_info.bid
    order_type = mt5.ORDER_TYPE_BUY if direction == "BUY" else mt5.ORDER_TYPE_SELL

    request: dict = {
        "action":    mt5.TRADE_ACTION_DEAL,
        "symbol":    resolved,
        "volume":    lot,
        "type":      order_type,
        "price":     price,
        "sl":        sl,
        "deviation": 20,
        "magic":     202605,
        "comment":   "MultiAgentMT5",
        "type_time": mt5.ORDER_TIME_GTC,
    }
    if tp is not None:
        request["tp"] = tp

    result = _send_order_with_filling_fallback(request, symbol, "新規注文")
    if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
        err = result.comment if result else "None"
        logger.error("注文失敗: %s err=%s", symbol, err)
        discord_notifier.send_error(f"注文失敗: {symbol}", f"err={err}")
        return None

    logger.info("注文成功: %s %s lot=%.2f price=%.5f ticket=%s",
                symbol, direction, lot, result.price, result.order)
    return result.order if result.order else result.deal


def close_position(ticket: int) -> bool:
    position = mt5.positions_get(ticket=ticket)
    if not position:
        logger.warning("ポジション未検出: ticket=%s", ticket)
        return False
    pos = position[0]
    close_type = mt5.ORDER_TYPE_SELL if pos.type == mt5.ORDER_TYPE_BUY else mt5.ORDER_TYPE_BUY
    tick = mt5.symbol_info_tick(pos.symbol)
    price = tick.bid if pos.type == mt5.ORDER_TYPE_BUY else tick.ask
    request = {
        "action":    mt5.TRADE_ACTION_DEAL,
        "symbol":    pos.symbol,
        "volume":    pos.volume,
        "type":      close_type,
        "position":  ticket,
        "price":     price,
        "deviation": 20,
        "magic":     202605,
        "comment":   "MultiAgentMT5_Exit",
        "type_time": mt5.ORDER_TIME_GTC,
    }
    result = _send_order_with_filling_fallback(request, pos.symbol, "決済注文")
    if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
        err = result.comment if result else "None"
        logger.error("決済失敗: ticket=%s err=%s", ticket, err)
        discord_notifier.send_error("決済失敗", f"ticket={ticket} err={err}")
        return False
    logger.info("決済成功: ticket=%s", ticket)
    return True


def modify_position_sl(ticket: int, new_sl: float) -> bool:
    position = mt5.positions_get(ticket=ticket)
    if not position:
        logger.warning("SL更新対象ポジション未検出: ticket=%s", ticket)
        return False
    pos = position[0]
    request = {
        "action":   mt5.TRADE_ACTION_SLTP,
        "position": ticket,
        "symbol":   pos.symbol,
        "sl":       new_sl,
        "tp":       pos.tp,
        "magic":    202605,
    }
    result = mt5.order_send(request)
    if result is None:
        logger.error("SL更新失敗: ticket=%s new_sl=%.5f err=None", ticket, new_sl)
        return False
    # 10025 = TRADE_RETCODE_NO_CHANGES: SLがすでに同値 → 実質成功
    if result.retcode == mt5.TRADE_RETCODE_DONE or result.retcode == 10025:
        if result.retcode == 10025:
            logger.info("SL更新: ticket=%s new_sl=%.5f (既に同値)", ticket, new_sl)
        else:
            logger.info("SL更新成功: ticket=%s new_sl=%.5f", ticket, new_sl)
        return True
    logger.error("SL更新失敗: ticket=%s new_sl=%.5f err=%s", ticket, new_sl, result.comment)
    return False


# ── サーバー時刻 / 市場開閉判定 ──────────

def get_server_datetime() -> datetime | None:
    from datetime import timedelta
    server_tz = timezone(timedelta(hours=3))
    for sym in list(config.SYMBOLS) + ["EURUSD", "USDJPY", "XAUUSD", "GOLD"]:
        tick, _ = _get_tick_with_fallback(sym)
        if tick is not None:
            utc_dt = datetime.utcfromtimestamp(tick.time).replace(tzinfo=timezone.utc)
            return utc_dt.astimezone(server_tz)
    return None


def is_fx_market_open() -> bool:
    """FX市場開場中か (XMTradingサーバー時刻 GMT+3 で判定)"""
    srv = get_server_datetime()
    if srv is None:
        logger.warning("[MarketHours] サーバー時刻取得失敗 → クローズ扱い")
        return False
    wd = srv.weekday()
    if wd == 5:                    # 土曜: 終日クローズ
        return False
    if wd == 6:                    # 日曜: 23:00以降にオープン
        return srv.hour >= 23
    if wd == 4 and srv.hour >= 23: # 金曜23:00以降: クローズ
        return False
    return True


def _get_conversion_rate(from_ccy: str, to_ccy: str) -> float | None:
    """from_ccy → to_ccy の換算レートを MT5 から取得する。"""
    pair1 = f"{from_ccy}{to_ccy}"
    pair2 = f"{to_ccy}{from_ccy}"
    tick1, _ = _get_tick_with_fallback(pair1)
    if tick1 and tick1.ask > 0:
        return tick1.ask
    tick2, _ = _get_tick_with_fallback(pair2)
    if tick2 and tick2.bid > 0:
        return 1.0 / tick2.bid
    return None
