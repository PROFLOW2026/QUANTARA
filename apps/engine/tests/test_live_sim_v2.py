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


def test_robots_a_through_e_are_live():
    from quantara_engine.live_sim.v2_policy import MAIN_LIVE_SIM_STRATEGY_SLUGS

    assert LIVE_SIM_PAUSED_STRATEGY_SLUGS == frozenset()
    core = MAIN_LIVE_SIM_STRATEGY_SLUGS - frozenset(
        {"gold-trend-pullback", "phase3-momentum-intraday", "phase3-volume-breakout", "phase3-mean-reversion-edge"}
    )
    for slug in core:
        for symbol in ("BTCUSD", "NVDA"):
            v = evaluate_live_sim_v2_policy(strategy_slug=slug, symbol=symbol, timeframe="5m")
            assert v.allowed, slug


def test_robot_a_phase3_narrow_universe():
    v = evaluate_live_sim_v2_policy(
        strategy_slug="gold-trend-pullback", symbol="NVDA", timeframe="5m"
    )
    assert not v.allowed
    assert v.reason == "ROBOT_A_PHASE3_NARROW_UNIVERSE"
    assert evaluate_live_sim_v2_policy(
        strategy_slug="gold-trend-pullback", symbol="XAUUSD", timeframe="5m"
    ).allowed


def test_phase3_experiments_allowed_on_equities():
    for slug in (
        "phase3-momentum-intraday",
        "phase3-volume-breakout",
        "phase3-mean-reversion-edge",
    ):
        assert evaluate_live_sim_v2_policy(strategy_slug=slug, symbol="META", timeframe="5m").allowed


def test_v32_slug_requires_qualified_params(monkeypatch):
    monkeypatch.setattr("quantara_engine.live_sim.v2_policy.V32_NEW_ENTRIES_BLOCKED", False)
    from quantara_engine.live_sim.v2_policy import V32_LIVE_SIM_STRATEGY_SLUG

    v = evaluate_live_sim_v2_policy(
        strategy_slug=V32_LIVE_SIM_STRATEGY_SLUG, symbol="COIN", timeframe="15m"
    )
    assert not v.allowed
    assert v.reason == "V32_CANDIDATE_NOT_QUALIFIED"


def test_robot_a_xau_1h_is_live():
    v = evaluate_live_sim_v2_policy(
        strategy_slug="gold-trend-pullback", symbol="XAUUSD", timeframe="1h"
    )
    assert v.allowed


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
    assert "volatility-squeeze" not in LIVE_SIM_PAUSED_STRATEGY_SLUGS


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


def test_owner_stop_budget_and_notional_cap_shrink_xau():
    """A full 1% target that overshoots the owner cap is stepped down, stop unchanged."""
    from decimal import Decimal

    from quantara_engine.domain.types import Direction, Instrument
    from quantara_engine.live_sim.sizing import size_live_sim_entry
    from quantara_engine.portfolio.currency import FxRateTable

    stop = Decimal("4129.16119276")
    res = size_live_sim_entry(
        equity=Decimal("10000"),
        cash=Decimal("10000"),
        target_risk=Decimal("98.71"),
        entry_reference=Decimal("4115.77"),
        stop_loss=stop,
        direction=Direction.SHORT,
        instrument=Instrument(
            id="i",
            symbol="XAUUSD",
            name="XAU",
            asset_class="commodity",
            quote_currency="USD",
            contract_size=Decimal("1"),
            quantity_step=Decimal("0.01"),
            min_quantity=Decimal("0.01"),
        ),
        fx_rates=FxRateTable.usd_only(),
        execution_assumptions=None,
        max_asset_leverage=Decimal("10"),
        hard_max_risk_usd=Decimal("96.82"),
        max_incremental_notional=Decimal("327"),
    )
    assert res.deny_reason is None
    assert res.quantity >= Decimal("0.01")
    assert res.quantity < Decimal("1")
    assert res.expected_risk_usd <= Decimal("96.82")


def test_min_quantity_above_notional_cap_is_rejected():
    from decimal import Decimal

    from quantara_engine.domain.types import Direction, Instrument
    from quantara_engine.live_sim.sizing import size_live_sim_entry
    from quantara_engine.portfolio.currency import FxRateTable

    res = size_live_sim_entry(
        equity=Decimal("10000"),
        cash=Decimal("100000"),
        target_risk=Decimal("98.64"),
        entry_reference=Decimal("209.1835"),
        stop_loss=Decimal("209.04148214"),
        direction=Direction.LONG,
        instrument=Instrument(
            id="i",
            symbol="GBPJPY",
            name="GBPJPY",
            asset_class="forex",
            quote_currency="JPY",
            contract_size=Decimal("1"),
            quantity_step=Decimal("1000"),
            min_quantity=Decimal("1000"),
        ),
        fx_rates=FxRateTable.with_jpy(Decimal("150")),
        execution_assumptions=None,
        max_asset_leverage=Decimal("20"),
        hard_max_risk_usd=Decimal("96.82"),
        max_incremental_notional=Decimal("327"),
    )
    assert res.quantity == 0
    assert res.deny_reason == "MIN_QUANTITY"


def test_v32_audit_since_prefers_observation_anchor():
    from quantara_engine.live_sim.audit_scope import resolve_live_sim_audit_since

    ts = resolve_live_sim_audit_since(
        MagicMock(),
        account_metadata={
            "live_sim_experiment": "v3.2-p2",
            "v32_observation_anchor": "2026-10-01T12:28:17.281564+00:00",
            "baseline_reset_at": "2020-01-01T00:00:00+00:00",
        },
        activated_at=None,
    )
    assert ts is not None
    assert ts.isoformat().startswith("2026-10-01T12:28:17.281564")


def test_release_v32_live_sim_script_model_broker_and_db_driver():
    """Release tooling must stamp live-sim-10k and use psycopg2 SQLAlchemy URLs."""
    from pathlib import Path

    from quantara_engine.broker.accounts import LIVE_SIM_10K_ACCOUNT_SLUG
    from quantara_engine.db.sqlalchemy_url import normalize_sqlalchemy_postgres_url

    root = Path(__file__).resolve().parents[3]
    source = (root / "scripts" / "release_v32_live_sim.py").read_text(encoding="utf-8")
    assert "LIVE_SIM_OWNER_SLUG" not in source
    assert "LIVE_SIM_10K_ACCOUNT_SLUG" in source
    assert "create_engine(normalize_sqlalchemy_postgres_url(db_url()))" in source
    assert LIVE_SIM_10K_ACCOUNT_SLUG == "live-sim-10k"
    assert normalize_sqlalchemy_postgres_url("postgresql://localhost/db").startswith(
        "postgresql+psycopg2://"
    )
