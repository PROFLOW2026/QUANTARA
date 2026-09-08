"""Regression tests for live-first strategy architecture."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from quantara_engine.core.clock import BacktestClock
from quantara_engine.domain.types import (
    Candle,
    DecisionType,
    Direction,
    ExecutionAssumptions,
    Instrument,
    IntentStatus,
    Mode,
    OrderIntent,
    Portfolio,
    PortfolioStatus,
    Position,
    PositionStatus,
    RiskProfile,
    Signal,
    SignalAction,
    StrategyInstance,
    new_id,
)
from quantara_engine.execution.paper_broker import PaperBrokerAdapter
from quantara_engine.pipeline.candle_processor import (
    CandleProcessor,
    FRESHNESS_MAX_AGE_MINUTES,
    intent_execution_allowed,
    signal_age_minutes,
)
from quantara_engine.portfolio.service import PortfolioState


def _base_candle(
    *,
    timestamp: datetime,
    timeframe: str = "15m",
    instrument_id: str = "btc",
) -> Candle:
    return Candle(
        instrument_id=instrument_id,
        timeframe=timeframe,
        timestamp=timestamp,
        open=Decimal("78400"),
        high=Decimal("78500"),
        low=Decimal("78300"),
        close=Decimal("78450"),
        volume=Decimal("1"),
    )


def _processor(
    *,
    candle: Candle,
    latest_completed: datetime | None = None,
    allow_live_execution: bool = True,
    execution_now: datetime | None = None,
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
        timeframe=candle.timeframe,
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
    broker = PaperBrokerAdapter(candle.instrument_id, ExecutionAssumptions())
    return CandleProcessor(
        portfolio_state=PortfolioState(portfolio=portfolio),
        strategy_instance=instance,
        instrument=instrument,
        risk_profile=risk,
        broker=broker,
        clock=BacktestClock(),
        store=None,
        mode=Mode.PAPER,
        latest_completed_timestamp=latest_completed,
        allow_live_execution=allow_live_execution,
        execution_now=execution_now or datetime.now(timezone.utc),
    )


def test_15m_signal_35_minutes_late_cannot_execute():
    signal_ts = datetime(2026, 9, 8, 22, 15, tzinfo=timezone.utc)
    exec_ts = datetime(2026, 9, 8, 22, 30, tzinfo=timezone.utc)
    now = datetime(2026, 9, 8, 22, 49, 42, tzinfo=timezone.utc)
    candle = _base_candle(timestamp=exec_ts, timeframe="15m")
    intent = OrderIntent(
        id=new_id(),
        signal_id=new_id(),
        strategy_instance_id="si1",
        portfolio_id="p1",
        direction=Direction.SHORT,
        quantity=Decimal("0.01"),
        stop_loss=Decimal("79000"),
        take_profit=Decimal("78000"),
        target_risk_amount=Decimal("5"),
        actual_risk_amount=Decimal("5"),
        signal_candle_timestamp=signal_ts,
        execution_candle_timestamp=exec_ts,
        risk_profile_id="rp1",
        status=IntentStatus.PENDING_EXECUTION,
    )
    allowed, reason = intent_execution_allowed(intent, candle=candle, now=now)
    assert not allowed
    assert reason is not None
    assert "stale_signal_age" in reason
    assert signal_age_minutes(signal_ts, now) > FRESHNESS_MAX_AGE_MINUTES


def test_historical_mode_persists_signal_decision_without_position():
    candle_ts = datetime(2026, 9, 8, 22, 15, tzinfo=timezone.utc)
    candle = _base_candle(timestamp=candle_ts, timeframe="15m")
    proc = _processor(
        candle=candle,
        allow_live_execution=False,
        execution_now=datetime(2026, 9, 8, 22, 49, tzinfo=timezone.utc),
    )
    proc.all_candles = [candle]
    sell = Signal(action=SignalAction.SELL, reason="test sell")
    proc.process_candle(0, shared_signal=sell)
    assert len(proc.state.open_positions()) == 0
    assert any(d.decision_type == DecisionType.SELL_SIGNAL for d in proc.decisions)


def test_live_current_bar_executes_normally():
    signal_ts = datetime(2026, 9, 8, 22, 35, tzinfo=timezone.utc)
    exec_ts = datetime(2026, 9, 8, 22, 40, tzinfo=timezone.utc)
    now = datetime(2026, 9, 8, 22, 49, tzinfo=timezone.utc)
    candle = _base_candle(timestamp=exec_ts, timeframe="5m")
    intent = OrderIntent(
        id=new_id(),
        signal_id=new_id(),
        strategy_instance_id="si1",
        portfolio_id="p1",
        direction=Direction.SHORT,
        quantity=Decimal("0.01"),
        stop_loss=Decimal("79000"),
        take_profit=Decimal("78000"),
        target_risk_amount=Decimal("5"),
        actual_risk_amount=Decimal("5"),
        signal_candle_timestamp=signal_ts,
        execution_candle_timestamp=exec_ts,
        risk_profile_id="rp1",
        status=IntentStatus.PENDING_EXECUTION,
    )
    allowed, _ = intent_execution_allowed(intent, candle=candle, now=now)
    assert allowed

    proc = _processor(
        candle=candle,
        latest_completed=exec_ts,
        allow_live_execution=True,
        execution_now=now,
    )
    proc.all_candles = [candle]
    proc.pending_intents = [intent]
    proc.process_candle(0, shared_signal=Signal(action=SignalAction.HOLD, reason="test"))
    assert len(proc.state.open_positions()) == 1


def test_open_position_sl_tp_still_processed_via_position_management():
    entry_ts = datetime(2026, 9, 8, 22, 0, tzinfo=timezone.utc)
    sl_ts = entry_ts + timedelta(minutes=5)
    sl_candle = Candle(
        instrument_id="btc",
        timeframe="5m",
        timestamp=sl_ts,
        open=Decimal("78400"),
        high=Decimal("78600"),
        low=Decimal("78300"),
        close=Decimal("78400"),
        volume=Decimal("1"),
    )
    proc = _processor(
        candle=sl_candle,
        allow_live_execution=True,
        execution_now=sl_ts + timedelta(minutes=1),
    )
    proc.state.positions.append(
        Position(
            id=new_id(),
            portfolio_id="p1",
            strategy_instance_id="si1",
            instrument_id="btc",
            direction=Direction.SHORT,
            quantity=Decimal("0.01"),
            entry_price=Decimal("78400"),
            stop_loss=Decimal("78500"),
            take_profit=Decimal("78000"),
            current_price=Decimal("78400"),
            status=PositionStatus.OPEN,
            opened_at=entry_ts,
            strategy_version_id="sv1",
        )
    )
    proc.all_candles = [sl_candle]
    proc.process_position_management(0)
    assert len(proc.state.open_positions()) == 0
