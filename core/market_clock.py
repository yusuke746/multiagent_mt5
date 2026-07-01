"""米国市場カレンダー（夏冬時間を zoneinfo で自動対応）。

TradingAgents に渡す trade_date を、実行時刻（JST 6:00/7:00 等）に依存せず
「直近に確定した米国取引日」から算出する。America/New_York タイムゾーンを使うため
夏時間(EDT)/冬時間(EST)は自動で切り替わる。

制限: 米国の祝日（Thanksgiving 等）は考慮しない（週末とクローズ時刻のみ判定）。
祝日当日に起動した場合は前営業日相当の日付が返らず祝日日付になり得るが、
TradingAgents 側のデータソースが前営業日データにフォールバックする想定。
"""
from __future__ import annotations

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

_ET = ZoneInfo("America/New_York")
_UTC = ZoneInfo("UTC")
_REGULAR_CLOSE = time(16, 0)  # 16:00 ET（夏冬とも現地16:00）


def last_completed_us_trading_day(now_utc: datetime | None = None) -> str:
    """直近に確定した米国取引日を 'YYYY-MM-DD' で返す。

    - 平日かつ現地 16:00 以降 → 当日（その日のセッションは確定済み）
    - それ以外（クローズ前・週末）→ 直前の平日まで遡る
    """
    now_et = (now_utc or datetime.now(tz=_UTC)).astimezone(_ET)

    completed_today = now_et.weekday() < 5 and now_et.time() >= _REGULAR_CLOSE
    d = now_et.date() if completed_today else (now_et.date() - timedelta(days=1))

    # 週末（土=5 / 日=6）は直前の金曜まで遡る
    while d.weekday() >= 5:
        d -= timedelta(days=1)

    return d.isoformat()
