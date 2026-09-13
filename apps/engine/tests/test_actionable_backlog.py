"""Actionable strategy backlog counter tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from quantara_engine.domain.types import Candle
from quantara_engine.execution.catch_up import (
    aggregate_actionable_backlog,
    compute_actionable_backlog_status,
    compute_backlog_status,
)
from quantara_workers.jobs.run_strategy import _timeframe_status_from_window, strategy_freshness_summary


COMPETITION_START = datetime(2026, 9, 12, 10, 11, 1, tzinfo=timezone.utc)


def _candle(ts: datetime, px: str = "100") -> Candle:
    price = Decimal(px)
    return Candle(
        instrument_id="i1",
        timeframe="5m",
        timestamp=ts,
        open=price,
        high=price,
        low=price,
        close=price,
    )


def _series(start: datetime, count: int, *, minutes: int = 5) -> list[Candle]:
    return [
        _candle(start + timedelta(minutes=minutes * i), str(100 + i))
        for i in range(count)
    ]


def test_pre_competition_unprocessed_candles_do_not_count():
    start = datetime(2026, 9, 10, 12, 0, tzinfo=timezone.utc)
    candles = _series(start, 20)
    now = candles[-1].timestamp + timedelta(minutes=10)
    status = compute_actionable_backlog_status(
        candles,
        "5m",
        set(),
        now,
        competition_floor=COMPETITION_START,
    )
    assert status["backlog"] == 0
    assert status["live_actionable"] == 0
    assert status["historical_actionable"] == 0


def test_post_competition_unprocessed_completed_candle_counts():
    start = datetime(2026, 9, 12, 10, 0, tzinfo=timezone.utc)
    candles = _series(start, 6)
    now = candles[-1].timestamp + timedelta(minutes=10)
    processed = {candles[i].timestamp for i in range(4)}
    status = compute_actionable_backlog_status(
        candles,
        "5m",
        processed,
        now,
        competition_floor=COMPETITION_START,
    )
    assert status["backlog"] == 2
    assert status["live_actionable"] == 1
    assert status["historical_actionable"] == 1


def test_fully_processed_group_has_zero_actionable():
    start = datetime(2026, 9, 12, 11, 0, tzinfo=timezone.utc)
    candles = _series(start, 8)
    now = candles[-1].timestamp + timedelta(minutes=10)
    processed = {c.timestamp for c in candles}
    status = compute_actionable_backlog_status(
        candles,
        "5m",
        processed,
        now,
        competition_floor=COMPETITION_START,
    )
    assert status["backlog"] == 0
    assert status["live_actionable"] == 0
    assert status["historical_actionable"] == 0


def test_ineligible_group_reports_zero_actionable():
    start = datetime(2026, 9, 12, 11, 0, tzinfo=timezone.utc)
    candles = _series(start, 8)
    now = candles[-1].timestamp + timedelta(minutes=10)
    status = compute_actionable_backlog_status(
        candles,
        "5m",
        set(),
        now,
        competition_floor=COMPETITION_START,
        eligible=False,
    )
    assert status["backlog"] == 0
    raw = compute_backlog_status(candles, "5m", last_processed=None, now=now)
    assert raw["backlog"] > 0


def test_multiple_strategies_pending_count_executable_units():
    start = datetime(2026, 9, 12, 11, 0, tzinfo=timezone.utc)
    candles = _series(start, 5)
    now = candles[-1].timestamp + timedelta(minutes=10)
    processed_a = {candles[i].timestamp for i in range(3)}
    processed_b = {candles[i].timestamp for i in range(4)}
    status_a = compute_actionable_backlog_status(
        candles, "5m", processed_a, now, competition_floor=COMPETITION_START
    )
    status_b = compute_actionable_backlog_status(
        candles, "5m", processed_b, now, competition_floor=COMPETITION_START
    )
    live, historical, total = aggregate_actionable_backlog(
        {"robot_a": status_a, "robot_b": status_b}
    )
    assert status_a["live_actionable"] == 1
    assert status_b["live_actionable"] == 1
    assert live == 2
    assert total == status_a["backlog"] + status_b["backlog"]


def test_cde_15m_reconciles_with_jobs_pending():
    timeframe_status = {
        "5m": {"live_actionable": 0, "historical_actionable": 0},
        "orb_5m": {"live_actionable": 0, "historical_actionable": 0},
        "15m": {"live_actionable": 1, "historical_actionable": 2},
        "1h": {"live_actionable": 0, "historical_actionable": 1},
        "cde_15m": {"live_actionable": 1, "historical_actionable": 3},
    }
    live, historical, total = aggregate_actionable_backlog(timeframe_status)
    assert live == 2
    assert historical == 6
    assert total == 8


def test_large_pre_comp_window_does_not_inflate_actionable_backlog():
    start = datetime(2026, 3, 1, 12, 0, tzinfo=timezone.utc)
    candles = _series(start, 500)
    now = candles[-1].timestamp + timedelta(minutes=10)
    raw = compute_backlog_status(candles, "5m", last_processed=None, now=now)
    actionable = compute_actionable_backlog_status(
        candles,
        "5m",
        set(),
        now,
        competition_floor=COMPETITION_START,
    )
    assert raw["backlog"] == 500
    assert actionable["backlog"] == 0


def test_timeframe_status_from_window_matches_actionable_helper():
    start = datetime(2026, 9, 12, 11, 0, tzinfo=timezone.utc)
    candles = _series(start, 10)
    now = candles[-1].timestamp + timedelta(minutes=10)
    processed = {candles[i].timestamp for i in range(6)}
    shared = _timeframe_status_from_window(
        candles,
        "5m",
        processed,
        now,
        competition_floor=COMPETITION_START,
    )
    direct = compute_actionable_backlog_status(
        candles,
        "5m",
        processed,
        now,
        competition_floor=COMPETITION_START,
    )
    assert shared["backlog"] == direct["backlog"]
    assert shared["live_actionable"] == direct["live_actionable"]
    assert shared["historical_actionable"] == direct["historical_actionable"]


def test_strategy_freshness_summary_ignores_legacy_raw_backlog():
    from unittest.mock import MagicMock, patch

    now = datetime(2026, 9, 13, 12, 0, tzinfo=timezone.utc)
    store = MagicMock()
    store.get_settings_dict.return_value = {
        "worker_status:strategy_runner": {
            "status": "catching_up",
            "last_evaluation_at": (now - timedelta(minutes=1)).isoformat(),
            "jobs_pending": 5414,
            "timeframes": {
                "5m": {"backlog": 1256},
                "orb_5m": {"backlog": 1505},
                "15m": {"backlog": 1253},
                "1h": {"backlog": 1400},
                "cde_15m": {"backlog": 1504},
            },
        },
        "worker_status:data_fetcher": {"status": "healthy"},
    }
    store.get_instrument_by_symbol.return_value = MagicMock(
        id="00000000-0000-4000-8000-000000000001"
    )
    with patch(
        "quantara_engine.persistence.batch_summary.batch_latest_candle_timestamps",
        return_value={"00000000-0000-4000-8000-000000000001": now - timedelta(minutes=5)},
    ):
        summary = strategy_freshness_summary(store, now)
    assert summary["live_backlog"] == 0
    assert summary["historical_backlog"] == 0
    assert summary["backlog"] == 0


def test_strategy_freshness_summary_sums_actionable_fields():
    from unittest.mock import MagicMock, patch

    now = datetime(2026, 9, 13, 12, 0, tzinfo=timezone.utc)
    store = MagicMock()
    store.get_settings_dict.return_value = {
        "worker_status:strategy_runner": {
            "status": "catching_up",
            "last_evaluation_at": (now - timedelta(minutes=1)).isoformat(),
            "jobs_pending": 5,
            "jobs_live_pending": 2,
            "jobs_historical_pending": 3,
            "timeframes": {
                "5m": {"live_actionable": 1, "historical_actionable": 1},
                "orb_5m": {"live_actionable": 1, "historical_actionable": 0},
                "15m": {"live_actionable": 0, "historical_actionable": 1},
                "1h": {"live_actionable": 0, "historical_actionable": 1},
                "cde_15m": {"live_actionable": 0, "historical_actionable": 0},
            },
        },
        "worker_status:data_fetcher": {"status": "healthy"},
    }
    store.get_instrument_by_symbol.return_value = MagicMock(
        id="00000000-0000-4000-8000-000000000001"
    )
    with patch(
        "quantara_engine.persistence.batch_summary.batch_latest_candle_timestamps",
        return_value={"00000000-0000-4000-8000-000000000001": now - timedelta(minutes=5)},
    ):
        summary = strategy_freshness_summary(store, now)
    assert summary["live_backlog"] == 2
    assert summary["historical_backlog"] == 3
    assert summary["backlog"] == 5
