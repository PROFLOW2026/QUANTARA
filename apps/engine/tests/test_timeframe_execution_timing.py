"""Timeframe-aware N+1 execution timing — 5m, 15m, 1h."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from quantara_engine.execution.timing import (
    execution_grace_minutes,
    freshness_max_age_minutes,
    intent_creation_tolerance_minutes,
    intent_past_execution_window,
    live_fill_allowed,
    next_execution_timestamp,
    stale_signal_max_age_minutes,
)


@pytest.mark.parametrize(
    ("timeframe", "tolerance", "grace", "stale", "freshness"),
    [
        ("5m", 5, 8, 15, 30),
        ("15m", 15, 23, 45, 60),
        ("1h", 60, 68, 180, 240),
    ],
)
def test_timeframe_scaled_constants(timeframe, tolerance, grace, stale, freshness):
    assert intent_creation_tolerance_minutes(timeframe) == tolerance
    assert execution_grace_minutes(timeframe) == grace
    assert stale_signal_max_age_minutes(timeframe) == stale
    assert freshness_max_age_minutes(timeframe) == freshness


@pytest.mark.parametrize("timeframe,bar_minutes", [("5m", 5), ("15m", 15), ("1h", 60)])
def test_n_plus_one_open_spacing(timeframe, bar_minutes):
    signal_ts = datetime(2026, 9, 9, 14, 0, tzinfo=timezone.utc)
    exec_ts = next_execution_timestamp(signal_ts, timeframe)
    assert exec_ts == signal_ts + timedelta(minutes=bar_minutes)


@pytest.mark.parametrize("timeframe", ["5m", "15m", "1h"])
def test_on_time_fill_within_grace(timeframe):
    bar = {"5m": 5, "15m": 15, "1h": 60}[timeframe]
    signal_ts = datetime(2026, 9, 9, 14, 0, tzinfo=timezone.utc)
    exec_ts = signal_ts + timedelta(minutes=bar)
    now = exec_ts + timedelta(minutes=bar + 2)
    allowed, reason = live_fill_allowed(
        execution_candle_timestamp=exec_ts,
        candle_timestamp=exec_ts,
        signal_candle_timestamp=signal_ts,
        now=now,
        timeframe=timeframe,
    )
    assert allowed is True
    assert reason is None


@pytest.mark.parametrize("timeframe", ["5m", "15m", "1h"])
def test_genuinely_stale_signal_rejected(timeframe):
    bar = {"5m": 5, "15m": 15, "1h": 60}[timeframe]
    stale = stale_signal_max_age_minutes(timeframe)
    signal_ts = datetime(2026, 9, 9, 14, 0, tzinfo=timezone.utc)
    exec_ts = signal_ts + timedelta(minutes=bar)
    now = signal_ts + timedelta(minutes=stale + 1)
    allowed, reason = live_fill_allowed(
        execution_candle_timestamp=exec_ts,
        candle_timestamp=exec_ts,
        signal_candle_timestamp=signal_ts,
        now=now,
        timeframe=timeframe,
    )
    assert allowed is False
    assert reason and "stale_signal_age" in reason


@pytest.mark.parametrize("timeframe", ["5m", "15m", "1h"])
def test_no_lookahead_rejects_wrong_execution_candle(timeframe):
    bar = {"5m": 5, "15m": 15, "1h": 60}[timeframe]
    signal_ts = datetime(2026, 9, 9, 14, 0, tzinfo=timezone.utc)
    exec_ts = signal_ts + timedelta(minutes=bar)
    allowed, reason = live_fill_allowed(
        execution_candle_timestamp=exec_ts,
        candle_timestamp=signal_ts,
        signal_candle_timestamp=signal_ts,
        now=exec_ts,
        timeframe=timeframe,
    )
    assert allowed is False
    assert reason == "execution_candle_mismatch"


def test_15m_intent_late_creation_expires():
    signal_ts = datetime(2026, 9, 9, 14, 0, tzinfo=timezone.utc)
    exec_ts = datetime(2026, 9, 9, 14, 15, tzinfo=timezone.utc)
    created_at = datetime(2026, 9, 9, 14, 36, tzinfo=timezone.utc)
    now = datetime(2026, 9, 9, 15, 0, tzinfo=timezone.utc)
    assert intent_past_execution_window(
        signal_candle_timestamp=signal_ts,
        execution_candle_timestamp=exec_ts,
        intent_created_at=created_at,
        timeframe="15m",
        now=now,
    )


def test_1h_on_time_fill_after_strategy_cycle_delay():
    signal_ts = datetime(2026, 9, 9, 14, 0, tzinfo=timezone.utc)
    exec_ts = datetime(2026, 9, 9, 15, 0, tzinfo=timezone.utc)
    now = datetime(2026, 9, 9, 16, 10, tzinfo=timezone.utc)
    allowed, reason = live_fill_allowed(
        execution_candle_timestamp=exec_ts,
        candle_timestamp=exec_ts,
        signal_candle_timestamp=signal_ts,
        now=now,
        timeframe="1h",
    )
    assert allowed is True
    assert reason is None
