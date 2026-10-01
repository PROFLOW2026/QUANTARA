"""Live Sim V2 — robot policy and owner-level risk sizing."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from quantara_engine.competition.robot_registry import ROBOT_LABELS
from quantara_engine.domain.types import Direction, Instrument
from quantara_engine.live_sim.v2_policy import (
    LIVE_SIM_PAUSED_STRATEGY_SLUGS,
    evaluate_live_sim_v2_policy,
)
from quantara_engine.live_sim.sizing import size_live_sim_entry
from quantara_engine.portfolio.currency import FxRateTable


def test_robot_d_e_paused_in_live_policy():
    for slug in ("volatility-squeeze", "momentum-continuation"):
        v = evaluate_live_sim_v2_policy(strategy_slug=slug, symbol="BTCUSD", timeframe="5m")
        assert not v.allowed
        assert v.reason == "ROBOT_LIVE_PAUSED"


def test_robot_a_b_paused_in_live_policy():
    for slug in ("gold-trend-pullback", "opening-range-breakout"):
        v = evaluate_live_sim_v2_policy(strategy_slug=slug, symbol="BTCUSD", timeframe="5m")
        assert not v.allowed
        assert v.reason == "ROBOT_LIVE_PAUSED"


def test_robot_c_paused_in_live_policy():
    v = evaluate_live_sim_v2_policy(strategy_slug="mean-reversion", symbol="BTCUSD", timeframe="5m")
    assert not v.allowed
    assert v.reason == "ROBOT_LIVE_PAUSED"


def test_coin_live_blocked_under_v32_slug():
    from quantara_engine.live_sim.v2_policy import V32_LIVE_SIM_STRATEGY_SLUG

    v = evaluate_live_sim_v2_policy(
        strategy_slug=V32_LIVE_SIM_STRATEGY_SLUG, symbol="COIN", timeframe="15m"
    )
    assert not v.allowed


def test_robot_a_paused_even_on_blocked_combo_xau_1h():
    v = evaluate_live_sim_v2_policy(
        strategy_slug="gold-trend-pullback", symbol="XAUUSD", timeframe="1h"
    )
    assert not v.allowed
    assert v.reason == "ROBOT_LIVE_PAUSED"


def test_owner_equity_produces_larger_target_risk_than_asset_slice():
    """1% of ~10k owner equity should exceed 1% of ~1250 asset slice."""
    instrument = Instrument(
        id="i",
        symbol="BTCUSD",
        name="BTC/USD",
        asset_class="crypto",
        quote_currency="USD",
        quantity_step=Decimal("0.00001"),
        min_quantity=Decimal("0.00001"),
    )
    target_owner = Decimal("100")  # ~1% of 10k
    target_asset = Decimal("12.5")  # ~1% of 1250
    fx = FxRateTable.usd_only()
    entry = Decimal("60000")
    sl = Decimal("59000")
    res_owner = size_live_sim_entry(
        equity=Decimal("10000"),
        cash=Decimal("5000"),
        target_risk=target_owner,
        entry_reference=entry,
        stop_loss=sl,
        direction=Direction.LONG,
        instrument=instrument,
        fx_rates=fx,
        execution_assumptions=None,
        max_asset_leverage=Decimal("5"),
    )
    res_asset = size_live_sim_entry(
        equity=Decimal("1250"),
        cash=Decimal("1250"),
        target_risk=target_asset,
        entry_reference=entry,
        stop_loss=sl,
        direction=Direction.LONG,
        instrument=instrument,
        fx_rates=fx,
        execution_assumptions=None,
        max_asset_leverage=Decimal("5"),
        hard_max_risk_usd=Decimal("12.5"),
    )
    assert res_owner.quantity >= res_asset.quantity
    assert (res_owner.expected_risk_usd or 0) >= (res_asset.expected_risk_usd or 0)


def test_paused_robots_remain_in_registry():
    assert "volatility-squeeze" in ROBOT_LABELS
    assert "volatility-squeeze" in LIVE_SIM_PAUSED_STRATEGY_SLUGS


def test_funnel_category_maps_min_qty_and_containment():
    from quantara_engine.live_sim.funnel_categories import funnel_category

    assert funnel_category("MIN_QUANTITY_EXCEEDS_RISK_BUDGET") == "MIN_QTY"
    assert funnel_category("live_sim_integrity_containment") == "CONTAINMENT"
    assert funnel_category("SYMBOL_SL_RISK_LIMIT") == "RISK_LIMIT"


def test_hard_max_risk_clamps_quantity():
    from decimal import Decimal

    from quantara_engine.domain.types import Direction, Instrument
    from quantara_engine.live_sim.sizing import size_live_sim_entry
    from quantara_engine.portfolio.currency import FxRateTable

    instrument = Instrument(
        id="i",
        symbol="BTCUSD",
        name="BTC",
        asset_class="crypto",
        quote_currency="USD",
        quantity_step=Decimal("0.00001"),
        min_quantity=Decimal("0.00001"),
    )
    fx = FxRateTable.usd_only()
    res = size_live_sim_entry(
        equity=Decimal("10000"),
        cash=Decimal("10000"),
        target_risk=Decimal("100"),
        entry_reference=Decimal("60000"),
        stop_loss=Decimal("59000"),
        direction=Direction.LONG,
        instrument=instrument,
        fx_rates=fx,
        execution_assumptions=None,
        hard_max_risk_usd=Decimal("5"),
    )
    assert (res.expected_risk_usd or 0) <= Decimal("5.01")
