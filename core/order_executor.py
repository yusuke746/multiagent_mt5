"""MT5 への発注・決済・SL 更新（v3.2：ヘッジング前提）。

口座は margin_mode=2（HEDGING）。同一シンボルに複数チケットが並存しうるため、
部分決済・SLTP 更新はチケット単位で行う（position にチケット ID を指定）。
発注シンボルは symbols.yaml の mt5_symbol（会社名）。ティッカーを直接渡さない。
TP は一切設定しない（tp=0.0）。
"""
from __future__ import annotations

import MetaTrader5 as mt5

from core import config_loader as cfg
from core import market_data
from core import mt5_client
from logger import get_logger

log = get_logger("order_exec")

_DEVIATION = 20


def _filling_mode(mt5_symbol: str) -> int:
    """シンボルが許可する filling mode を選ぶ（IOC 優先、なければ FOK）。"""
    info = mt5.symbol_info(mt5_symbol)
    if info is not None and (info.filling_mode & 1):  # SYMBOL_FILLING_FOK
        return mt5.ORDER_FILLING_FOK
    return mt5.ORDER_FILLING_IOC


def _result_dict(result) -> dict:
    if result is None:
        return {"success": False, "retcode": -1, "comment": str(mt5.last_error())}
    return {
        "success": result.retcode == mt5.TRADE_RETCODE_DONE,
        "retcode": result.retcode,
        "comment": result.comment,
        "order": getattr(result, "order", 0),
        "volume": getattr(result, "volume", 0.0),
        "price": getattr(result, "price", 0.0),
    }


def validate_order(order_params: dict, portfolio_state) -> tuple[bool, str]:
    """1つでも失敗したら (False, 理由)。全合格で (True, "OK")。"""
    action = order_params.get("action")
    mt5_symbol = order_params.get("mt5_symbol")
    ticker = order_params.get("ticker")
    lot_size = order_params.get("lot_size", 0.0)
    sl = order_params.get("sl")
    tp = order_params.get("tp", 0.0)

    spec = cfg.spec_for_mt5(mt5_symbol) if mt5_symbol else None
    risk = cfg.load_risk()
    max_per_sector = int(risk["portfolio"]["max_positions_per_sector"])

    # 1. SL 必須
    if sl is None or sl <= 0:
        return False, "SL is not set"
    # 2. TP 禁止（tp>0 は禁止）
    if tp and tp > 0.0:
        return False, "tp > 0 is forbidden in v3.2"
    # 3. ロット下限
    if spec is None:
        return False, f"unknown symbol: {mt5_symbol}"
    if lot_size < spec.volume_min:
        return False, f"lot {lot_size} < volume_min {spec.volume_min}"

    if action == "BUY":
        # 7. 保有中銘柄への新規BUY（二重発注防止）
        if portfolio_state.get_position(ticker) is not None:
            return False, "already holding; new BUY forbidden (use INCREASE)"
        # 4. 空きスロット
        if portfolio_state.available_slots() <= 0:
            return False, "no available slots (portfolio full)"
        # 5. セクター集中制限
        if portfolio_state.sector_count(spec.sector) >= max_per_sector:
            return False, f"sector limit reached ({max_per_sector}) for {spec.sector}"

    return True, "OK"


def open_position(mt5_symbol: str, lots: float, sl: float) -> dict:
    """新規 BUY を成行で開く。TP は設定しない。"""
    if not mt5_client.ensure_symbol(mt5_symbol):
        return {"success": False, "retcode": -1, "comment": "symbol unavailable"}
    ask = market_data.get_ask(mt5_symbol)
    if ask is None:
        return {"success": False, "retcode": -1, "comment": "no price"}

    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": mt5_symbol,
        "volume": float(lots),
        "type": mt5.ORDER_TYPE_BUY,
        "price": ask,
        "sl": float(sl),
        "tp": 0.0,
        "deviation": _DEVIATION,
        "type_filling": _filling_mode(mt5_symbol),
        "type_time": mt5.ORDER_TIME_GTC,
        "comment": "ta-xm open",
    }
    res = _result_dict(mt5.order_send(request))
    if res["success"]:
        log.info("%s: BUY %.2f lot @ $%.2f SL=$%.2f TP=none", mt5_symbol, lots, ask, sl)
    else:
        log.error("%s: open failed retcode %s - %s", mt5_symbol, res["retcode"], res["comment"])
    return res


def add_position(mt5_symbol: str, lots: float, sl: float) -> dict:
    """買い増し（ヘッジ口座では新規チケットとして追加）。TP なし。"""
    # ヘッジ口座では BUY 追加＝新チケット。open_position と同じ発注で実現する。
    res = open_position(mt5_symbol, lots, sl)
    if res["success"]:
        log.info("%s: INCREASE +%.2f lot (new hedging ticket)", mt5_symbol, lots)
    return res


def update_sl_only(mt5_symbol: str, position_ticket: int, new_sl: float) -> dict:
    """既存ポジションの SL のみ更新。TP は 0（TP なし）。ロットは変えない。
    update_trailing_sl が float を返したときのみ呼ぶ（None なら呼ばない）。
    """
    request = {
        "action": mt5.TRADE_ACTION_SLTP,
        "symbol": mt5_symbol,
        "sl": float(new_sl),
        "tp": 0.0,
        "position": int(position_ticket),
    }
    res = _result_dict(mt5.order_send(request))
    if res["success"]:
        log.info("%s: SLTP updated. SL=$%.2f TP=0 (ticket=%s)", mt5_symbol, new_sl, position_ticket)
    else:
        log.error("%s: SLTP update failed retcode %s - %s", mt5_symbol, res["retcode"], res["comment"])
    return res


def _close_ticket(mt5_symbol: str, ticket: int, lots: float) -> dict:
    """BUY ポジション（チケット単位）を成行で決済する。"""
    bid = market_data.get_bid(mt5_symbol)
    if bid is None:
        return {"success": False, "retcode": -1, "comment": "no price"}
    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": mt5_symbol,
        "volume": float(lots),
        "type": mt5.ORDER_TYPE_SELL,   # BUY の反対売買
        "position": int(ticket),
        "price": bid,
        "deviation": _DEVIATION,
        "type_filling": _filling_mode(mt5_symbol),
        "type_time": mt5.ORDER_TIME_GTC,
        "comment": "ta-xm close",
    }
    return _result_dict(mt5.order_send(request))


def close_position(position_info) -> list[dict]:
    """全チケットを決済する（CLOSE）。"""
    results = []
    for t in position_info.tickets:
        res = _close_ticket(position_info.mt5_symbol, t.ticket, t.lots)
        results.append(res)
        if res["success"]:
            log.info("%s: CLOSE %.2f lot (ticket=%s)", position_info.mt5_symbol, t.lots, t.ticket)
        else:
            log.error("%s: close failed ticket=%s - %s", position_info.mt5_symbol, t.ticket, res["comment"])
    return results


def partial_close(position_info, fraction: float = 0.5) -> list[dict]:
    """保有ロットの fraction 割合（既定 50%）を決済する（DECREASE）。

    ヘッジ口座のためチケットごとに fraction を按分し、銘柄別 volume_step で丸める。
    """
    spec = cfg.spec_for_mt5(position_info.mt5_symbol)
    step = spec.volume_step if spec else 0.01
    vmin = spec.volume_min if spec else 0.01
    import math

    results = []
    for t in position_info.tickets:
        raw = t.lots * fraction
        steps = math.floor(raw / step)
        close_lots = round(steps * step, 8)
        if close_lots < vmin or close_lots <= 0:
            log.info("%s: skip partial close ticket=%s (below volume_min)", position_info.mt5_symbol, t.ticket)
            continue
        # チケット総量以上は決済しない
        close_lots = min(close_lots, t.lots)
        res = _close_ticket(position_info.mt5_symbol, t.ticket, close_lots)
        results.append(res)
        if res["success"]:
            log.info("%s: DECREASE %.2f/%.2f lot (ticket=%s)", position_info.mt5_symbol, close_lots, t.lots, t.ticket)
        else:
            log.error("%s: partial close failed ticket=%s - %s", position_info.mt5_symbol, t.ticket, res["comment"])
    return results
