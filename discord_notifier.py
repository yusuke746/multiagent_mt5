"""Discord 通知モジュール"""

import logging
from datetime import datetime

import requests

import config

logger = logging.getLogger(__name__)


def _send(content: str = "", embeds: list[dict] | None = None):
    if not config.DISCORD_WEBHOOK_URL:
        return
    payload: dict = {}
    if content:
        payload["content"] = content[:2000]
    if embeds:
        payload["embeds"] = embeds[:10]
    try:
        resp = requests.post(config.DISCORD_WEBHOOK_URL, json=payload, timeout=10)
        if resp.status_code not in (200, 204):
            logger.error("Discord送信失敗: %s %s", resp.status_code, resp.text[:200])
    except requests.RequestException as e:
        logger.error("Discord送信エラー: %s", e)


def send_heartbeat(balance: float, equity: float, open_positions: int,
                   drawdown_pct: float = 0.0):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    embed = {
        "title": "⚠️ Heartbeat Alert",
        "color": 0xFF6600,
        "fields": [
            {"name": "時刻",             "value": now,                 "inline": True},
            {"name": "残高",             "value": f"¥{balance:,.0f}",  "inline": True},
            {"name": "有効証拠金",       "value": f"¥{equity:,.0f}",   "inline": True},
            {"name": "保有ポジション",   "value": str(open_positions), "inline": True},
            {"name": "浮動損益 (DD%)",   "value": f"-{drawdown_pct:.1f}%", "inline": True},
        ],
    }
    _send(embeds=[embed])


def send_analysis(symbol: str, direction: str, rating: str, reasoning: str):
    """TradingAgents分析結果の通知"""
    color = 0x2ECC71 if direction == "BUY" else (0xE74C3C if direction == "SELL" else 0x95A5A6)
    dir_emoji = "📈" if direction == "BUY" else ("📉" if direction == "SELL" else "↔️")
    embed = {
        "title": f"{dir_emoji} TA分析: {symbol} → {direction}",
        "color": color,
        "fields": [
            {"name": "評価",   "value": rating,                "inline": True},
            {"name": "方向",   "value": direction,             "inline": True},
            {"name": "サマリー", "value": reasoning[:800]},
        ],
        "timestamp": datetime.utcnow().isoformat(),
    }
    _send(embeds=[embed])


def send_entry(symbol: str, direction: str, lot: float,
               entry_price: float, sl: float, tp: float, rating: str):
    color = 0x2ECC71 if direction == "BUY" else 0xE74C3C
    embed = {
        "title": f"📈 Entry: {symbol} {direction}",
        "color": color,
        "fields": [
            {"name": "ロット",         "value": f"{lot:.2f}",   "inline": True},
            {"name": "エントリー価格", "value": str(entry_price),"inline": True},
            {"name": "SL",             "value": str(sl),        "inline": True},
            {"name": "TP",             "value": str(tp),        "inline": True},
            {"name": "TA評価",         "value": rating,         "inline": True},
        ],
        "timestamp": datetime.utcnow().isoformat(),
    }
    _send(embeds=[embed])


def send_exit(symbol: str, direction: str, exit_price: float,
              profit: float, exit_reason: str):
    color = 0x2ECC71 if profit >= 0 else 0xE74C3C
    embed = {
        "title": f"📉 Exit: {symbol} {direction}",
        "color": color,
        "fields": [
            {"name": "決済価格", "value": str(exit_price),        "inline": True},
            {"name": "損益",     "value": f"¥{profit:,.0f}",      "inline": True},
            {"name": "理由",     "value": exit_reason,            "inline": True},
        ],
        "timestamp": datetime.utcnow().isoformat(),
    }
    _send(embeds=[embed])


def send_error(title: str, detail: str):
    embed = {
        "title": f"🚨 Error: {title}",
        "color": 0xFF0000,
        "description": detail[:2000],
        "timestamp": datetime.utcnow().isoformat(),
    }
    _send(embeds=[embed])


def send_skip(symbol: str, reason: str, notify: bool = False):
    logger.info("SKIP %s: %s", symbol, reason)
    if not notify:
        return
    embed = {
        "title": f"⏭️ Skip: {symbol}",
        "color": 0xF1C40F,
        "description": reason[:1000],
        "timestamp": datetime.utcnow().isoformat(),
    }
    _send(embeds=[embed])
