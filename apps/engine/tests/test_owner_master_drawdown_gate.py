"""Owner Master drawdown gate — multi-broker slices must not false-trigger DRAWDOWN_GATE."""

from __future__ import annotations

import json
from decimal import Decimal
from unittest.mock import patch

import pytest
from sqlalchemy import text

from quantara_engine.broker.accounts import (
    LIVE_SIM_IBKR_LIKE_SLUG,
    LIVE_SIM_KRAKEN_LIKE_SLUG,
)
from quantara_engine.live_sim.owner_master_risk import (
    evaluate_owner_master_drawdown_gate,
    load_owner_master_risk_settings,
    owner_master_equity,
)
from quantara_engine.live_sim.risk_policy import evaluate_drawdown_gate
from quantara_engine.owner_portfolio.asset_allocation import (
    configure_equal_asset_allocations,
    deactivate_equal_asset_allocations,
    is_equal_asset_mode_active,
)
from quantara_engine.owner_portfolio.service import LIVE_SIM_OWNER_SLUG


@pytest.fixture
def multi_broker_owner(broker_test_store):
    store = broker_test_store
    if is_equal_asset_mode_active(store, LIVE_SIM_OWNER_SLUG):
        deactivate_equal_asset_allocations(store, LIVE_SIM_OWNER_SLUG)
    result = configure_equal_asset_allocations(store, LIVE_SIM_OWNER_SLUG, activate=True)
    assert result["ok"], result
    try:
        yield store
    finally:
        store.session.rollback()
        deactivate_equal_asset_allocations(store, LIVE_SIM_OWNER_SLUG)


def _set_broker_slice(store, slug: str, equity: Decimal, hwm: Decimal, starting: Decimal):
    patch_rs = json.dumps({"high_water_mark": float(hwm)})
    store.session.execute(
        text(
            """
            UPDATE broker_accounts
            SET equity = :eq, starting_cash = :start,
                risk_settings = COALESCE(risk_settings, '{}'::jsonb) || CAST(:patch AS jsonb)
            WHERE slug = :slug
            """
        ),
        {"slug": slug, "eq": float(equity), "start": float(starting), "patch": patch_rs},
    )


def _set_owner_hwm(store, hwm: Decimal, target: Decimal = Decimal("10000")):
    patch_rs = json.dumps({"high_water_mark": float(hwm)})
    store.session.execute(
        text(
            """
            UPDATE owner_trading_portfolios
            SET target_capital = :target,
                risk_settings = COALESCE(risk_settings, '{}'::jsonb) || CAST(:patch AS jsonb)
            WHERE slug = :slug
            """
        ),
        {"slug": LIVE_SIM_OWNER_SLUG, "target": float(target), "patch": patch_rs},
    )


def test_slice_7500_2500_vs_10k_hwm_false_broker_drawdown():
    """Legacy bug: comparing slice equity to $10K HWM."""
    ibkr = evaluate_drawdown_gate(
        equity=Decimal("7500"),
        high_water_mark=Decimal("10000"),
        max_drawdown_gate_pct=Decimal("10"),
        scope_label="broker drawdown",
    )
    kraken = evaluate_drawdown_gate(
        equity=Decimal("2500"),
        high_water_mark=Decimal("10000"),
        max_drawdown_gate_pct=Decimal("10"),
        scope_label="broker drawdown",
    )
    assert not ibkr.allowed
    assert not kraken.allowed


def test_owner_aggregate_10k_hwm_10k_zero_drawdown(multi_broker_owner):
    store = multi_broker_owner
    _set_broker_slice(store, LIVE_SIM_IBKR_LIKE_SLUG, Decimal("7500"), Decimal("10000"), Decimal("7500"))
    _set_broker_slice(store, LIVE_SIM_KRAKEN_LIKE_SLUG, Decimal("2500"), Decimal("10000"), Decimal("2500"))
    _set_owner_hwm(store, Decimal("10000"))
    store.session.commit()

    agg = owner_master_equity(store)
    assert agg == Decimal("10000")

    gate = evaluate_owner_master_drawdown_gate(store)
    assert gate.allowed, gate.detail


def test_owner_master_11pct_drawdown_triggers_gate(multi_broker_owner):
    store = multi_broker_owner
    _set_owner_hwm(store, Decimal("10000"))
    store.session.execute(
        text(
            """
            UPDATE broker_accounts
            SET equity = CASE
                WHEN slug = :ibkr THEN :ibkr_eq
                WHEN slug = :kraken THEN :kraken_eq
                ELSE equity
            END
            WHERE slug IN (:ibkr, :kraken)
            """
        ),
        {
            "ibkr": LIVE_SIM_IBKR_LIKE_SLUG,
            "kraken": LIVE_SIM_KRAKEN_LIKE_SLUG,
            "ibkr_eq": 6675.0,
            "kraken_eq": 2225.0,
        },
    )
    store.session.commit()

    assert owner_master_equity(store) == Decimal("8900")
    settings = load_owner_master_risk_settings(store)
    settings = type(settings)(
        risk_per_trade_pct=settings.risk_per_trade_pct,
        max_total_open_sl_risk_pct=settings.max_total_open_sl_risk_pct,
        max_symbol_sl_risk_pct=settings.max_symbol_sl_risk_pct,
        max_group_sl_risk_pct=settings.max_group_sl_risk_pct,
        daily_loss_gate_pct=settings.daily_loss_gate_pct,
        max_drawdown_gate_pct=Decimal("10"),
        concentration_mode=settings.concentration_mode,
        high_water_mark=Decimal("10000"),
        daily_start_equity=settings.daily_start_equity,
        daily_start_date=settings.daily_start_date,
    )
    gate = evaluate_drawdown_gate(
        equity=Decimal("8900"),
        high_water_mark=settings.high_water_mark,
        max_drawdown_gate_pct=settings.max_drawdown_gate_pct,
        scope_label="owner master drawdown",
    )
    assert not gate.allowed
    assert gate.reason == "DRAWDOWN_GATE"


def test_hwm_rises_with_equity_then_dd_from_peak(multi_broker_owner):
    store = multi_broker_owner
    _set_owner_hwm(store, Decimal("10000"))
    store.session.commit()
    with patch(
        "quantara_engine.live_sim.owner_master_risk.owner_master_equity",
        return_value=Decimal("10500"),
    ):
        gate = evaluate_owner_master_drawdown_gate(store)
    assert gate.allowed

    gate_fall = evaluate_drawdown_gate(
        equity=Decimal("9975"),
        high_water_mark=Decimal("10500"),
        max_drawdown_gate_pct=Decimal("10"),
        scope_label="owner master drawdown",
    )
    dd = (Decimal("10500") - Decimal("9975")) / Decimal("10500") * Decimal("100")
    assert dd == Decimal("5")
    assert gate_fall.allowed
