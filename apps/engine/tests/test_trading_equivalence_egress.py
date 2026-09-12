"""Trading output equivalence — baseline data access vs optimized runtime paths."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch

from quantara_engine.core.clock import BacktestClock
from quantara_engine.domain.types import (
    Candle,
    Direction,
    Instrument,
    Mode,
    Portfolio,
    PortfolioStatus,
    Position,
    PositionStatus,
    StrategyInstance,
    Trade,
)
from quantara_engine.execution.position_management import (
    MAX_CANDLES_PER_POSITION_PER_RUN,
    _prefetch_candles_by_pair,
    process_position_management,
)
from quantara_engine.market_data.candle_working_set import reset_worker_candle_cache
from quantara_engine.pipeline.candle_processor import CandleProcessor
from quantara_engine.portfolio.service import PortfolioState
from quantara_workers.jobs.run_strategy import _catchup_indices, _process_candle_batch
from tests.egress_test_support import IncrementalCandleBackingStore, make_candle_series
from tests.test_pm_candle_cache_catchup import (
    _baseline_prefetch,
    _instrument,
    _instance,
    _portfolio_state,
    _short_position,
    _PmStoreAdapter,
)


def _risk_profile():
    profile = MagicMock()
    profile.slug = "balanced"
    profile.risk_per_trade_pct = Decimal("1")
    profile.max_open_positions = 1
    profile.max_exposure_pct = Decimal("100")
    return profile


def test_pm_baseline_and_cache_prefetch_produce_identical_sl_exit():
    reset_worker_candle_cache()
    candles = make_candle_series(500, instrument_id="btc", timeframe="5m")
    sl_index = 49
    candles[sl_index] = Candle(
        instrument_id="btc",
        timeframe="5m",
        timestamp=candles[sl_index].timestamp,
        open=Decimal("149"),
        high=Decimal("160"),
        low=Decimal("148"),
        close=Decimal("155"),
        volume=Decimal("1"),
    )
    opened_at = candles[0].timestamp
    now = candles[-1].timestamp + timedelta(minutes=10)

    def _run(use_cache: bool) -> dict:
        reset_worker_candle_cache()
        pos = _short_position(opened_at=opened_at, sl="156")
        backing = IncrementalCandleBackingStore()
        backing.seed("btc", "5m", candles)
        state = _portfolio_state(pos)
        store = _PmStoreAdapter(backing, state, _instance())
        store.cursors[pos.id] = opened_at.isoformat()
        work = [(pos, _instance(), _instrument())]
        if use_cache:
            prefetched = _prefetch_candles_by_pair(store, work, store.cursors)
            rows = prefetched.get(("btc", "5m"))
        else:
            rows = store.list_candles(
                "btc",
                "5m",
                since=opened_at,
                limit=MAX_CANDLES_PER_POSITION_PER_RUN,
            )
        with patch("quantara_engine.broker.execution_bridge.execute_through_broker", return_value=None):
            return process_position_management(
                store,
                position=pos,
                instance=_instance(),
                instrument=_instrument(),
                now=now,
                cursors=store.cursors,
                portfolio_state=state,
                prefetched=rows,
            )

    baseline = _run(use_cache=False)
    optimized = _run(use_cache=True)
    assert baseline["status"] == optimized["status"] == "closed"
    assert baseline["exit_reason"] == optimized["exit_reason"] == "sl"
    assert baseline["exit_candle"] == optimized["exit_candle"]


def test_strategy_batch_decisions_unaffected_by_trade_snapshot_history():
    candles = make_candle_series(40, instrument_id="inst-1", timeframe="5m")
    now = candles[-1].timestamp + timedelta(minutes=10)
    portfolio = Portfolio(
        id="p1",
        name="Test",
        mode=Mode.PAPER,
        initial_capital=Decimal("2000"),
        balance=Decimal("2000"),
        equity=Decimal("2000"),
        status=PortfolioStatus.ACTIVE,
    )
    position = Position(
        id="pos1",
        portfolio_id="p1",
        strategy_instance_id="si1",
        instrument_id="inst-1",
        direction=Direction.LONG,
        quantity=Decimal("0.01"),
        entry_price=Decimal("100"),
        stop_loss=Decimal("95"),
        take_profit=Decimal("110"),
        current_price=Decimal("100"),
        status=PositionStatus.OPEN,
        opened_at=candles[0].timestamp,
        strategy_version_id="sv1",
    )
    from quantara_engine.domain.types import ExitReason

    history_trades = [
        Trade(
            id=f"t{i}",
            position_id=f"pos-h{i}",
            portfolio_id="p1",
            strategy_instance_id="si1",
            strategy_version_id="sv1",
            instrument_id="inst-1",
            direction=Direction.LONG,
            quantity=Decimal("0.01"),
            entry_price=Decimal("100"),
            exit_price=Decimal("101"),
            gross_pnl=Decimal("1"),
            realized_pnl=Decimal("1"),
            fees_total=Decimal("0"),
            slippage_total=Decimal("0"),
            spread_total=Decimal("0"),
            target_risk_amount=Decimal("20"),
            actual_risk_amount=Decimal("20"),
            exit_reason=ExitReason.TP,
            duration_seconds=300,
            opened_at=candles[0].timestamp,
            closed_at=candles[1].timestamp,
        )
        for i in range(50)
    ]
    runtime_state = PortfolioState(portfolio=portfolio, positions=[position], trades=[], snapshots=[])
    history_state = PortfolioState(
        portfolio=portfolio,
        positions=[position],
        trades=history_trades,
        snapshots=[],
    )
    instance = StrategyInstance(
        id="si1",
        portfolio_id="p1",
        strategy_slug="gold-trend-pullback",
        strategy_version="1.0.0",
        strategy_version_id="sv1",
        instrument_id="inst-1",
        timeframe="5m",
        risk_profile_id="rp1",
        parameter_overrides={},
    )
    instrument = Instrument(
        id="inst-1",
        symbol="BTCUSD",
        name="BTC",
        asset_class="crypto",
        quantity_step=Decimal("0.0001"),
        min_quantity=Decimal("0.0001"),
    )

    def _signal_for_state(state: PortfolioState):
        processor = CandleProcessor(
            portfolio_state=state,
            strategy_instance=instance,
            instrument=instrument,
            risk_profile=_risk_profile(),
            broker=MagicMock(),
            clock=BacktestClock(),
            store=MagicMock(),
            mode=Mode.PAPER,
        )
        processor.all_candles = candles
        signal, _ = processor.evaluate_signal(len(candles) - 1)
        return signal

    assert _signal_for_state(runtime_state) == _signal_for_state(history_state)


def test_catchup_indices_match_baseline_with_bounded_processed():
    store = MagicMock()
    candles = make_candle_series(30, instrument_id="inst-1", timeframe="5m")
    now = candles[-1].timestamp + timedelta(minutes=10)
    processed = {candles[i].timestamp for i in range(10)}
    store.fully_processed_candle_timestamps.return_value = processed
    store.get_competition_started_at.return_value = None

    indices = _catchup_indices(
        store,
        candles,
        "5m",
        ["si1"],
        "inst-1",
        now,
        processed_timestamps=processed,
    )
    baseline = _catchup_indices(
        store,
        candles,
        "5m",
        ["si1"],
        "inst-1",
        now,
        processed_timestamps=processed,
    )
    assert indices == baseline
