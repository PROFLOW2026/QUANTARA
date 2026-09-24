"""Alpaca bars API range helpers — UTC normalization and completed-minute semantics."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from quantara_engine.market_data.polling import is_bar_complete, timeframe_minutes


def utc_for_alpaca(ts: datetime) -> datetime:
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc)


def format_alpaca_ts(ts: datetime) -> str:
    """Alpaca expects UTC timestamps; never label local wall time with Z."""
    return utc_for_alpaca(ts).strftime("%Y-%m-%dT%H:%M:%SZ")


def bar_open_utc(ts: datetime, timeframe: str) -> datetime:
    """Floor timestamp to bar open in UTC."""
    ts = utc_for_alpaca(ts)
    step_min = timeframe_minutes(timeframe)
    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    minutes = int((ts - epoch).total_seconds() // 60)
    floored = (minutes // step_min) * step_min
    return epoch + timedelta(minutes=floored)


def exclusive_end_utc(now: datetime, timeframe: str) -> datetime:
    """
    Exclusive end for Alpaca bars requests.

    At xx:55:20 the xx:55 bar is still open, so end = open(xx:55) excludes it.
    """
    return bar_open_utc(now, timeframe)


def latest_completed_bar_open(now: datetime, timeframe: str) -> datetime:
    """Open timestamp of the latest fully closed bar at `now`."""
    now = utc_for_alpaca(now)
    current_open = bar_open_utc(now, timeframe)
    if is_bar_complete(current_open, timeframe, now):
        return current_open
    step = timedelta(minutes=timeframe_minutes(timeframe))
    return current_open - step


def resolve_bars_request_range(
    start: datetime | None,
    end: datetime | None,
    *,
    timeframe: str,
    now: datetime | None = None,
) -> tuple[datetime, datetime] | None:
    """
    Return (start, exclusive_end) in UTC when a valid HTTP range exists.

    Returns None when start >= exclusive_end (no completed bar to fetch yet).
    """
    if start is None:
        return None
    start_u = utc_for_alpaca(start)
    end_u = exclusive_end_utc(end or now or datetime.now(timezone.utc), timeframe)
    if start_u >= end_u:
        return None
    return start_u, end_u


def ranges_equal_at_alpaca_minute_precision(start: datetime, end: datetime) -> bool:
    """True when strftime would produce identical minute tokens for start and end."""
    return format_alpaca_ts(start) >= format_alpaca_ts(end)
