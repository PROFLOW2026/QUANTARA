"""Owner / broker / asset layer alignment (not closed-position ledger analytics)."""

from __future__ import annotations

from decimal import Decimal

from quantara_engine.live_sim.financial_layers import live_sim_owner_broker_asset_aligned


def test_layers_aligned_when_owner_broker_asset_match():
    owner_eq = Decimal("9835.66")
    owner_real = Decimal("-247.48")
    owner_unreal = Decimal("83.14")
    assert live_sim_owner_broker_asset_aligned(
        owner_equity=owner_eq,
        owner_realized=owner_real,
        owner_unrealized=owner_unreal,
        broker_equity=owner_eq,
        broker_realized=owner_real,
        broker_unrealized=owner_unreal,
        asset_equity=owner_eq,
        asset_realized=owner_real,
        asset_unrealized=owner_unreal,
    )


def test_partial_close_ledger_gap_does_not_break_layer_alignment():
    """Broker realized includes open-position partials; closed-position ledger sum can differ."""
    owner_real = Decimal("-247.48")
    closed_position_ledger_sum = Decimal("-319.16")  # excludes ~64.57 on open MSTR/GBPJPY
    assert owner_real - closed_position_ledger_sum == Decimal("71.68")
    assert live_sim_owner_broker_asset_aligned(
        owner_equity=Decimal("9835.66"),
        owner_realized=owner_real,
        owner_unrealized=Decimal("83.14"),
        broker_equity=Decimal("9835.66"),
        broker_realized=owner_real,
        broker_unrealized=Decimal("83.14"),
        asset_equity=Decimal("9835.66"),
        asset_realized=owner_real,
        asset_unrealized=Decimal("83.14"),
    )


def test_detects_broker_owner_mismatch():
    assert not live_sim_owner_broker_asset_aligned(
        owner_equity=Decimal("100"),
        owner_realized=Decimal("0"),
        owner_unrealized=Decimal("0"),
        broker_equity=Decimal("101"),
        broker_realized=Decimal("0"),
        broker_unrealized=Decimal("0"),
        asset_equity=Decimal("100"),
        asset_realized=Decimal("0"),
        asset_unrealized=Decimal("0"),
    )
