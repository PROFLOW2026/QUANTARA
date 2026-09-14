"""Targeted Live Sim lifecycle integrity tests (containment, entry/close authority)."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from quantara_engine.broker.execution_service import BrokerExecutionResult
from quantara_engine.live_sim.entry_authority import (
    live_sim_physical_entry_succeeded,
)
from quantara_engine.live_sim.integrity_containment import (
    activate_live_sim_entry_containment,
    is_live_sim_entries_blocked,
    release_live_sim_entry_containment,
)
from quantara_engine.learning.planned_vs_actual import compute_risk_observation


def _entry_res(**kwargs) -> BrokerExecutionResult:
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


def test_a_accepted_no_fill_not_physical_entry():
    assert live_sim_physical_entry_succeeded(None) is False
    assert live_sim_physical_entry_succeeded(_entry_res(accepted=False)) is False
    assert live_sim_physical_entry_succeeded(_entry_res(shadow_only=True)) is False
    assert live_sim_physical_entry_succeeded(
        _entry_res(fill_quantity=Decimal("0"), physical_opened_qty=Decimal("0"))
    ) is False
    assert live_sim_physical_entry_succeeded(
        _entry_res(broker_fill_id=None, broker_order_id=None)
    ) is False


def test_b_physical_entry_succeeds():
    assert live_sim_physical_entry_succeeded(_entry_res()) is True


def test_containment_blocks_entries():
    store = MagicMock()
    store.get_settings_dict.return_value = {}
    store.update_settings = MagicMock()
    activate_live_sim_entry_containment(store, reason="test")
    assert store.update_settings.called
    store.get_settings_dict.return_value = {
        "live_sim_integrity_containment": {"block_new_entries": True}
    }
    assert is_live_sim_entries_blocked(store) is True
    release_live_sim_entry_containment(store)
    store.get_settings_dict.return_value = {
        "live_sim_integrity_containment": {"block_new_entries": False}
    }
    assert is_live_sim_entries_blocked(store) is False


def test_i_gbpjpy_usd_risk_measurement():
    jpy_per_usd = Decimal("150")
    qty = Decimal("7000")
    entry = Decimal("208.54")
    sl = Decimal("208.78")
    obs = compute_risk_observation(
        direction="short",
        signal_candle_close=entry,
        planned_entry_ref=entry,
        execution_candle_open=entry,
        actual_fill_price=entry,
        planned_sl=sl,
        planned_tp=Decimal("208.27"),
        quantity=qty,
        equity_at_entry=Decimal("1250"),
        planned_risk_usd=Decimal("12.31"),
        symbol="GBPJPY",
        fx_rates={"JPY": jpy_per_usd},
    )
    assert obs["actual_risk_usd"] is not None
    assert obs["actual_risk_usd"] < 20
    assert obs["risk_overrun"] is False


def test_j_broker_hwm_monotonic():
    from quantara_engine.live_sim.risk_policy import update_high_water_mark

    store = MagicMock()
    session = MagicMock()
    store.session = session
    session.execute.return_value.scalar.return_value = {"high_water_mark": 7500.0}
    hwm = update_high_water_mark(store, "acct", Decimal("7512.46"))
    assert hwm == Decimal("7512.46")
    assert session.execute.call_count == 2


def test_allocator_skips_when_containment_active():
    from quantara_engine.live_sim.allocator import maybe_allocate_live_sim

    store = MagicMock()
    store.get_settings_dict.return_value = {
        "live_sim_integrity_containment": {"block_new_entries": True}
    }
    out = maybe_allocate_live_sim(
        store,
        entry={"instance": SimpleNamespace(timeframe="5m", strategy_slug="robot-a")},
        instrument=SimpleNamespace(symbol="BTCUSD"),
        candle=SimpleNamespace(timestamp=datetime.now(timezone.utc), close=Decimal("1")),
        candles=[],
        candle_index=0,
        signal=None,
        execution_now=datetime.now(timezone.utc),
    )
    assert out["reason"] == "live_sim_integrity_containment"
