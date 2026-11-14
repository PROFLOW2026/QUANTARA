"""Real market session and data freshness gates for broker execution."""

from __future__ import annotations

from datetime import datetime, timezone

from quantara_engine.broker.instruments import get_instrument_spec
from quantara_engine.domain.types import Instrument
from quantara_engine.execution.timing import freshness_max_age_minutes
from quantara_engine.market_data.registry import get_asset
from quantara_engine.market_data.sessions import session_allows_entries
from quantara_engine.persistence.store import TradingStore


def market_open_for_instrument(instrument: Instrument, at: datetime) -> bool:
    spec = get_instrument_spec(instrument.symbol)
    try:
        asset = get_asset(instrument.symbol)
        sessions = asset.get("sessions") if isinstance(asset, dict) else None
    except Exception:
        sessions = None
    if sessions:
        return session_allows_entries({"sessions": sessions}, at)
    key = spec.session_key
    if key == "24x7":
        return True
    if key == "24x5":
        from quantara_engine.market_data.sessions import is_forex_session

        return is_forex_session(at)
    if key == "us_equity_rth":
        from quantara_engine.market_data.sessions import is_us_equity_rth

        return is_us_equity_rth(at)
    return True


def candle_executable_for_instrument(instrument: Instrument, candle_timestamp: datetime) -> bool:
    """True when a protective/strategy exit may be *executed* during this candle.

    Premarket / after-hours equity candles (and weekend FX candles) are valid market data
    but the simulated broker rejects equity closes while the market is closed
    (``market_closed``). Detecting an SL/TP in such a candle would create a protective order
    that can never fill and loops forever. Exit detection therefore only considers candles
    whose open time is inside an executable session; the first executable candle then
    applies the open-first rule, so an overnight/premarket gap fills at the real open.
    """
    ts = candle_timestamp
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    try:
        return market_open_for_instrument(instrument, ts)
    except Exception:  # noqa: BLE001 — never block protection on a session-lookup error
        return True


def data_fresh_for_instrument(
    store: TradingStore,
    instrument: Instrument,
    timeframe: str,
    at: datetime,
) -> tuple[bool, float | None]:
    """Return (is_fresh, age_minutes)."""
    ts = store.latest_candle_timestamp(instrument.id, timeframe)
    if ts is None:
        return False, None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    if at.tzinfo is None:
        at = at.replace(tzinfo=timezone.utc)
    age_min = (at - ts).total_seconds() / 60
    max_age = freshness_max_age_minutes(timeframe)
    return age_min <= max_age, round(age_min, 2)
