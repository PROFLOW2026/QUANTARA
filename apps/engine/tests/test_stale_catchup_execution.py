"""Tests for stale catch-up execution guard."""

from datetime import datetime, timezone
from decimal import Decimal

from quantara_engine.core.clock import BacktestClock
from quantara_engine.domain.types import (
    Candle,
    Direction,
    ExecutionAssumptions,
    Instrument,
    IntentStatus,
    Mode,
    OrderIntent,
    Portfolio,
    PortfolioStatus,
    RiskProfile,
    Signal,
    SignalAction,
    StrategyInstance,
    new_id,
)
from quantara_engine.execution.paper_broker import PaperBrokerAdapter
from quantara_engine.pipeline.candle_processor import CandleProcessor
from quantara_engine.portfolio.service import PortfolioState


def _processor(
    *,
    latest_completed: datetime,
    candle: Candle,
) -> CandleProcessor:
    portfolio = Portfolio(
        id="p1",
        name="Test",
        mode=Mode.PAPER,
        initial_capital=Decimal("2000"),
        balance=Decimal("2000"),
        equity=Decimal("2000"),
        status=PortfolioStatus.ACTIVE,
    )
    instance = StrategyInstance(
        id="si1",
        portfolio_id="p1",
        strategy_slug="gold-trend-pullback",
        strategy_version="1.0.0",
        strategy_version_id="sv1",
        instrument_id=candle.instrument_id,
        timeframe="5m",
        risk_profile_id="rp1",
        parameter_overrides={},
    )
    instrument = Instrument(
        id=candle.instrument_id,
        symbol="BTCUSD",
        name="BTC/USD",
        asset_class="crypto",
        quantity_step=Decimal("0.0001"),
        min_quantity=Decimal("0.0001"),
    )
    risk = RiskProfile(
        id="rp1",
        slug="very_conservative",
        name="Very Conservative",
        risk_per_trade_pct=Decimal("0.25"),
        max_open_positions=1,
        max_total_exposure_pct=Decimal("100"),
        daily_loss_limit_pct=Decimal("5"),
        max_drawdown_pct=Decimal("50"),
    )
    broker = PaperBrokerAdapter(ExecutionAssumptions())
    proc = CandleProcessor(
        portfolio_state=PortfolioState(portfolio=portfolio),
        strategy_instance=instance,
        instrument=instrument,
        risk_profile=risk,
        broker=broker,
        clock=BacktestClock(),
        store=None,
        mode=Mode.PAPER,
        latest_completed_timestamp=latest_completed,
    )
    proc.all_candles = [candle]
    intent = OrderIntent(
        id=new_id(),
        signal_id=new_id(),
        strategy_instance_id=instance.id,
        portfolio_id=portfolio.id,
        direction=Direction.SHORT,
        quantity=Decimal("0.01"),
        stop_loss=Decimal("79000"),
        take_profit=Decimal("78000"),
        target_risk_amount=Decimal("5"),
        actual_risk_amount=Decimal("5"),
        signal_candle_timestamp=candle.timestamp,
        execution_candle_timestamp=candle.timestamp,
        risk_profile_id=risk.id,
        status=IntentStatus.PENDING_EXECUTION,
    )
    proc.pending_intents = [intent]
    return proc


def test_stale_catchup_intent_rejected_not_executed():
    candle_ts = datetime(2026, 9, 8, 20, 35, tzinfo=timezone.utc)
    latest = datetime(2026, 9, 8, 21, 5, tzinfo=timezone.utc)
    candle = Candle(
        instrument_id="btc",
        timeframe="5m",
        timestamp=candle_ts,
        open=Decimal("78400"),
        high=Decimal("78500"),
        low=Decimal("78300"),
        close=Decimal("78450"),
        volume=Decimal("1"),
    )
    proc = _processor(latest_completed=latest, candle=candle)
    hold = Signal(action=SignalAction.HOLD, reason="test")
    proc.process_candle(0, shared_signal=hold)
    assert len(proc.state.open_positions()) == 0


def test_live_intent_on_latest_candle_executes():
    candle_ts = datetime(2026, 9, 8, 21, 5, tzinfo=timezone.utc)
    candle = Candle(
        instrument_id="btc",
        timeframe="5m",
        timestamp=candle_ts,
        open=Decimal("78400"),
        high=Decimal("78500"),
        low=Decimal("78300"),
        close=Decimal("78450"),
        volume=Decimal("1"),
    )
    proc = _processor(latest_completed=candle_ts, candle=candle)
    hold = Signal(action=SignalAction.HOLD, reason="test")
    proc.process_candle(0, shared_signal=hold)
    assert len(proc.state.open_positions()) == 1
