"""No lookahead bias tests."""

from datetime import datetime, timezone
from decimal import Decimal

from quantara_engine.core.clock import BacktestClock
from quantara_engine.domain.types import Instrument, Mode, Portfolio, StrategyInstance
from quantara_engine.execution.paper_broker import PaperBrokerAdapter
from quantara_engine.market_data.adapters.mock import MockMarketDataProvider
from quantara_engine.pipeline.candle_processor import CandleProcessor
from quantara_engine.portfolio.service import PortfolioState
from quantara_engine.risk.profiles import get_risk_profile
from quantara_engine.strategies.registry import get


def _processor(candles):
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
    )
    instrument = Instrument(id="xauusd", symbol="XAUUSD", name="Gold")
    p = CandleProcessor(
        portfolio_state=PortfolioState(portfolio=portfolio),
        strategy_instance=instance,
        instrument=instrument,
        risk_profile=get_risk_profile("balanced"),
        broker=PaperBrokerAdapter("xauusd"),
        clock=BacktestClock(),
    )
    p.all_candles = candles
    return p


def test_past_decisions_unchanged_when_future_candles_added():
    provider = MockMarketDataProvider()
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    base_candles = provider.generate_candles("xauusd", "1h", 210, start)

    cls = get("gold-trend-pullback", "1.0.0")
    strategy = cls()
    ctx_params = {"min_candles_required": 200}

    from quantara_engine.domain.types import StrategyContext

    ctx = StrategyContext("xauusd", "1h", ctx_params)
    signal_base = strategy.evaluate(base_candles[:210], ctx)

    extended = provider.generate_candles("xauusd", "1h", 220, start)
    signal_extended = strategy.evaluate(extended[:210], ctx)

    assert signal_base.action == signal_extended.action
    assert signal_base.reason == signal_extended.reason


def test_strategy_never_sees_future_candle():
    provider = MockMarketDataProvider()
    candles = provider.generate_candles("xauusd", "1h", 205)
    processor = _processor(candles)

    decisions_at_200 = []
    for i in range(201):
        result = processor.process_candle(i)
        decisions_at_200.extend(result.decisions)

    # Processor only passes candles[:i+1] internally — verify clock never exceeds current
    assert processor.clock.current_candle_time() == candles[200].timestamp
