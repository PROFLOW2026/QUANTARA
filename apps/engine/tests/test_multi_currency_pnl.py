"""Multi-currency P&L — quote currency → USD account currency."""

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from quantara_engine.domain.types import (
    Direction,
    ExitReason,
    Instrument,
    Mode,
    Portfolio,
    PortfolioStatus,
    Position,
    PositionStatus,
)
from quantara_engine.domain.types import ExecutionAssumptions
from quantara_engine.execution.fill_calculator import calculate_fill_price
from quantara_engine.portfolio.currency import FxRateTable, quote_currencies_for_instruments
from quantara_engine.portfolio.pnl import gross_pnl, raw_pnl_in_quote, unrealized_pnl
from quantara_engine.portfolio.service import PortfolioState
from quantara_engine.risk.sizing import compute_position_size, fx_risk_usd


def _gbpjpy() -> Instrument:
    return Instrument(
        id="gbp-id",
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


def _btcusd() -> Instrument:
    return Instrument(
        id="btc-id",
        symbol="BTCUSD",
        name="BTC/USD",
        asset_class="crypto",
        base_currency="BTC",
        quote_currency="USD",
    )


def _xauusd() -> Instrument:
    return Instrument(
        id="xau-id",
        symbol="XAUUSD",
        name="XAU/USD",
        asset_class="commodity",
        base_currency="XAU",
        quote_currency="USD",
    )


FX_150 = FxRateTable.with_jpy(Decimal("150"))


@pytest.mark.parametrize(
    "direction,entry,mark,qty,raw_jpy,expected_usd",
    [
        (Direction.LONG, Decimal("200"), Decimal("201.5"), Decimal("1000"), Decimal("1500"), Decimal("10.00")),
        (Direction.LONG, Decimal("200"), Decimal("198.5"), Decimal("1000"), Decimal("-1500"), Decimal("-10.00")),
        (Direction.SHORT, Decimal("200"), Decimal("198.5"), Decimal("1000"), Decimal("1500"), Decimal("10.00")),
        (Direction.SHORT, Decimal("200"), Decimal("201.5"), Decimal("1000"), Decimal("-1500"), Decimal("-10.00")),
    ],
)
def test_gbpjpy_unrealized_converts_jpy_to_usd(direction, entry, mark, qty, raw_jpy, expected_usd):
    inst = _gbpjpy()
    assert raw_pnl_in_quote(direction, entry, mark, qty) == raw_jpy
    assert unrealized_pnl(direction, entry, mark, qty, inst, FX_150) == expected_usd


@pytest.mark.parametrize(
    "direction,entry,exit_,qty,expected_usd",
    [
        (Direction.LONG, Decimal("208.804"), Decimal("207.304"), Decimal("4000"), Decimal("-40.00")),
        (Direction.SHORT, Decimal("208.804"), Decimal("210.304"), Decimal("4000"), Decimal("-40.00")),
    ],
)
def test_gbpjpy_realized_gross_converts(direction, entry, exit_, qty, expected_usd):
    inst = _gbpjpy()
    assert gross_pnl(direction, entry, exit_, qty, inst, FX_150) == expected_usd


def test_usd_quoted_instruments_unchanged():
    inst = _btcusd()
    assert unrealized_pnl(
        Direction.LONG, Decimal("100"), Decimal("105"), Decimal("1"), inst, FxRateTable.usd_only()
    ) == Decimal("5.00")
    assert gross_pnl(
        Direction.LONG, Decimal("100"), Decimal("105"), Decimal("1"), inst, FxRateTable.usd_only()
    ) == Decimal("5.00")
    xau = _xauusd()
    assert unrealized_pnl(
        Direction.LONG, Decimal("2000"), Decimal("2010"), Decimal("2"), xau, FxRateTable.usd_only()
    ) == Decimal("20.00")


def test_gbpjpy_metadata_quote_currency():
    inst = _gbpjpy()
    assert inst.base_currency == "GBP"
    assert inst.quote_currency == "JPY"
    assert "JPY" in quote_currencies_for_instruments([inst])


def test_fx_rate_table_conversion():
    rates = FxRateTable.with_jpy(Decimal("150"))
    assert rates.quote_to_account(Decimal("1500"), "JPY") == Decimal("10.00")
    assert rates.quote_to_account(Decimal("10"), "USD") == Decimal("10.00")


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
def test_gbpjpy_risk_tiers_match_sizing(risk_pct, expected_target, expected_actual):
    from quantara_engine.domain.types import RiskProfile

    inst = _gbpjpy()
    portfolio = Portfolio(
        id="p1",
        name="Test",
        mode=Mode.PAPER,
        initial_capital=Decimal("2000"),
        balance=Decimal("2000"),
        equity=Decimal("2000"),
    )
    risk = RiskProfile(
        id="r1",
        slug="balanced",
        name="Balanced",
        risk_per_trade_pct=Decimal(risk_pct),
        max_open_positions=8,
        max_total_exposure_pct=Decimal("500"),
        daily_loss_limit_pct=Decimal("5"),
        max_drawdown_pct=Decimal("20"),
    )
    entry = Decimal("200.000")
    stop = Decimal("198.500")
    qty, target, actual, denial = compute_position_size(
        portfolio,
        risk,
        inst,
        entry,
        stop,
        "long",
        [],
        entry,
        allow_virtual_leverage=True,
        fx_rates=FX_150,
    )
    assert denial is None
    assert qty >= inst.min_quantity
    assert target == expected_target
    assert abs(actual - expected_actual) <= Decimal("1.00")
    assert fx_risk_usd(qty, abs(entry - stop), inst, FX_150) == actual


def test_portfolio_aggregation_gbpjpy_usd():
    from quantara_engine.portfolio.currency import CurrencyContext

    inst = _gbpjpy()
    ctx = CurrencyContext({inst.id: inst}, FX_150)
    portfolio = Portfolio(
        id="p1",
        name="GBP/JPY 15m",
        mode=Mode.PAPER,
        initial_capital=Decimal("2000"),
        balance=Decimal("2000"),
        equity=Decimal("2000"),
    )
    positions = [
        Position(
            id="pos1",
            portfolio_id="p1",
            strategy_instance_id="si1",
            instrument_id=inst.id,
            direction=Direction.LONG,
            quantity=Decimal("4000"),
            entry_price=Decimal("208.80427334"),
            stop_loss=Decimal("207"),
            take_profit=None,
            current_price=Decimal("208.80427334"),
            status=PositionStatus.OPEN,
            opened_at=datetime.now(timezone.utc),
        ),
        Position(
            id="pos2",
            portfolio_id="p1",
            strategy_instance_id="si1",
            instrument_id=inst.id,
            direction=Direction.LONG,
            quantity=Decimal("8000"),
            entry_price=Decimal("208.80427334"),
            stop_loss=Decimal("207"),
            take_profit=None,
            current_price=Decimal("208.80427334"),
            status=PositionStatus.OPEN,
            opened_at=datetime.now(timezone.utc),
        ),
    ]
    state = PortfolioState(portfolio=portfolio, positions=positions)
    state.recalculate_equity({inst.id: Decimal("208.62067")}, ctx)
    assert state.portfolio.unrealized_pnl < Decimal("-10")
    assert state.portfolio.unrealized_pnl > Decimal("-20")
    assert sum(p.unrealized_pnl for p in state.open_positions()) == state.portfolio.unrealized_pnl


def test_gbpjpy_close_realized_usd_balance():
    from quantara_engine.portfolio.currency import CurrencyContext

    inst = _gbpjpy()
    ctx = CurrencyContext({inst.id: inst}, FX_150)
    portfolio = Portfolio(
        id="p1",
        name="Test",
        mode=Mode.PAPER,
        initial_capital=Decimal("2000"),
        balance=Decimal("2000"),
        equity=Decimal("2000"),
    )
    pos = Position(
        id="pos1",
        portfolio_id="p1",
        strategy_instance_id="si1",
        instrument_id=inst.id,
        direction=Direction.LONG,
        quantity=Decimal("4000"),
        entry_price=Decimal("200"),
        stop_loss=Decimal("198.5"),
        take_profit=None,
        current_price=Decimal("200"),
        status=PositionStatus.OPEN,
        opened_at=datetime.now(timezone.utc),
        strategy_version_id="v1",
    )
    state = PortfolioState(portfolio=portfolio, positions=[pos])
    fill = calculate_fill_price(
        Direction.LONG, "exit", Decimal("198.5"), Decimal("4000"), ExecutionAssumptions()
    )
    trade = state.close_position(pos, fill, ExitReason.SL, datetime.now(timezone.utc), ctx)
    expected = gross_pnl(
        Direction.LONG,
        Decimal("200"),
        fill.fill_price,
        Decimal("4000"),
        inst,
        FX_150,
    )
    assert trade.gross_pnl == expected
    assert trade.realized_pnl == expected
    assert portfolio.balance == Decimal("2000") + expected
