"""Egress regression — steady-state DB read budget after candle cache warmup."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from quantara_engine.domain.types import Candle
from quantara_engine.execution.catch_up import list_catchup_candle_indices
from quantara_engine.market_data.candle_working_set import (
    WorkerCandleCache,
    reset_worker_candle_cache,
)
from quantara_engine.persistence.egress_metrics import EgressMetrics
from quantara_workers.jobs.snapshot import _snapshot_materially_changed


def _candle(i: int, instrument_id: str = "inst1", timeframe: str = "5m") -> Candle:
    ts = datetime(2026, 9, 1, tzinfo=timezone.utc) + timedelta(minutes=5 * i)
    price = Decimal("100") + Decimal(i)
    return Candle(
        instrument_id=instrument_id,
        timeframe=timeframe,
        timestamp=ts,
        open=price,
        high=price + 1,
        low=price - 1,
        close=price,
        volume=Decimal("1"),
    )


def test_candle_cache_warm_then_incremental_tail_only():
    reset_worker_candle_cache()
    cache = WorkerCandleCache()
    store = MagicMock()
    lookback = 250
    warm = [_candle(i) for i in range(lookback)]
    store.list_recent_candles.return_value = warm
    first = cache.get_window(store, "inst1", "5m", lookback=lookback)
    assert len(first) == lookback
    assert store.list_recent_candles.call_count == 1

    store.list_candles_after.return_value = [_candle(lookback)]
    second = cache.get_window(store, "inst1", "5m", lookback=lookback)
    assert len(second) == lookback
    store.list_candles_after.assert_called_once()
    store.list_recent_candles.assert_called_once()


def test_bounded_processed_timestamps_match_catchup_in_window():
    candles = [_candle(i) for i in range(20)]
    now = candles[-1].timestamp + timedelta(minutes=10)
    instance_ids = ["a", "b", "c", "d", "e"]
    full_processed = {candles[i].timestamp for i in range(0, 15)}
    window_start = candles[0].timestamp
    bounded_processed = {ts for ts in full_processed if ts >= window_start}

    full_indices = list_catchup_candle_indices(
        candles,
        "5m",
        last_processed=None,
        now=now,
        processed_timestamps=full_processed,
    )
    bounded_indices = list_catchup_candle_indices(
        candles,
        "5m",
        last_processed=None,
        now=now,
        processed_timestamps=bounded_processed,
    )
    assert full_indices == bounded_indices


def test_steady_state_cycle_candle_row_budget_after_warmup():
    """288 normal cycles: returned candle rows grow with new bars, not history depth."""
    from tests.egress_test_support import IncrementalCandleBackingStore

    reset_worker_candle_cache()
    cache = WorkerCandleCache()
    lookback = 250
    backing = IncrementalCandleBackingStore()
    base = [_candle(i) for i in range(lookback)]
    backing.seed("inst1", "5m", base)

    cache.get_window(backing, "inst1", "5m", lookback=lookback)
    warm_rows = backing.egress_metrics.candle_rows
    warm_recent_calls = backing.egress_metrics.query_labels.get("list_recent_candles", 0)

    per_cycle_rows: list[int] = []
    latest_ts = base[-1].timestamp
    for cycle in range(288):
        backing.egress_metrics.reset()
        new_row = _candle(lookback + cycle)
        backing.append("inst1", "5m", new_row)
        cache.get_window(backing, "inst1", "5m", lookback=lookback)
        per_cycle_rows.append(backing.egress_metrics.candle_rows)
        assert backing.egress_metrics.candle_rows <= 1
        assert new_row.timestamp > latest_ts
        latest_ts = new_row.timestamp

    assert warm_rows == lookback
    assert warm_recent_calls == 1
    assert max(per_cycle_rows) == 1
    assert sum(per_cycle_rows) == 288


def test_snapshot_skips_unchanged_portfolio():
    started = datetime(2026, 9, 12, 12, 0, tzinfo=timezone.utc)
    portfolio = MagicMock(
        balance=Decimal("2000"),
        equity=Decimal("2000"),
        unrealized_pnl=Decimal("0"),
        exposure_notional=Decimal("0"),
    )
    last = {
        "timestamp": started - timedelta(minutes=10),
        "balance": Decimal("2000"),
        "equity": Decimal("2000"),
        "unrealized_pnl": Decimal("0"),
        "exposure_notional": Decimal("0"),
        "open_positions_count": 0,
    }
    assert not _snapshot_materially_changed(portfolio, [], last, started)


def test_batch_runtime_state_has_no_history_lists():
    from quantara_engine.portfolio.service import PortfolioState

    state = PortfolioState(portfolio=MagicMock(), positions=[], trades=[], snapshots=[])
    assert state.trades == []
    assert state.snapshots == []
