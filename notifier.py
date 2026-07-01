"""Discord Webhook 通知（LINE Notify 終了のため Discord を使用）。

発注・エラー・サイクルサマリを Discord チャンネルへ送信する。
Webhook URL が未設定の場合は送信をスキップし、警告ログのみ出力する（実行は止めない）。
"""
from __future__ import annotations

import os
from typing import Any

import requests

from logger import get_logger

log = get_logger("notifier")

_MAX_LEN = 1900  # Discord の 2000 文字制限に対する安全マージン

_COLOR = {
    "INFO": 0x2ECC71,   # green
    "WARN": 0xF1C40F,   # yellow
    "ERROR": 0xE74C3C,  # red
}


def _webhook_url() -> str | None:
    url = os.getenv("DISCORD_WEBHOOK_URL", "").strip()
    return url or None


def send(title: str, message: str, level: str = "INFO") -> bool:
    """Discord へ Embed を1件送信する。成功時 True。

    ネットワーク例外は握りつぶし（取引本体を止めない）、False を返す。
    """
    url = _webhook_url()
    if not url:
        log.warning("DISCORD_WEBHOOK_URL is not set. Skip notification: %s", title)
        return False

    level = level.upper() if level.upper() in _COLOR else "INFO"
    description = message if len(message) <= _MAX_LEN else message[:_MAX_LEN] + "\n…(truncated)"

    payload: dict[str, Any] = {
        "embeds": [
            {
                "title": title[:250],
                "description": description,
                "color": _COLOR[level],
            }
        ]
    }
    try:
        resp = requests.post(url, json=payload, timeout=10)
        if resp.status_code in (200, 204):
            log.info("Discord webhook sent (%s).", title)
            return True
        log.warning("Discord webhook failed: HTTP %s %s", resp.status_code, resp.text[:200])
        return False
    except requests.RequestException as e:
        log.warning("Discord webhook error: %s", e)
        return False


def send_cycle_summary(lines: list[str], level: str = "INFO") -> bool:
    """1サイクルの結果サマリをまとめて送信する。"""
    body = "\n".join(lines) if lines else "(no actions this cycle)"
    return send("TradingAgents × XM サイクルサマリ", body, level)
