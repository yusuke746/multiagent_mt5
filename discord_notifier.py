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


def send_weekly_screening(
    selected: dict,
    all_results: list[dict],
    analysis_date: str,
    prev_symbols: list[str],
) -> None:
    """週次スクリーニング結果の通知"""
    mt5_symbol = selected.get("mt5_symbol", selected.get("ticker", "N/A"))
    action     = selected.get("action", "N/A")
    score      = selected.get("score", 0.0)
    momentum   = selected.get("momentum_20d", 0.0)
    rsi        = selected.get("rsi", 0.0)
    price      = selected.get("price", 0.0)

    candidates_text = "\n".join(
        f"  • {r.get('mt5_symbol', r.get('ticker', '?'))}: "
        f"{r.get('action', 'N/A')} (score: {r.get('score', 0.0):.4f})"
        for r in all_results
    )

    color = 0x00FF88 if "BUY" in action or action == "OVERWEIGHT" else 0xAAAAAA
    embed = {
        "title": f"📅 週次監視銘柄 決定 ({analysis_date} 週)",
        "color": color,
        "fields": [
            {"name": "🎯 今週の監視銘柄",   "value": f"**{mt5_symbol}**",     "inline": True},
            {"name": "📊 シグナル",          "value": f"**{action}**",         "inline": True},
            {"name": "💰 現在価格",          "value": f"${price:.2f}",         "inline": True},
            {"name": "📈 20日モメンタム",    "value": f"{momentum:+.1f}%",     "inline": True},
            {"name": "⚡ RSI",               "value": f"{rsi:.1f}",            "inline": True},
            {"name": "🔢 スクリーナースコア","value": f"{score:.4f}",          "inline": True},
            {"name": "📋 候補銘柄分析",      "value": candidates_text or "N/A","inline": False},
            {"name": "⚙️ 前週銘柄",         "value": ", ".join(prev_symbols) or "N/A", "inline": False},
        ],
        "footer": {"text": "Weekly Screener → multiagent_mt5"},
        "timestamp": datetime.utcnow().isoformat(),
    }
    _send(embeds=[embed])
