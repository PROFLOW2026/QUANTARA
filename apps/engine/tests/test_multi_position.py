"""Multi-position Paper trading — owner-approved simultaneous positions."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock

from quantara_engine.competition.constants import ACTIVE_COMPETITION_PORTFOLIOS
from quantara_engine.competition.leverage import is_paper_competition_portfolio
from quantara_engine.domain.types import (
    Candle,
    Direction,
    Mode,
    Portfolio,
    PortfolioStatus,
    Position,
    PositionStatus,
    RiskProfile,
    Signal,
    SignalAction,
    StrategyInstance,
)
from quantara_engine.risk.engine import RiskEngine, RiskEvaluationInput


def _portfolio(portfolio_id: str | None = None) -> Portfolio:
    pid = portfolio_id or ACTIVE_COMPETITION_PORTFOLIOS[0].portfolio_id
    return Portfolio(
        id=pid,
        name="test",
        mode=Mode.PAPER,
        initial_capital=Decimal("2000"),
        balance=Decimal("2000"),
        equity=Decimal("2000"),
        peak_equity=Decimal("2000"),
        status=PortfolioStatus.ACTIVE,
    )


def _risk(**overrides) -> RiskProfile:
    base = dict(
        id="rp1",
        slug="balanced",
        name="Balanced",
        risk_per_trade_pct=Decimal("1"),
        max_open_positions=1,
        max_total_exposure_pct=Decimal("100"),
        daily_loss_limit_pct=Decimal("10"),
        max_drawdown_pct=Decimal("5"),
    )
    base.update(overrides)
    return RiskProfile(**base)


def _candle() -> Candle:
    ts = datetime(2026, 9, 9, 14, 0, tzinfo=timezone.utc)
    return Candle(
        instrument_id="inst-btc",
        timeframe="5m",
        timestamp=ts,
        open=Decimal("60000"),
        high=Decimal("60100"),
        low=Decimal("59900"),
        close=Decimal("60050"),
        volume=Decimal("1"),
    )


def _instrument():
    from quantara_engine.domain.types import Instrument

    return Instrument(
        id="inst-btc",
        symbol="BTCUSD",
        name="BTC/USD",
        asset_class="crypto",
        quantity_step=Decimal("0.0001"),
        min_quantity=Decimal("0.0001"),
    )


def _instance():
    return StrategyInstance(
        id=ACTIVE_COMPETITION_PORTFOLIOS[0].instance_id,
        portfolio_id=ACTIVE_COMPETITION_PORTFOLIOS[0].portfolio_id,
        strategy_version_id="sv1",
        strategy_slug="gold-trend-pullback",
        strategy_version="1.0.0",
        instrument_id="inst-btc",
        timeframe="5m",
        risk_profile_id="rp1",
        parameter_overrides={},
        is_active=True,
    )


def _open_position(*, direction: Direction, pos_id: str, qty: str = "0.01") -> Position:
    return Position(
        id=pos_id,
        portfolio_id=ACTIVE_COMPETITION_PORTFOLIOS[0].portfolio_id,
        instrument_id="inst-btc",
        strategy_instance_id=ACTIVE_COMPETITION_PORTFOLIOS[0].instance_id,
        direction=direction,
        quantity=Decimal(qty),
        entry_price=Decimal("60000"),
        current_price=Decimal("60050"),
        stop_loss=Decimal("59000"),
        take_profit=Decimal("62000"),
        unrealized_pnl=Decimal("0.50"),
        status=PositionStatus.OPEN,
        opened_at=datetime(2026, 9, 9, 13, 0, tzinfo=timezone.utc),
    )


def test_paper_competition_allows_second_long_same_asset():
    engine = RiskEngine()
    candle = _candle()
    existing = _open_position(direction=Direction.LONG, pos_id="p1")
    decision = engine.evaluate(
        RiskEvaluationInput(
            signal=Signal(
                action=SignalAction.BUY,
                reason="test",
                suggested_sl=Decimal("59000"),
                suggested_tp=Decimal("62000"),
            ),
            strategy_instance=_instance(),
            portfolio=_portfolio(),
            open_positions=[existing],
            risk_profile=_risk(max_open_positions=1),
            current_candle=candle,
            instrument=_instrument(),
        )
    )
    assert decision.approved is True
    assert decision.intent is not None


def test_paper_competition_allows_long_and_short_same_asset():
    engine = RiskEngine()
    candle = _candle()
    existing = _open_position(direction=Direction.LONG, pos_id="p1")
    decision = engine.evaluate(
        RiskEvaluationInput(
            signal=Signal(
                action=SignalAction.SELL,
                reason="test",
                suggested_sl=Decimal("61000"),
                suggested_tp=Decimal("58000"),
            ),
            strategy_instance=_instance(),
            portfolio=_portfolio(),
            open_positions=[existing],
            risk_profile=_risk(max_open_positions=1),
            current_candle=candle,
            instrument=_instrument(),
        )
    )
    assert decision.approved is True
    assert decision.intent.direction == Direction.SHORT


def test_paper_competition_ignores_drawdown_halt():
    engine = RiskEngine()
    candle = _candle()
    portfolio = _portfolio()
    portfolio.peak_equity = Decimal("3000")
    portfolio.equity = Decimal("2000")
    decision = engine.evaluate(
        RiskEvaluationInput(
            signal=Signal(
                action=SignalAction.BUY,
                reason="test",
                suggested_sl=Decimal("59000"),
                suggested_tp=Decimal("62000"),
            ),
            strategy_instance=_instance(),
            portfolio=portfolio,
            open_positions=[],
            risk_profile=_risk(max_drawdown_pct=Decimal("5")),
            current_candle=candle,
            instrument=_instrument(),
        )
    )
    assert decision.approved is True
    assert decision.should_halt is False


def test_non_competition_portfolio_still_blocks_duplicate_asset():
    engine = RiskEngine()
    candle = _candle()
    existing = _open_position(direction=Direction.LONG, pos_id="p1")
    decision = engine.evaluate(
        RiskEvaluationInput(
            signal=Signal(
                action=SignalAction.BUY,
                reason="test",
                suggested_sl=Decimal("59000"),
                suggested_tp=Decimal("62000"),
            ),
            strategy_instance=_instance(),
            portfolio=_portfolio("00000000-0000-0000-0000-999999999999"),
            open_positions=[existing],
            risk_profile=_risk(),
            current_candle=candle,
            instrument=_instrument(),
        )
    )
    assert decision.approved is False
    assert decision.denial_reason == "POSITION_ALREADY_OPEN"


def test_active_competition_portfolio_count():
    assert len(ACTIVE_COMPETITION_PORTFOLIOS) == 120
    assert is_paper_competition_portfolio(ACTIVE_COMPETITION_PORTFOLIOS[0].portfolio_id)
