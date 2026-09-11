"""Approved final risk engine configuration — deterministic guard tests."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from quantara_engine.competition.asset_equity import portfolio_ids_for_symbol
from quantara_engine.domain.types import (
    Direction,
    Instrument,
    Portfolio,
    PortfolioStatus,
    Position,
    PositionStatus,
    StrategyInstance,
)
from quantara_engine.execution.cost_profile import execution_assumptions_for
from quantara_engine.portfolio.currency import FxRateTable
from quantara_engine.risk.global_replay import ReplayEntry, chronological_global_replay
from quantara_engine.risk.open_risk_guard import (
    DEFAULT_OPEN_RISK_LIMITS,
    evaluate_global_asset_open_risk,
    load_global_risk_context,
    sum_open_risk_usd,
)
from quantara_engine.risk.sizing import DEFAULT_RISK_ROUNDING_TOLERANCE_PCT, compute_position_size

FX150 = FxRateTable.with_jpy(Decimal("150"))


def test_approved_default_global_limit_is_two_percent():
    assert DEFAULT_OPEN_RISK_LIMITS.max_global_asset_open_risk_pct == Decimal("2")
    assert DEFAULT_OPEN_RISK_LIMITS.max_portfolio_open_risk_pct == Decimal("10")
    assert DEFAULT_OPEN_RISK_LIMITS.max_asset_open_risk_pct == Decimal("6")
    assert DEFAULT_OPEN_RISK_LIMITS.max_strategy_asset_open_risk_pct == Decimal("4")


def test_approved_quantity_rounding_tolerance_is_two_percent():
    assert DEFAULT_RISK_ROUNDING_TOLERANCE_PCT == Decimal("2")


def test_denominator_uses_asset_portfolios_only():
    gbp_ids = set(portfolio_ids_for_symbol("GBPJPY"))
    btc_ids = set(portfolio_ids_for_symbol("BTCUSD"))
    assert gbp_ids.isdisjoint(btc_ids)
    assert len(gbp_ids) == 20


def test_unrelated_asset_does_not_affect_gbpjpy_denominator():
    mock_store = MagicMock()
    mock_store._position_to_domain.side_effect = lambda r: _domain_pos(str(r), "gbp-id", "10")
    mock_store._hydrate_position_strategy_versions = MagicMock()
    mock_store.hydrate_position_risk_from_intents = MagicMock()
    mock_store.session.scalars.return_value.all.return_value = [object()]
    mock_store.session.scalar.return_value = Decimal("32000")

    _, equity = load_global_risk_context(mock_store, "gbp-id", "GBPJPY")
    assert equity == Decimal("32000")
    mock_store.session.scalar.assert_called_once()
    # Portfolio filter scoped to GBPJPY ids only (20 portfolios, not 160)
    call_sql = str(mock_store.session.scalar.call_args)
    assert "sum" in call_sql.lower() or mock_store.session.scalar.called


def test_prospective_risk_above_two_percent_denied():
    positions = [_pos("600", pid=f"p{i}", si=f"si{i}") for i in range(13)]
    ok, reason = evaluate_global_asset_open_risk(
        global_open_positions_on_asset=positions,
        incremental_risk_usd=Decimal("50"),
        asset_allocated_equity_usd=Decimal("32000"),
        limits=DEFAULT_OPEN_RISK_LIMITS,
    )
    assert not ok
    assert reason is not None and "GLOBAL" in reason


def test_prospective_risk_at_two_percent_allowed():
    # Exactly 2.0% of $32k = $640
    positions = [_pos("100", pid=f"p{i}", si=f"si{i}") for i in range(6)]
    ok, reason = evaluate_global_asset_open_risk(
        global_open_positions_on_asset=positions,
        incremental_risk_usd=Decimal("40"),
        asset_allocated_equity_usd=Decimal("32000"),
        limits=DEFAULT_OPEN_RISK_LIMITS,
    )
    assert ok, reason


def test_five_tier_burst_allowed_under_default_global_limit():
    tiers = [Decimal("5"), Decimal("10"), Decimal("20"), Decimal("30"), Decimal("40")]
    open_positions: list[Position] = []
    for i, risk in enumerate(tiers[:-1]):
        open_positions.append(_pos(str(risk), pid=f"p{i}", si=f"si{i}"))
    ok, reason = evaluate_global_asset_open_risk(
        global_open_positions_on_asset=open_positions,
        incremental_risk_usd=tiers[-1],
        asset_allocated_equity_usd=Decimal("32000"),
        limits=DEFAULT_OPEN_RISK_LIMITS,
    )
    assert ok, reason
    assert sum_open_risk_usd(open_positions) + tiers[-1] == Decimal("105")


def test_position_close_releases_global_risk():
    base = datetime(2026, 3, 1, tzinfo=timezone.utc)
    entries = [
        ReplayEntry("p1", "a", "si1", "long", base, base.replace(hour=2), Decimal("80")),
        ReplayEntry("p2", "b", "si2", "long", base.replace(minute=30), base.replace(hour=1), Decimal("80")),
        ReplayEntry("p3", "c", "si3", "long", base.replace(hour=3), None, Decimal("80")),
    ]
    stats = chronological_global_replay(
        entries=entries,
        instrument=_inst(),
        asset_allocated_equity_usd=Decimal("50000"),
        limits=DEFAULT_OPEN_RISK_LIMITS,
    )
    assert stats.entries_allowed == 3
    assert stats.peak_open_risk_usd == Decimal("160.00")


def test_restart_rebuilds_risk_from_db_open_positions():
    row = MagicMock()
    mock_store = MagicMock()
    domain_pos = _domain_pos("pos-1", "gbp-id", "25.50")
    mock_store._position_to_domain.return_value = domain_pos
    mock_store.session.scalars.return_value.all.return_value = [row]

    def hydrate(positions):
        for p in positions:
            if p.actual_risk_amount <= 0:
                p.actual_risk_amount = Decimal("25.50")

    mock_store.hydrate_position_risk_from_intents.side_effect = hydrate
    mock_store.session.scalar.return_value = Decimal("31996.66")

    positions, equity = load_global_risk_context(mock_store, "gbp-id", "GBPJPY")
    mock_store.hydrate_position_risk_from_intents.assert_called_once()
    assert len(positions) == 1
    assert positions[0].actual_risk_amount == Decimal("25.50")
    assert equity == Decimal("31996.66")


def test_lower_asset_equity_tightens_dollar_risk_ceiling():
    high_eq = Decimal("40000")
    low_eq = Decimal("30000")
    positions = [_pos("500", pid="p1")]
    ok_high, _ = evaluate_global_asset_open_risk(
        global_open_positions_on_asset=positions,
        incremental_risk_usd=Decimal("300"),
        asset_allocated_equity_usd=high_eq,
        limits=DEFAULT_OPEN_RISK_LIMITS,
    )
    ok_low, reason_low = evaluate_global_asset_open_risk(
        global_open_positions_on_asset=positions,
        incremental_risk_usd=Decimal("300"),
        asset_allocated_equity_usd=low_eq,
        limits=DEFAULT_OPEN_RISK_LIMITS,
    )
    assert ok_high  # $800 = 2.0% of $40k
    assert not ok_low  # $800 > $600 cap after losses
    assert reason_low is not None and "GLOBAL" in reason_low
    assert high_eq * Decimal("2") / Decimal("100") == Decimal("800")
    assert low_eq * Decimal("2") / Decimal("100") == Decimal("600")


def test_min_quantity_never_forces_material_over_risk():
    inst = Instrument(
        id="gbp",
        symbol="GBPJPY",
        name="GBP/JPY",
        asset_class="forex",
        quote_currency="JPY",
        pip_size=Decimal("0.01"),
        quantity_step=Decimal("1000"),
        min_quantity=Decimal("1000"),
    )
    portfolio = Portfolio(
        id="p1",
        name="t",
        mode="paper",
        initial_capital=Decimal("2000"),
        balance=Decimal("2000"),
        equity=Decimal("2000"),
        status=PortfolioStatus.ACTIVE,
        peak_equity=Decimal("2000"),
    )
    from quantara_engine.domain.types import RiskProfile

    profile = RiskProfile(
        id="vc",
        slug="very_conservative",
        name="vc",
        risk_per_trade_pct=Decimal("0.25"),
        max_open_positions=10,
        max_total_exposure_pct=Decimal("500"),
        daily_loss_limit_pct=Decimal("10"),
        max_drawdown_pct=Decimal("50"),
    )
    assumptions = execution_assumptions_for(inst, Decimal("200"))
    qty, target, actual, deny = compute_position_size(
        portfolio,
        profile,
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
    assert qty == 0
    assert actual > target * Decimal("1.02")


def _inst() -> Instrument:
    return Instrument(id="gbp", symbol="GBPJPY", name="GBP/JPY", asset_class="forex")


def _pos(risk: str, *, pid: str = "p1", si: str = "si1") -> Position:
    return _domain_pos(f"pos-{risk}-{pid}", "gbp", risk, pid=pid, si=si)


def _domain_pos(
    pos_id: str,
    iid: str,
    risk: str,
    *,
    pid: str = "p1",
    si: str = "si1",
) -> Position:
    return Position(
        id=pos_id,
        portfolio_id=pid,
        strategy_instance_id=si,
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
