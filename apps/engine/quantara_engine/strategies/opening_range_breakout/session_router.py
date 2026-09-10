"""ORB session routing — US RTH, crypto UTC daily, FX UTC daily."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from typing import Protocol
from zoneinfo import ZoneInfo

from quantara_engine.market_data.active_universe import orb_session_for
from quantara_engine.market_data.sessions import US_EASTERN, US_RTH_CLOSE, US_RTH_OPEN, is_forex_session
from quantara_engine.strategies.opening_range_breakout.session import (
    OpeningRange,
    compute_opening_range as compute_us_opening_range,
    is_market_closed as is_us_market_closed,
    is_opening_range_complete as is_us_opening_range_complete,
    rth_session_date,
    to_et,
)

UTC = timezone.utc
CRYPTO_RANGE_COMPLETE = time(0, 30)


class OrbSessionHandler(Protocol):
    def session_date(self, ts: datetime) -> date | None: ...
    def is_market_closed(self, ts: datetime) -> bool: ...
    def is_opening_range_complete(self, ts: datetime) -> bool: ...
    def compute_opening_range(self, candles: list, session_date: date) -> OpeningRange | None: ...


@dataclass(frozen=True)
class UsEquityRthSession:
    def session_date(self, ts: datetime) -> date | None:
        return rth_session_date(ts)

    def is_market_closed(self, ts: datetime) -> bool:
        return is_us_market_closed(ts)

    def is_opening_range_complete(self, ts: datetime) -> bool:
        return is_us_opening_range_complete(ts)

    def compute_opening_range(self, candles: list, session_date: date) -> OpeningRange | None:
        return compute_us_opening_range(candles, session_date)


@dataclass(frozen=True)
class CryptoUtcDailySession:
    def session_date(self, ts: datetime) -> date | None:
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        return ts.astimezone(UTC).date()

    def is_market_closed(self, ts: datetime) -> bool:
        return False

    def is_opening_range_complete(self, ts: datetime) -> bool:
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        local = ts.astimezone(UTC)
        return local.time() >= CRYPTO_RANGE_COMPLETE

    def compute_opening_range(self, candles: list, session_date: date) -> OpeningRange | None:
        expected = {
            datetime.combine(session_date, time(0, 0), tzinfo=UTC) + timedelta(minutes=5 * i)
            for i in range(6)
        }
        return _match_opening_range(candles, session_date, expected)


@dataclass(frozen=True)
class FxUtcDailySession:
    def session_date(self, ts: datetime) -> date | None:
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        if not is_forex_session(ts):
            return None
        return ts.astimezone(UTC).date()

    def is_market_closed(self, ts: datetime) -> bool:
        return not is_forex_session(ts)

    def is_opening_range_complete(self, ts: datetime) -> bool:
        if self.is_market_closed(ts):
            return False
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        return ts.astimezone(UTC).time() >= CRYPTO_RANGE_COMPLETE

    def compute_opening_range(self, candles: list, session_date: date) -> OpeningRange | None:
        expected = {
            datetime.combine(session_date, time(0, 0), tzinfo=UTC) + timedelta(minutes=5 * i)
            for i in range(6)
        }
        return _match_opening_range(candles, session_date, expected)


def _match_opening_range(
    candles: list,
    session_date: date,
    expected: set[datetime],
) -> OpeningRange | None:
    matched = []
    for candle in candles:
        ts = candle.timestamp
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
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


_HANDLERS: dict[str, OrbSessionHandler] = {
    "us_equity_rth": UsEquityRthSession(),
    "crypto_utc_daily": CryptoUtcDailySession(),
    "fx_utc_daily": FxUtcDailySession(),
}


def get_orb_session_handler(db_symbol: str) -> OrbSessionHandler:
    session_type = orb_session_for(db_symbol)
    if not session_type:
        return UsEquityRthSession()
    return _HANDLERS[session_type]
