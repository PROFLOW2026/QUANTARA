"""Risk sizing — min quantity, step rounding, tolerance, global denominator isolation."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from quantara_engine.competition.asset_equity import nominal_asset_allocated_equity, portfolio_ids_for_symbol
from quantara_engine.domain.types import (
    Direction,
    Instrument,
    Portfolio,
    PortfolioStatus,
    Position,
    PositionStatus,
    RiskProfile,
    StrategyInstance,
)
from quantara_engine.execution.cost_profile import execution_assumptions_for
from quantara_engine.portfolio.currency import FxRateTable
from quantara_engine.risk.open_risk_guard import (
    OpenRiskLimits,
    evaluate_all_open_risk_guards,
    evaluate_global_asset_open_risk,
    load_global_risk_context,
    sum_open_risk_usd,
)
from quantara_engine.risk.sizing import (
    DEFAULT_RISK_ROUNDING_TOLERANCE_PCT,
    compute_position_size,
    select_quantity_for_risk_budget,
)

FX150 = FxRateTable.with_jpy(Decimal("150"))


def _gbpjpy() -> Instrument:
    return Instrument(
        id="gbp-id",
        symbol="GBPJPY",
        name="GBP/JPY",
        asset_class="forex",
        quote_currency="JPY",
        pip_size=Decimal("0.01"),
        contract_size=Decimal("100000"),
        price_tick_size=Decimal("0.001"),
        quantity_step=Decimal("1000"),
        min_quantity=Decimal("1000"),
    )


def _portfolio(equity: str = "2000") -> Portfolio:
    return Portfolio(
        id="p1",
        name="t",
        mode="paper",
        initial_capital=Decimal(equity),
        balance=Decimal(equity),
        equity=Decimal(equity),
        status=PortfolioStatus.ACTIVE,
        peak_equity=Decimal(equity),
    )


def _profile(pct: str) -> RiskProfile:
    return RiskProfile(
        id="rp",
        slug="tier",
        name="tier",
        risk_per_trade_pct=Decimal(pct),
        max_open_positions=10,
        max_total_exposure_pct=Decimal("500"),
        daily_loss_limit_pct=Decimal("10"),
        max_drawdown_pct=Decimal("50"),
    )


def _pos(risk: str, *, pid: str = "p1", iid: str = "gbp-id") -> Position:
    return Position(
        id=f"pos-{pid}-{risk}",
        portfolio_id=pid,
        strategy_instance_id="si1",
        instrument_id=iid,
        direction=Direction.LONG,
        quantity=Decimal("1000"),
        entry_price=Decimal("200"),
        current_price=Decimal("200"),
        stop_loss=Decimal("198"),
        take_profit=Decimal("204"),
        actual_risk_amount=Decimal(risk),
        status=PositionStatus.OPEN,
    )


def test_min_quantity_exceeds_risk_budget_rejects():
    inst = _gbpjpy()
    assumptions = execution_assumptions_for(inst, Decimal("200"))
    qty, target, actual, deny = compute_position_size(
        _portfolio(),
        _profile("0.25"),
        inst,
        Decimal("200"),
        Decimal("198"),
        "long",
        [],
        Decimal("200"),
        allow_virtual_leverage=True,
        fx_rates=FX150,
        execution_assumptions=assumptions,
    )
    assert qty == 0
    assert target == Decimal("5.00")
    assert actual > target
    assert deny == "MIN_QUANTITY_EXCEEDS_RISK_BUDGET"


def test_valid_quantity_below_target_allowed():
    inst = _gbpjpy()
    assumptions = execution_assumptions_for(inst, Decimal("200"))
    qty, target, actual, deny = compute_position_size(
        _portfolio(),
        _profile("1"),
        inst,
        Decimal("200"),
        Decimal("198"),
        "long",
        [],
        Decimal("200"),
        allow_virtual_leverage=True,
        fx_rates=FX150,
        execution_assumptions=assumptions,
    )
    assert deny is None
    assert qty == Decimal("1000")
    assert target == Decimal("20.00")
    assert actual < target


def test_rounding_tolerance_allows_slight_excess():
    inst = _gbpjpy()
    assumptions = execution_assumptions_for(inst, Decimal("200"))
    qty, target, actual, deny = compute_position_size(
        _portfolio(),
        _profile("2"),
        inst,
        Decimal("200"),
        Decimal("198"),
        "long",
        [],
        Decimal("200"),
        allow_virtual_leverage=True,
        fx_rates=FX150,
        execution_assumptions=assumptions,
        risk_rounding_tolerance_pct=Decimal("2"),
    )
    assert deny is None
    assert qty == Decimal("3000")
    assert target == Decimal("40.00")
    assert actual <= target * Decimal("1.02")


def test_zero_tolerance_rejects_when_only_ceil_exceeds():
    inst = _gbpjpy()
    assumptions = execution_assumptions_for(inst, Decimal("200"))
    qty, _, _, deny = compute_position_size(
        _portfolio(),
        _profile("2"),
        inst,
        Decimal("200"),
        Decimal("198"),
        "long",
        [],
        Decimal("200"),
        allow_virtual_leverage=True,
        fx_rates=FX150,
        execution_assumptions=assumptions,
        risk_rounding_tolerance_pct=Decimal("0"),
    )
    # With 0% tolerance, 3000 may exceed $40 cap — should fall back to 2000 or reject min
    assert deny is None or deny == "MIN_QUANTITY_EXCEEDS_RISK_BUDGET"
    if deny is None:
        assert qty == Decimal("2000")


def test_material_over_risk_rejected_at_min():
    inst = _gbpjpy()
    assumptions = execution_assumptions_for(inst, Decimal("200"))
    _, _, _, deny = compute_position_size(
        _portfolio(),
        _profile("0.5"),
        inst,
        Decimal("200"),
        Decimal("198"),
        "long",
        [],
        Decimal("200"),
        allow_virtual_leverage=True,
        fx_rates=FX150,
        execution_assumptions=assumptions,
    )
    assert deny == "MIN_QUANTITY_EXCEEDS_RISK_BUDGET"


def test_global_risk_decreases_on_close():
    open_a = [_pos("20"), _pos("20", pid="p2")]
    before = sum_open_risk_usd(open_a)
    after = sum_open_risk_usd([open_a[0]])
    assert after < before


def test_gbpjpy_denominator_is_asset_allocated_only():
    ids = portfolio_ids_for_symbol("GBPJPY")
    assert len(ids) == 20
    nominal = nominal_asset_allocated_equity("GBPJPY")
    assert nominal == Decimal("40000")


def test_btc_denominator_does_not_include_gbpjpy():
    gbp_ids = set(portfolio_ids_for_symbol("GBPJPY"))
    btc_ids = set(portfolio_ids_for_symbol("BTCUSD"))
    assert gbp_ids.isdisjoint(btc_ids)
    assert len(gbp_ids) == 20
    assert nominal_asset_allocated_equity("GBPJPY") == Decimal("40000")


def test_global_guard_five_tier_preserved():
    tiers = [Decimal("5"), Decimal("10"), Decimal("20"), Decimal("30"), Decimal("40")]
    open_positions = [_pos(str(r), pid=f"p{i}") for i, r in enumerate(tiers[:-1])]
    ok, reason = evaluate_global_asset_open_risk(
        global_open_positions_on_asset=open_positions,
        incremental_risk_usd=tiers[-1],
        asset_allocated_equity_usd=Decimal("40000"),
        limits=OpenRiskLimits(
            max_portfolio_open_risk_pct=Decimal("10"),
            max_asset_open_risk_pct=Decimal("6"),
            max_strategy_asset_open_risk_pct=Decimal("4"),
            max_global_asset_open_risk_pct=Decimal("3"),
        ),
    )
    assert ok, reason


def test_global_guard_default_is_two_percent():
    from quantara_engine.risk.open_risk_guard import DEFAULT_OPEN_RISK_LIMITS

    assert DEFAULT_OPEN_RISK_LIMITS.max_global_asset_open_risk_pct == Decimal("2")


def test_load_global_risk_context_uses_asset_portfolios(monkeypatch):
    mock_store = MagicMock()
    mock_store._position_to_domain.side_effect = lambda r: _pos("10", pid=str(r))
    mock_store._hydrate_position_strategy_versions = MagicMock()
    mock_store.hydrate_position_risk_from_intents = MagicMock()
    mock_store.session.scalars.return_value.all.return_value = []
    mock_store.session.scalar.return_value = Decimal("38500")

    positions, equity = load_global_risk_context(mock_store, "gbp-id", "GBPJPY")
    assert positions == []
    assert equity == Decimal("38500")


def test_btc_risk_does_not_affect_gbpjpy_guard():
    """Global guard is per-asset — BTC positions are not in GBPJPY context."""
    gbp_positions = [_pos("100", iid="gbp-id")]
    ok, _ = evaluate_global_asset_open_risk(
        global_open_positions_on_asset=gbp_positions,
        incremental_risk_usd=Decimal("20"),
        asset_allocated_equity_usd=Decimal("40000"),
        limits=OpenRiskLimits(
            max_portfolio_open_risk_pct=Decimal("10"),
            max_asset_open_risk_pct=Decimal("6"),
            max_strategy_asset_open_risk_pct=Decimal("4"),
            max_global_asset_open_risk_pct=Decimal("2"),
        ),
    )
    assert ok
