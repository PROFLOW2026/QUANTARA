"""Live Paper execution timing — canonical N+1 open semantics with bounded grace."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from quantara_engine.market_data.polling import timeframe_minutes

# After signal bar N closes, fill is scheduled at N+1 open.
# Processing/provider latency gets one extra bar of grace before expiry.
LIVE_EXECUTION_GRACE_MINUTES = 8

# Intent must be created within this many minutes of the N+1 open timestamp.
INTENT_CREATION_TOLERANCE_MINUTES = 5

# Beyond this age from signal candle, never open new exposure (≈3 × 5m bars).
STALE_SIGNAL_MAX_AGE_MINUTES = 15


def next_execution_timestamp(signal_candle_timestamp: datetime, timeframe: str) -> datetime:
    minutes = timeframe_minutes(timeframe)
    if signal_candle_timestamp.tzinfo is None:
        signal_candle_timestamp = signal_candle_timestamp.replace(tzinfo=timezone.utc)
    return signal_candle_timestamp + timedelta(minutes=minutes)


def execution_grace_deadline(
    execution_candle_timestamp: datetime,
    timeframe: str,
    *,
    grace_minutes: int = LIVE_EXECUTION_GRACE_MINUTES,
) -> datetime:
    """Last moment a pending intent may still fill at the scheduled N+1 open."""
    if execution_candle_timestamp.tzinfo is None:
        execution_candle_timestamp = execution_candle_timestamp.replace(tzinfo=timezone.utc)
    bar_minutes = timeframe_minutes(timeframe)
    return execution_candle_timestamp + timedelta(minutes=bar_minutes + grace_minutes)


def intent_past_execution_window(
    *,
    signal_candle_timestamp: datetime,
    execution_candle_timestamp: datetime | None,
    intent_created_at: datetime,
    timeframe: str,
    now: datetime,
    grace_minutes: int = LIVE_EXECUTION_GRACE_MINUTES,
) -> bool:
    """True when an intent can no longer be filled without creating stale exposure."""
    exec_ts = execution_candle_timestamp or next_execution_timestamp(
        signal_candle_timestamp, timeframe
    )
    deadline = execution_grace_deadline(exec_ts, timeframe, grace_minutes=grace_minutes)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    if intent_created_at.tzinfo is None:
        intent_created_at = intent_created_at.replace(tzinfo=timezone.utc)
    creation_deadline = exec_ts + timedelta(minutes=INTENT_CREATION_TOLERANCE_MINUTES)
    if intent_created_at > creation_deadline:
        return True
    return now > deadline


def live_fill_allowed(
    *,
    execution_candle_timestamp: datetime,
    candle_timestamp: datetime,
    signal_candle_timestamp: datetime,
    now: datetime,
    timeframe: str,
    grace_minutes: int = LIVE_EXECUTION_GRACE_MINUTES,
) -> tuple[bool, str | None]:
    """Return (allowed, rejection_reason) for filling at N+1 open with bounded latency."""
    if execution_candle_timestamp != candle_timestamp:
        return False, "execution_candle_mismatch"

    age_min = (now - signal_candle_timestamp).total_seconds() / 60
    if age_min > STALE_SIGNAL_MAX_AGE_MINUTES:
        return False, f"stale_signal_age ({round(age_min, 1)}m)"

    deadline = execution_grace_deadline(
        execution_candle_timestamp, timeframe, grace_minutes=grace_minutes
    )
    if now > deadline:
        return False, "execution_window_passed"

    expected_delta = timeframe_minutes(timeframe)
    actual_delta = (
        execution_candle_timestamp - signal_candle_timestamp
    ).total_seconds() / 60
    if actual_delta > expected_delta + 1:
        return False, "invalid_next_open_spacing"

    return True, None
