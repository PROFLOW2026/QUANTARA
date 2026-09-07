"""Tests for competition virtual leverage sizing."""

from decimal import Decimal

from quantara_engine.competition.constants import COMPETITION_PORTFOLIOS
from quantara_engine.competition.leverage import is_competition_portfolio
from quantara_engine.domain.types import Instrument, Mode, Portfolio
from quantara_engine.risk.profiles import get_risk_profile
from quantara_engine.risk.sizing import compute_position_size

ENTRY = Decimal("4404.51")
STOP = Decimal("4419.74")
SL_DISTANCE = abs(ENTRY - STOP)
EQUITY = Decimal("2000")

INSTRUMENT = Instrument(
    id="xauusd",
    symbol="XAUUSD",
    name="Gold",
    asset_class="commodity",
    base_currency="XAU",
    quote_currency="USD",
    pip_size=Decimal("0.01"),
    contract_size=Decimal("100"),
    price_tick_size=Decimal("0.01"),
    quantity_step=Decimal("0.01"),
    min_quantity=Decimal("0.01"),
)


def test_competition_portfolio_ids_registered():
    assert len(COMPETITION_PORTFOLIOS) == 5
    assert is_competition_portfolio(COMPETITION_PORTFOLIOS[0].portfolio_id)


def test_legacy_cap_collapses_higher_tiers():
    portfolio = Portfolio(
        id="legacy",
        name="Legacy",
        mode=Mode.PAPER,
        initial_capital=Decimal("2000"),
        balance=Decimal("2000"),
        equity=Decimal("2000"),
    )
    balanced = get_risk_profile("balanced")
    aggressive = get_risk_profile("aggressive")
    qty_bal, _, actual_bal, _ = compute_position_size(
        portfolio=portfolio,
        risk_profile=balanced,
        instrument=INSTRUMENT,
        entry_reference=ENTRY,
        stop_loss=STOP,
        direction="short",
        open_positions=[],
        mark_price=ENTRY,
        allow_virtual_leverage=False,
    )
    qty_agg, _, actual_agg, _ = compute_position_size(
        portfolio=portfolio,
        risk_profile=aggressive,
        instrument=INSTRUMENT,
        entry_reference=ENTRY,
        stop_loss=STOP,
        direction="short",
        open_positions=[],
        mark_price=ENTRY,
        allow_virtual_leverage=False,
    )
    assert qty_bal == qty_agg
    assert actual_bal == actual_agg
    assert actual_bal < EQUITY * Decimal("0.01")


def test_competition_virtual_leverage_differentiates_tiers():
    results: list[tuple[Decimal, Decimal]] = []
    for entry in COMPETITION_PORTFOLIOS:
        portfolio = Portfolio(
            id=entry.portfolio_id,
            name=entry.name_he,
            mode=Mode.PAPER,
            initial_capital=EQUITY,
            balance=EQUITY,
            equity=EQUITY,
        )
        profile = get_risk_profile(entry.risk_slug)
        qty, target, actual, deny = compute_position_size(
            portfolio=portfolio,
            risk_profile=profile,
            instrument=INSTRUMENT,
            entry_reference=ENTRY,
            stop_loss=STOP,
            direction="short",
            open_positions=[],
            mark_price=ENTRY,
            allow_virtual_leverage=True,
        )
        assert deny is None, deny
        assert actual <= target
        assert actual >= target - Decimal("0.15")
        results.append((qty, actual))

    quantities = [r[0] for r in results]
    assert len(set(quantities)) == 5
    assert quantities == sorted(quantities)
    assert results[-1][0] * SL_DISTANCE >= EQUITY * Decimal("0.02") - Decimal("0.15")
