"""Decision-time entry quote for Live Sim (price actually available when the order is created)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from quantara_engine.execution.timing import decision_time_fill_allowed
from quantara_engine.market_data.polling import bar_close_timestamp


def _utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


@dataclass(frozen=True)
class DecisionCandle:
    """Candle-shaped quote used by the paper broker (timestamp = decision time).

    open/high/low/close all equal the price that was available at decision time, so the
    broker's open-based entry pricing and low/high clamping can never use a price from
    the future (or from a bar that had not yet been observed).
    """

    timestamp: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    quote_timestamp: datetime
    quote_source: str
    volume: Decimal = Decimal("0")


def resolve_decision_time_candle(
    store: Any,
    instrument: Any,
    *,
    signal_candle_timestamp: datetime,
    signal_candle_close: Decimal | None,
    timeframe: str,
    now: datetime,
) -> tuple[DecisionCandle | None, str | None]:
    """Return (quote candle, None) or (None, reject_reason).

    Price = most recent *completed* 1m close at/before ``now``; if the signal bar closed
    later than that 1m bar (1m feed lag) the signal bar close is used instead. Never a
    price from after ``now``.
    """
    now = _utc(now)
    signal_ts = _utc(signal_candle_timestamp)

    best_price: Decimal | None = None
    best_ts: datetime | None = None
    best_src = ""

    try:
        ones = store.list_recent_candles(instrument.id, "1m", limit=8)
    except Exception:  # noqa: BLE001
        ones = []
    for c in reversed(ones or []):
        close_ts = _utc(c.timestamp) + timedelta(minutes=1)
        if close_ts <= now:
            best_price = Decimal(str(c.close))
            best_ts = close_ts
            best_src = "1m_close"
            break

    signal_close_ts = bar_close_timestamp(signal_ts, timeframe)
    if signal_candle_close is not None and signal_close_ts <= now:
        if best_ts is None or signal_close_ts > best_ts:
            best_price = Decimal(str(signal_candle_close))
            best_ts = signal_close_ts
            best_src = f"signal_{timeframe}_close"

    allowed, reason = decision_time_fill_allowed(
        signal_candle_timestamp=signal_ts,
        timeframe=timeframe,
        now=now,
        quote_timestamp=best_ts,
    )
    if not allowed or best_price is None or best_ts is None:
        return None, reason or "decision_quote_missing"
    return (
        DecisionCandle(
            timestamp=now,
            open=best_price,
            high=best_price,
            low=best_price,
            close=best_price,
            quote_timestamp=best_ts,
            quote_source=best_src,
        ),
        None,
    )
