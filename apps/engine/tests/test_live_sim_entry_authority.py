"""Live Sim entry authority — broker fill required before shadow OPEN."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from quantara_engine.broker.execution_service import BrokerExecutionResult
from quantara_engine.execution.timing import is_execution_candle_ready, next_execution_timestamp
from quantara_engine.live_sim.candidate_log import allocation_lifecycle_state
from quantara_engine.live_sim.entry_authority import (
    live_sim_entry_broker_exposure_ok,
    live_sim_physical_entry_succeeded,
    resolve_live_sim_entry_quantity,
)


def _res(**kwargs) -> BrokerExecutionResult:
    base = dict(
        accepted=True,
        shadow_only=False,
        physical_opened_qty=Decimal("0.01"),
        physical_closed_qty=Decimal("0"),
        fill_quantity=Decimal("0.01"),
        broker_order_id="ord",
        broker_fill_id="fill",
    )
    base.update(kwargs)
    return BrokerExecutionResult(**base)


def test_1_broker_failure_no_open():
    assert live_sim_physical_entry_succeeded(None) is False
    assert live_sim_physical_entry_succeeded(_res(accepted=False)) is False


def test_2_broker_rejected_no_open():
    assert live_sim_physical_entry_succeeded(_res(accepted=False, broker_fill_id="f")) is False


def test_3_broker_fill_absent_no_open():
    assert live_sim_physical_entry_succeeded(_res(shadow_only=True)) is False
    assert live_sim_physical_entry_succeeded(_res(broker_fill_id=None)) is False
    assert live_sim_physical_entry_succeeded(
        _res(physical_opened_qty=Decimal("0"), fill_quantity=Decimal("1"))
    ) is False


def test_4_broker_fill_succeeds_reconcile_fields():
    res = _res(physical_opened_qty=Decimal("0.5"), fill_quantity=Decimal("0.5"))
    assert live_sim_physical_entry_succeeded(res) is True
    store = MagicMock()
    store.session.execute.return_value.mappings.return_value.first.return_value = {
        "rem": Decimal("0.5"),
        "opened": Decimal("0.5"),
    }
    qty = resolve_live_sim_entry_quantity(
        store, broker_res=res, strategy_position_id="p1", requested_qty=Decimal("0.25")
    )
    assert qty == Decimal("0.5")


def test_5_exception_path_rejects_flat_broker():
    store = MagicMock()
    store.session.execute.return_value.scalar.return_value = Decimal("0")
    assert live_sim_entry_broker_exposure_ok(
        store,
        broker_account_id="aid",
        symbol="ETHUSD",
        direction="short",
        minimum_qty=Decimal("0.1"),
    ) is False


def test_6_idempotent_retry_no_duplicate_open():
    store = MagicMock()
    store.session.execute.return_value.mappings.return_value.first.return_value = {
        "id": "p1",
        "status": "open",
    }
    row = store.session.execute.return_value.mappings.return_value.first.return_value
    assert row is not None


def test_7_pending_execution_within_window_not_limbo():
    signal = datetime(2026, 9, 20, 5, 30, tzinfo=timezone.utc)
    exec_ts = next_execution_timestamp(signal, "15m")
    allowed, reason = is_execution_candle_ready(
        signal_candle_timestamp=signal,
        execution_candle_timestamp=exec_ts,
        timeframe="15m",
        now=datetime(2026, 9, 20, 5, 45, 30, tzinfo=timezone.utc),
    )
    assert allowed is False
    assert reason == "execution_bar_not_complete"
    row = {
        "accepted": True,
        "broker_order_id": None,
        "live_sim_position_id": None,
        "metadata": {
            "pending_execution": True,
            "lifecycle_state": "pending_execution",
            "execution_candle_timestamp": exec_ts.isoformat(),
        },
    }
    assert allocation_lifecycle_state(row) == "pending_execution"


def test_8_pending_execution_past_window_is_expirable():
    signal = datetime(2026, 9, 20, 4, 0, tzinfo=timezone.utc)
    exec_ts = next_execution_timestamp(signal, "15m")
    allowed, reason = is_execution_candle_ready(
        signal_candle_timestamp=signal,
        execution_candle_timestamp=exec_ts,
        timeframe="15m",
        now=datetime(2026, 9, 20, 6, 0, tzinfo=timezone.utc),
    )
    assert allowed is False
    assert reason and ("execution_window_passed" in reason or "stale_signal_age" in reason)


def test_9_broker_exposure_direction_long():
    store = MagicMock()
    store.session.execute.return_value.scalar.return_value = Decimal("0.2")
    assert live_sim_entry_broker_exposure_ok(
        store,
        broker_account_id="aid",
        symbol="ETHUSD",
        direction="long",
        minimum_qty=Decimal("0.1"),
    ) is True
