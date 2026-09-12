"""Live Paper execution timing — canonical N+1 open semantics with timeframe-aware grace."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from quantara_engine.market_data.polling import bar_close_timestamp, is_bar_complete, timeframe_minutes

# Baseline 5m constants (preserved for backward-compatible defaults).
LIVE_EXECUTION_GRACE_MINUTES = 8
INTENT_CREATION_TOLERANCE_MINUTES = 5
STALE_SIGNAL_MAX_AGE_MINUTES = 15


def intent_creation_tolerance_minutes(timeframe: str) -> int:
    """Minutes after N+1 open by which the intent must exist."""
    bar = timeframe_minutes(timeframe)
    if bar <= 5:
        return INTENT_CREATION_TOLERANCE_MINUTES
    return bar


def execution_grace_minutes(timeframe: str) -> int:
    """Extra minutes after the execution bar closes before expiry."""
    bar = timeframe_minutes(timeframe)
    if bar <= 5:
        return LIVE_EXECUTION_GRACE_MINUTES
    return bar + LIVE_EXECUTION_GRACE_MINUTES


def stale_signal_max_age_minutes(timeframe: str) -> int:
    """Maximum signal age (~3 bars) before live entry is forbidden."""
    bar = timeframe_minutes(timeframe)
    return 3 * bar


def freshness_max_age_minutes(timeframe: str) -> int:
    """Maximum signal age for live intent creation at strategy evaluation."""
    bar = timeframe_minutes(timeframe)
    if bar <= 5:
        return 30
    return stale_signal_max_age_minutes(timeframe) + intent_creation_tolerance_minutes(timeframe)


def _as_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def normalize_utc(dt: datetime) -> datetime:
    """Public UTC normalizer for cross-source timestamp comparisons."""
    return _as_utc(dt)


def find_candle_at_timestamp(candles: list, timestamp: datetime):
    """Return the candle whose open timestamp matches (UTC-normalized)."""
    target = normalize_utc(timestamp)
    for candle in candles:
        if normalize_utc(candle.timestamp) == target:
            return candle
    return None


def resolve_execution_candle(
    candles: list,
    signal_candle_timestamp: datetime,
    timeframe: str,
    *,
    execution_candle_timestamp: datetime | None = None,
):
    """Resolve N+1 execution candle — same semantics as Research intent execution."""
    exec_ts = execution_candle_timestamp or next_execution_timestamp(
        signal_candle_timestamp, timeframe
    )
    candle = find_candle_at_timestamp(candles, exec_ts)
    if candle is not None:
        return candle, exec_ts

    target_signal = normalize_utc(signal_candle_timestamp)
    for idx, row in enumerate(candles):
        if normalize_utc(row.timestamp) == target_signal:
            next_idx = idx + 1
            if next_idx < len(candles):
                next_candle = candles[next_idx]
                return next_candle, next_candle.timestamp
            break
    return None, exec_ts


def is_execution_candle_ready(
    *,
    signal_candle_timestamp: datetime,
    execution_candle_timestamp: datetime | None,
    timeframe: str,
    now: datetime,
) -> tuple[bool, str | None]:
    """Canonical readiness gate shared by Research and Live Sim."""
    exec_ts = execution_candle_timestamp or next_execution_timestamp(
        signal_candle_timestamp, timeframe
    )
    if not is_bar_complete(exec_ts, timeframe, now):
        return False, "execution_bar_not_complete"
    return live_fill_allowed(
        execution_candle_timestamp=exec_ts,
        candle_timestamp=exec_ts,
        signal_candle_timestamp=signal_candle_timestamp,
        now=now,
        timeframe=timeframe,
    )


def next_execution_timestamp(signal_candle_timestamp: datetime, timeframe: str) -> datetime:
    minutes = timeframe_minutes(timeframe)
    if signal_candle_timestamp.tzinfo is None:
        signal_candle_timestamp = signal_candle_timestamp.replace(tzinfo=timezone.utc)
    return signal_candle_timestamp + timedelta(minutes=minutes)


def execution_grace_deadline(
    execution_candle_timestamp: datetime,
    timeframe: str,
    *,
    grace_minutes: int | None = None,
) -> datetime:
    """Last moment a pending intent may still fill at the scheduled N+1 open."""
    if execution_candle_timestamp.tzinfo is None:
        execution_candle_timestamp = execution_candle_timestamp.replace(tzinfo=timezone.utc)
    bar_minutes = timeframe_minutes(timeframe)
    grace = grace_minutes if grace_minutes is not None else execution_grace_minutes(timeframe)
    return execution_candle_timestamp + timedelta(minutes=bar_minutes + grace)


def intent_past_execution_window(
    *,
    signal_candle_timestamp: datetime,
    execution_candle_timestamp: datetime | None,
    intent_created_at: datetime,
    timeframe: str,
    now: datetime,
    grace_minutes: int | None = None,
) -> bool:
    """True when an intent can no longer be filled without creating stale exposure."""
    grace = grace_minutes if grace_minutes is not None else execution_grace_minutes(timeframe)
    tolerance = intent_creation_tolerance_minutes(timeframe)
    exec_ts = execution_candle_timestamp or next_execution_timestamp(
        signal_candle_timestamp, timeframe
    )
    now = _as_utc(now)
    intent_created_at = _as_utc(intent_created_at)
    signal_candle_timestamp = _as_utc(signal_candle_timestamp)
    exec_ts = _as_utc(exec_ts)

    # N+1 execution bar must close before expiry — tolerate scheduler +18/+32 offsets.
    if not is_bar_complete(exec_ts, timeframe, now):
        return False

    deadline = execution_grace_deadline(exec_ts, timeframe, grace_minutes=grace)
    creation_deadline = bar_close_timestamp(exec_ts, timeframe) + timedelta(minutes=tolerance)
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
    grace_minutes: int | None = None,
) -> tuple[bool, str | None]:
    """Return (allowed, rejection_reason) for filling at N+1 open with bounded latency."""
    grace = grace_minutes if grace_minutes is not None else execution_grace_minutes(timeframe)
    stale_max = stale_signal_max_age_minutes(timeframe)

    now = _as_utc(now)
    signal_candle_timestamp = _as_utc(signal_candle_timestamp)
    execution_candle_timestamp = _as_utc(execution_candle_timestamp)
    candle_timestamp = _as_utc(candle_timestamp)

    if execution_candle_timestamp != candle_timestamp:
        return False, "execution_candle_mismatch"

    if not is_bar_complete(execution_candle_timestamp, timeframe, now):
        return False, "execution_bar_not_complete"

    age_min = (now - signal_candle_timestamp).total_seconds() / 60
    if age_min > stale_max:
        return False, f"stale_signal_age ({round(age_min, 1)}m)"

    deadline = execution_grace_deadline(
        execution_candle_timestamp, timeframe, grace_minutes=grace
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
