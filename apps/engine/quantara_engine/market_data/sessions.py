"""Market session helpers for aggregation and entry gating."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

US_EASTERN = ZoneInfo("America/New_York")
US_RTH_OPEN = time(9, 30)
US_RTH_CLOSE = time(16, 0)


def is_us_equity_rth(ts: datetime) -> bool:
    """True when timestamp falls inside regular US equity session (09:30–16:00 ET)."""
    local = ts.astimezone(US_EASTERN)
    if local.weekday() >= 5:
        return False
    t = local.time()
    return US_RTH_OPEN <= t < US_RTH_CLOSE


def us_rth_bucket_start(timestamp: datetime, timeframe: str) -> datetime | None:
    """
    Floor timestamp to an RTH-aligned bucket open in UTC.

    Returns None when the timestamp is outside regular session or cannot form
    a valid bucket (e.g. before first complete 5m after 09:30 ET).
    """
    from quantara_engine.market_data.polling import timeframe_minutes

    if not is_us_equity_rth(timestamp):
        return None

    local = timestamp.astimezone(US_EASTERN)
    session_open = datetime.combine(local.date(), US_RTH_OPEN, tzinfo=US_EASTERN)
    minutes = timeframe_minutes(timeframe)
    elapsed = int((local - session_open).total_seconds() // 60)
    if elapsed < 0:
        return None
    bucket_index = elapsed // minutes
    bucket_local = session_open + timedelta(minutes=bucket_index * minutes)
    if bucket_local.time() >= US_RTH_CLOSE:
        return None
    return bucket_local.astimezone(timezone.utc)


def expected_us_rth_5m_timestamps(bucket: datetime, count: int) -> list[datetime]:
    from quantara_engine.market_data.polling import timeframe_minutes

    base = timeframe_minutes("5m")
    return [bucket + timedelta(minutes=base * i) for i in range(count)]


def is_forex_session(ts: datetime) -> bool:
    """FX trades 24x5 — closed Saturday/Sunday UTC evening through Sunday evening."""
    utc = ts.astimezone(timezone.utc)
    wd = utc.weekday()
    if wd == 5:
        return False
    if wd == 6 and utc.hour < 22:
        return False
    if wd == 4 and utc.hour >= 22:
        return False
    return True


def is_crypto_session(ts: datetime) -> bool:
    return True


def session_allows_entries(asset_sessions: dict, ts: datetime) -> bool:
    sessions = asset_sessions.get("sessions") or []
    if "24x7" in sessions:
        return is_crypto_session(ts)
    if "24x5" in sessions:
        return is_forex_session(ts)
    if "us_equity_rth" in sessions:
        return is_us_equity_rth(ts)
    return True
