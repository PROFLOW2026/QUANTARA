"""Prove strategy catch-up/output equivalence with shared candle window."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from quantara_engine.domain.types import Candle
from quantara_engine.execution.catch_up import compute_backlog_status, list_catchup_candle_indices
from quantara_workers.jobs.run_strategy import _timeframe_status_from_window


def _candles(n: int) -> list[Candle]:
    out: list[Candle] = []
    for i in range(n):
        ts = datetime(2026, 9, 1, tzinfo=timezone.utc) + timedelta(minutes=5 * i)
        px = Decimal("100") + Decimal(i)
        out.append(
            Candle(
                instrument_id="i1",
                timeframe="5m",
                timestamp=ts,
                open=px,
                high=px,
                low=px,
                close=px,
            )
        )
    return out


def test_shared_window_status_matches_db_status_shape():
    candles = _candles(30)
    now = candles[-1].timestamp + timedelta(minutes=10)
    processed = {candles[i].timestamp for i in range(10)}
    shared = _timeframe_status_from_window(candles, "5m", processed, now)
    direct = compute_backlog_status(
        candles,
        "5m",
        last_processed=max(processed),
        now=now,
    )
    assert shared["backlog"] == direct["backlog"]
    assert shared["status"] == direct["status"]


def test_catchup_indices_unchanged_with_window_bound_processed():
    candles = _candles(40)
    now = candles[-1].timestamp + timedelta(minutes=10)
    processed_full = {candles[i].timestamp for i in range(25)}
    window_start = candles[0].timestamp
    processed_bounded = {ts for ts in processed_full if ts >= window_start}
    kwargs = dict(
        candles=candles,
        timeframe="5m",
        last_processed=None,
        now=now,
        processed_timestamps=processed_full,
    )
    full = list_catchup_candle_indices(**kwargs)
    bounded = list_catchup_candle_indices(
        **{**kwargs, "processed_timestamps": processed_bounded}
    )
    assert full == bounded
