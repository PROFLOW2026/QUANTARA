"""Counterfactual global asset risk replay."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from quantara_engine.domain.types import Instrument
from quantara_engine.risk.global_replay import ReplayEntry, chronological_global_replay
from quantara_engine.risk.open_risk_guard import OpenRiskLimits


def _inst() -> Instrument:
    return Instrument(id="gbp", symbol="GBPJPY", name="GBP/JPY", asset_class="forex")


def test_simulated_peak_below_uncontrolled_when_global_blocks():
    """Guarded replay peak must differ from historical when global limit binds."""
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    entries = [
        ReplayEntry("p1", "port-a", "si-a", "long", base, base.replace(hour=5), Decimal("400")),
        ReplayEntry("p2", "port-b", "si-b", "long", base.replace(minute=1), base.replace(hour=5), Decimal("400")),
        ReplayEntry("p3", "port-c", "si-c", "long", base.replace(minute=2), base.replace(hour=5), Decimal("400")),
    ]
    limits = OpenRiskLimits(
        max_portfolio_open_risk_pct=Decimal("100"),
        max_asset_open_risk_pct=Decimal("100"),
        max_strategy_asset_open_risk_pct=Decimal("100"),
        max_global_asset_open_risk_pct=Decimal("10"),
    )
    stats = chronological_global_replay(
        entries=entries,
        instrument=_inst(),
        asset_allocated_equity_usd=Decimal("10000"),
        limits=limits,
    )
    assert stats.max_simultaneous_positions == 2
    assert stats.peak_open_risk_usd == Decimal("800.00")
    assert stats.global_denials >= 1
    assert stats.entries_allowed == 2


def test_denied_entry_not_in_simulated_state():
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    entries = [
        ReplayEntry("p1", "port-a", "si-a", "long", base, None, Decimal("500")),
        ReplayEntry("p2", "port-b", "si-b", "long", base.replace(minute=1), None, Decimal("600")),
    ]
    limits = OpenRiskLimits(
        max_portfolio_open_risk_pct=Decimal("100"),
        max_asset_open_risk_pct=Decimal("100"),
        max_strategy_asset_open_risk_pct=Decimal("100"),
        max_global_asset_open_risk_pct=Decimal("20"),
    )
    stats = chronological_global_replay(
        entries=entries,
        instrument=_inst(),
        asset_allocated_equity_usd=Decimal("10000"),
        limits=limits,
    )
    assert stats.entries_allowed == 2
    assert stats.peak_open_risk_usd == Decimal("1100.00")
