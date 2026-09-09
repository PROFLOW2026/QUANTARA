"""US equity RTH opening-range session helpers (America/New_York)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from quantara_engine.market_data.sessions import US_EASTERN, US_RTH_CLOSE, US_RTH_OPEN

ORB_RANGE_OPEN = US_RTH_OPEN
ORB_RANGE_COMPLETE = time(10, 0)
ORB_ENTRY_CUTOFF = time(15, 30)
ORB_SESSION_CLOSE = US_RTH_CLOSE
ORB_SESSION_CLOSE_SIGNAL = time(15, 55)


@dataclass(frozen=True)
class OpeningRange:
    session_date: date
    high: Decimal
    low: Decimal
    size: Decimal
    candle_count: int


def to_et(ts: datetime) -> datetime:
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(US_EASTERN)


def rth_session_date(ts: datetime) -> date | None:
    """Session calendar date for an ET timestamp inside RTH, else None."""
    local = to_et(ts)
    if local.weekday() >= 5:
        return None
    if local.time() < US_RTH_OPEN or local.time() >= US_RTH_CLOSE:
        return None
    return local.date()


def expected_opening_range_candle_opens(session_date: date) -> list[datetime]:
    """Six canonical 5m bar open times for the 09:30–09:55 ET opening range."""
    session_open = datetime.combine(session_date, ORB_RANGE_OPEN, tzinfo=US_EASTERN)
    return [
        (session_open + timedelta(minutes=5 * i)).astimezone(timezone.utc)
        for i in range(6)
    ]


def is_opening_range_complete(ts: datetime) -> bool:
    local = to_et(ts)
    if local.weekday() >= 5:
        return False
    return local.time() >= ORB_RANGE_COMPLETE


def is_entry_cutoff_passed(ts: datetime) -> bool:
    local = to_et(ts)
    if local.weekday() >= 5:
        return True
    return local.time() >= ORB_ENTRY_CUTOFF


def is_session_close_window(ts: datetime) -> bool:
    local = to_et(ts)
    if local.weekday() >= 5:
        return False
    return ORB_SESSION_CLOSE_SIGNAL <= local.time() < ORB_SESSION_CLOSE


def is_market_closed(ts: datetime) -> bool:
    local = to_et(ts)
    if local.weekday() >= 5:
        return True
    return local.time() < US_RTH_OPEN or local.time() >= US_RTH_CLOSE


def compute_opening_range(candles: list, session_date: date) -> OpeningRange | None:
    """Build opening range from completed 5m candles whose open is in 09:30–09:55 ET."""
    expected = {ts for ts in expected_opening_range_candle_opens(session_date)}
    matched = []
    for candle in candles:
        ts = candle.timestamp
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        if ts not in expected:
            continue
        matched.append(candle)

    if len(matched) < 6:
        return None

    highs = [Decimal(str(c.high)) for c in matched]
    lows = [Decimal(str(c.low)) for c in matched]
    high = max(highs)
    low = min(lows)
    return OpeningRange(
        session_date=session_date,
        high=high,
        low=low,
        size=high - low,
        candle_count=len(matched),
    )
