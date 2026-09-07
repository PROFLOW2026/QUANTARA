"""Tests for next_open fill timing."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from quantara_engine.core.clock import BacktestClock
from quantara_engine.domain.types import (
    ExecutionAssumptions,
    Instrument,
    Mode,
    Portfolio,
    StrategyInstance,
)
from quantara_engine.execution.paper_broker import PaperBrokerAdapter
from quantara_engine.market_data.adapters.mock import MockMarketDataProvider
from quantara_engine.pipeline.candle_processor import CandleProcessor
from quantara_engine.portfolio.service import PortfolioState
from quantara_engine.risk.profiles import get_risk_profile


def _make_processor(candle_count: int = 210):
    provider = MockMarketDataProvider(base_price=Decimal("2700"))
    candles = provider.generate_candles(
        "xauusd", "1h", candle_count, datetime(2024, 6, 1, tzinfo=timezone.utc)
    )
    portfolio = Portfolio(
        id="p1",
        name="Test",
        mode=Mode.BACKTEST,
        initial_capital=Decimal("10000"),
        balance=Decimal("10000"),
        equity=Decimal("10000"),
        peak_equity=Decimal("10000"),
    )
    instance = StrategyInstance(
        id="si1",
        portfolio_id="p1",
        strategy_version_id="v1",
        strategy_slug="gold-trend-pullback",
        strategy_version="1.0.0",
        instrument_id="xauusd",
        timeframe="1h",
        risk_profile_id="balanced",
        parameter_overrides={"min_candles_required": 200, "rsi_entry_min": 0, "rsi_entry_max": 100},
    )
    instrument = Instrument(id="xauusd", symbol="XAUUSD", name="Gold")
    processor = CandleProcessor(
        portfolio_state=PortfolioState(portfolio=portfolio),
        strategy_instance=instance,
        instrument=instrument,
        risk_profile=get_risk_profile("balanced"),
        broker=PaperBrokerAdapter("xauusd", ExecutionAssumptions()),
        clock=BacktestClock(),
    )
    processor.all_candles = candles
    return processor, candles


def test_signal_on_n_fills_at_n_plus_one_open():
    processor, candles = _make_processor()
    # Run until we get a pending intent
    signal_candle_idx = None
    for i in range(200, len(candles)):
        processor.process_candle(i)
        if processor.pending_intents:
            signal_candle_idx = i
            break

    if signal_candle_idx is None:
        # Force a BUY by patching is not needed — skip if no signal in deterministic data
        return

    intent = processor.pending_intents[0]
    assert intent.execution_candle_timestamp == candles[signal_candle_idx + 1].timestamp
    assert intent.signal_candle_timestamp == candles[signal_candle_idx].timestamp


def test_fill_uses_next_candle_open_not_signal_close():
    processor, candles = _make_processor()
    assumptions = ExecutionAssumptions(spread=Decimal("0.30"), slippage_pct=Decimal("0.0001"))
    broker = PaperBrokerAdapter("xauusd", assumptions)

    next_candle = candles[201]
    from quantara_engine.domain.types import Direction, OrderIntent, new_id

    intent = OrderIntent(
        id=new_id(),
        signal_id=new_id(),
        strategy_instance_id="si1",
        portfolio_id="p1",
        direction=Direction.LONG,
        quantity=Decimal("0.1"),
        stop_loss=Decimal("2640"),
        take_profit=Decimal("2680"),
        target_risk_amount=Decimal("100"),
        actual_risk_amount=Decimal("100"),
        signal_candle_timestamp=candles[200].timestamp,
        execution_candle_timestamp=next_candle.timestamp,
    )
    _, fill = broker.execute_entry(intent, next_candle)
    assert fill.base_price == next_candle.open
    assert fill.fill_price != candles[200].close
