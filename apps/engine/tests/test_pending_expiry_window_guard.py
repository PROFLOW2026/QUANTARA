"""Stale pending expiry must not expire intents still inside the execution window."""

from datetime import datetime, timedelta, timezone

from quantara_engine.execution.timing import intent_past_execution_window


def test_fresh_intent_inside_grace_not_past_window():
    now = datetime(2026, 11, 7, 18, 10, tzinfo=timezone.utc)
    signal = datetime(2026, 11, 7, 18, 0, tzinfo=timezone.utc)
    execution = datetime(2026, 11, 7, 18, 5, tzinfo=timezone.utc)
    created = datetime(2026, 11, 7, 18, 5, 20, tzinfo=timezone.utc)
    assert not intent_past_execution_window(
        signal_candle_timestamp=signal,
        execution_candle_timestamp=execution,
        intent_created_at=created,
        timeframe="5m",
        now=now,
    )


def test_old_intent_after_grace_is_past_window():
    now = datetime(2026, 11, 7, 19, 0, tzinfo=timezone.utc)
    signal = datetime(2026, 11, 7, 18, 0, tzinfo=timezone.utc)
    execution = datetime(2026, 11, 7, 18, 5, tzinfo=timezone.utc)
    created = datetime(2026, 11, 7, 18, 5, 20, tzinfo=timezone.utc)
    assert intent_past_execution_window(
        signal_candle_timestamp=signal,
        execution_candle_timestamp=execution,
        intent_created_at=created,
        timeframe="5m",
        now=now,
    )


def test_execution_bar_not_complete_never_expires():
    now = datetime(2026, 11, 7, 18, 6, tzinfo=timezone.utc)
    signal = datetime(2026, 11, 7, 18, 0, tzinfo=timezone.utc)
    execution = datetime(2026, 11, 7, 18, 5, tzinfo=timezone.utc)
    created = now - timedelta(seconds=10)
    # 5m bar opened 18:05 is not complete until 18:10.
    assert not intent_past_execution_window(
        signal_candle_timestamp=signal,
        execution_candle_timestamp=execution,
        intent_created_at=created,
        timeframe="5m",
        now=now,
    )
