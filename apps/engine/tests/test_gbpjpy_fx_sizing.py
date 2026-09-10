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
    "risk_pct,expected_target,expected_actual",
    [
        ("0.25", Decimal("5"), Decimal("10")),
        ("0.5", Decimal("10"), Decimal("10")),
        ("1", Decimal("20"), Decimal("20")),
        ("1.5", Decimal("30"), Decimal("30")),
        ("2", Decimal("40"), Decimal("40")),
    ],
)
def test_gbpjpy_risk_tiers_approximate_target(risk_pct, expected_target, expected_actual):
    instrument = _gbpjpy_instrument()
    portfolio = _portfolio("2000")
    entry = Decimal("200.000")
    stop = Decimal("198.500")
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
    )
    assert denial is None
    assert qty >= instrument.min_quantity
    assert target == expected_target
    assert abs(actual - expected_actual) <= Decimal("1.00")


def test_gbpjpy_not_denied_below_minimum():
    instrument = _gbpjpy_instrument()
    portfolio = _portfolio("2000")
    entry = Decimal("200.000")
    stop = Decimal("198.500")
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
    )
    assert denial is None
    assert qty == Decimal("2000")


def test_fx_risk_usd_jpy_quote():
    instrument = _gbpjpy_instrument()
    risk = fx_risk_usd(Decimal("1000"), Decimal("1.50"), instrument, FX_150)
    assert risk == Decimal("10.00")
