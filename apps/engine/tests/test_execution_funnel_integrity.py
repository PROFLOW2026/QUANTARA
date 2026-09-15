"""Execution funnel integrity — stale alignment and accepted limbo prevention."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from quantara_engine.execution.timing import (
    freshness_max_age_minutes,
    is_terminal_execution_rejection,
    live_fill_allowed,
    stale_signal_max_age_minutes,
)
from quantara_engine.live_sim.allocator import _resume_pending_allocation
from quantara_engine.live_sim.candidate_log import (
    allocation_lifecycle_state,
    reconcile_accepted_limbo_allocations,
)


def test_freshness_matches_fill_stale_gate_all_timeframes():
    for tf in ("5m", "15m", "1h"):
        assert freshness_max_age_minutes(tf) == stale_signal_max_age_minutes(tf)


def test_fresh_5m_opportunity_can_fill_on_normal_cadence():
    signal_ts = datetime(2026, 9, 15, 10, 0, tzinfo=timezone.utc)
    exec_ts = datetime(2026, 9, 15, 10, 5, tzinfo=timezone.utc)
    now = datetime(2026, 9, 15, 10, 12, tzinfo=timezone.utc)
    allowed, reason = live_fill_allowed(
        execution_candle_timestamp=exec_ts,
        candle_timestamp=exec_ts,
        signal_candle_timestamp=signal_ts,
        now=now,
        timeframe="5m",
    )
    assert allowed is True
    assert reason is None


def test_catchup_older_than_stale_is_terminal():
    signal_ts = datetime(2026, 9, 15, 10, 0, tzinfo=timezone.utc)
    exec_ts = datetime(2026, 9, 15, 10, 5, tzinfo=timezone.utc)
    now = datetime(2026, 9, 15, 10, 16, tzinfo=timezone.utc)
    allowed, reason = live_fill_allowed(
        execution_candle_timestamp=exec_ts,
        candle_timestamp=exec_ts,
        signal_candle_timestamp=signal_ts,
        now=now,
        timeframe="5m",
    )
    assert allowed is False
    assert is_terminal_execution_rejection(reason)


def test_resume_pending_terminalizes_stale_signal():
    store = MagicMock()
    existing = {
        "id": "log-1",
        "accepted": True,
        "broker_order_id": None,
        "live_sim_position_id": None,
        "metadata": {"pending_execution": True},
        "signal_candle_timestamp": datetime(2026, 9, 15, 10, 0, tzinfo=timezone.utc),
        "timeframe": "5m",
        "direction": "long",
        "symbol": "COIN",
        "proposed_entry": Decimal("200"),
        "stop_loss": Decimal("195"),
        "take_profit": Decimal("210"),
    }
    exec_ts = datetime(2026, 9, 15, 10, 5, tzinfo=timezone.utc)
    exec_candle = MagicMock(timestamp=exec_ts, close=Decimal("201"))
    now = datetime(2026, 9, 15, 10, 16, tzinfo=timezone.utc)

    with patch(
        "quantara_engine.live_sim.allocator.resolve_execution_candle",
        return_value=(exec_candle, exec_ts),
    ):
        with patch(
            "quantara_engine.live_sim.allocator.is_execution_candle_ready",
            return_value=(False, "stale_signal_age (16.0m)"),
        ):
            with patch("quantara_engine.live_sim.candidate_log.mark_allocation_rejected") as reject:
                result = _resume_pending_allocation(
                    store,
                    existing=existing,
                    account_id="acc-1",
                    account={"id": "acc-1", "equity": "10000", "starting_cash": "10000"},
                    entry={"instance": MagicMock()},
                    instrument=MagicMock(symbol="COIN"),
                    candles=[exec_candle],
                    execution_now=now,
                )
    assert result["status"] == "rejected"
    assert result["reason"] == "STALE_SIGNAL"
    reject.assert_called_once()


def test_accepted_limbo_lifecycle_detected():
    row = {
        "accepted": True,
        "broker_order_id": None,
        "live_sim_position_id": None,
        "metadata": {"lifecycle_state": "executing"},
    }
    assert allocation_lifecycle_state(row) == "pending_execution"


def test_reconcile_accepted_limbo_terminalizes_stale_rows():
    store = MagicMock()
    signal_ts = datetime(2026, 9, 15, 10, 0, tzinfo=timezone.utc)
    row = {
        "id": "log-1",
        "signal_candle_timestamp": signal_ts,
        "timeframe": "5m",
        "created_at": signal_ts,
        "metadata": {"lifecycle_state": "executing"},
        "symbol": "COIN",
        "direction": "long",
    }
    store.session.execute.return_value.mappings.return_value.all.return_value = [row]
    now = datetime(2026, 9, 15, 10, 20, tzinfo=timezone.utc)

    with patch("quantara_engine.live_sim.candidate_log.mark_allocation_rejected") as reject:
        count = reconcile_accepted_limbo_allocations(store, now)
    assert count == 1
    reject.assert_called_once()
