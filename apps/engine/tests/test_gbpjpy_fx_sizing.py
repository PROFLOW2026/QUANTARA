"""GBPJPY FX sizing — risk-to-stop in USD account (canonical quote=JPY)."""

from decimal import Decimal

import pytest

from quantara_engine.domain.types import Instrument, Mode, Portfolio, RiskProfile
from quantara_engine.portfolio.currency import FxRateTable
from quantara_engine.risk.sizing import compute_position_size, fx_risk_usd

FX_150 = FxRateTable.with_jpy(Decimal("150"))


def _gbpjpy_instrument() -> Instrument:
    return Instrument(
        id="gbp",
        symbol="GBPJPY",
        name="GBP/JPY",
        asset_class="forex",
        base_currency="GBP",
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
        name="Test",
        mode=Mode.PAPER,
        initial_capital=Decimal(equity),
        balance=Decimal(equity),
        equity=Decimal(equity),
    )


def _risk(pct: str) -> RiskProfile:
    return RiskProfile(
        id="r1",
        slug="balanced",
        name="Balanced",
        risk_per_trade_pct=Decimal(pct),
        max_open_positions=8,
        max_total_exposure_pct=Decimal("500"),
        daily_loss_limit_pct=Decimal("5"),
        max_drawdown_pct=Decimal("20"),
    )


@pytest.mark.parametrize(
    "risk_pct,expected_target,expect_deny",
    [
        ("0.25", Decimal("5"), True),
        ("0.5", Decimal("10"), True),
        ("1", Decimal("20"), False),
        ("1.5", Decimal("30"), False),
        ("2", Decimal("40"), False),
    ],
)
def test_gbpjpy_risk_tiers_respect_budget(risk_pct, expected_target, expect_deny):
    from quantara_engine.execution.cost_profile import execution_assumptions_for

    instrument = _gbpjpy_instrument()
    portfolio = _portfolio("2000")
    entry = Decimal("200.000")
    stop = Decimal("198.000")
    assumptions = execution_assumptions_for(instrument, entry)
    qty, target, actual, denial = compute_position_size(
        portfolio,
        _risk(risk_pct),
        instrument,
        entry,
        stop,
        "long",
        [],
        entry,
        allow_virtual_leverage=True,
        fx_rates=FX_150,
        execution_assumptions=assumptions,
    )
    assert target == expected_target
    if expect_deny:
        assert denial == "MIN_QUANTITY_EXCEEDS_RISK_BUDGET"
        assert qty == 0
    else:
        assert denial is None
        assert qty >= instrument.min_quantity
        assert actual <= target * Decimal("1.02")


def test_gbpjpy_one_pct_uses_min_when_ceil_over_budget():
    from quantara_engine.execution.cost_profile import execution_assumptions_for

    instrument = _gbpjpy_instrument()
    portfolio = _portfolio("2000")
    entry = Decimal("200.000")
    stop = Decimal("198.000")
    assumptions = execution_assumptions_for(instrument, entry)
    qty, _, _, denial = compute_position_size(
        portfolio,
        _risk("1"),
        instrument,
        entry,
        stop,
        "long",
        [],
        entry,
        allow_virtual_leverage=True,
        fx_rates=FX_150,
        execution_assumptions=assumptions,
    )
    assert denial is None
    assert qty == Decimal("1000")


def test_fx_risk_usd_jpy_quote():
    instrument = _gbpjpy_instrument()
    risk = fx_risk_usd(Decimal("1000"), Decimal("1.50"), instrument, FX_150)
    assert risk == Decimal("10.00")
