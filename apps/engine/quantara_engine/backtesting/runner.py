"""Backtest runner."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import TYPE_CHECKING

from quantara_engine.backtesting.fingerprint import compute_dataset_fingerprint
from quantara_engine.backtesting.metrics import compute_backtest_metrics
from quantara_engine.core.clock import BacktestClock
from quantara_engine.domain.types import (
    ExecutionAssumptions,
    ExitReason,
    Instrument,
    Mode,
    Portfolio,
    PortfolioStatus,
    RiskProfile,
    StrategyInstance,
    Trade,
)
from quantara_engine.execution.simulated_broker import SimulatedBrokerAdapter
from quantara_engine.pipeline.candle_processor import CandleProcessor
from quantara_engine.portfolio.service import PortfolioState

if TYPE_CHECKING:
    from quantara_engine.persistence.store import TradingStore


class BacktestStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class BacktestRun:
    id: str
    strategy_instance: StrategyInstance
    instrument: Instrument
    risk_profile: RiskProfile
    candles: list
    initial_capital: Decimal
    execution_assumptions: ExecutionAssumptions = field(default_factory=ExecutionAssumptions)
    status: BacktestStatus = BacktestStatus.PENDING
    metrics: dict | None = None
    dataset_fingerprint: str = ""
    error_message: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    trades: list[Trade] = field(default_factory=list)
    positions: list = field(default_factory=list)


class BacktestRunner:
    def run(
        self,
        backtest: BacktestRun,
        store: TradingStore | None = None,
    ) -> BacktestRun:
        backtest.status = BacktestStatus.RUNNING
        backtest.started_at = datetime.now(timezone.utc)

        try:
            if store and not _is_uuid(backtest.id):
                backtest.id = str(uuid.uuid4())

            backtest.dataset_fingerprint = compute_dataset_fingerprint(backtest.candles)
            portfolio_id = backtest.id if store else f"bt-{backtest.id}"

            if store:
                self._ensure_backtest_portfolio(store, backtest, portfolio_id)
                store.create_backtest_run_record(
                    run_id=backtest.id,
                    portfolio_id=portfolio_id,
                    strategy_instance_id=backtest.strategy_instance.id,
                    strategy_version_id=backtest.strategy_instance.strategy_version_id,
                    instrument_id=backtest.instrument.id,
                    timeframe=backtest.strategy_instance.timeframe,
                    start_date=backtest.candles[0].timestamp if backtest.candles else datetime.now(timezone.utc),
                    end_date=backtest.candles[-1].timestamp if backtest.candles else datetime.now(timezone.utc),
                    initial_capital=backtest.initial_capital,
                    parameters=backtest.strategy_instance.parameter_overrides,
                    execution_assumptions={
                        "spread": float(backtest.execution_assumptions.spread),
                        "slippage_pct": float(backtest.execution_assumptions.slippage_pct),
                        "fee_rate": float(backtest.execution_assumptions.fee_rate),
                        "fill_timing": backtest.execution_assumptions.fill_timing,
                    },
                    dataset_fingerprint=backtest.dataset_fingerprint,
                )
                store.flush()

            portfolio = Portfolio(
                id=portfolio_id,
                name=f"Backtest {backtest.id}",
                mode=Mode.BACKTEST,
                initial_capital=backtest.initial_capital,
                balance=backtest.initial_capital,
                equity=backtest.initial_capital,
                peak_equity=backtest.initial_capital,
            )
            state = PortfolioState(portfolio=portfolio)
            broker = SimulatedBrokerAdapter(
                instrument_id=backtest.instrument.id,
                assumptions=backtest.execution_assumptions,
            )
            processor = CandleProcessor(
                portfolio_state=state,
                strategy_instance=backtest.strategy_instance,
                instrument=backtest.instrument,
                risk_profile=backtest.risk_profile,
                broker=broker,
                clock=BacktestClock(),
                store=store,
                mode=Mode.BACKTEST,
                backtest_run_id=backtest.id if store else None,
            )
            processor.all_candles = backtest.candles
            processor.run_all()

            # Close remaining positions at last candle
            if backtest.candles:
                last = backtest.candles[-1]
                for position in list(state.open_positions()):
                    from quantara_engine.execution.fill_calculator import calculate_fill_price

                    fill = calculate_fill_price(
                        position.direction,
                        "exit",
                        last.close,
                        position.quantity,
                        backtest.execution_assumptions,
                    )
                    trade = state.close_position(
                        position, fill, ExitReason.END_OF_BACKTEST, last.timestamp
                    )
                    if store:
                        store.update_position_closed(position.id, last.timestamp, fill.fill_price)
                        store.save_trade(trade)
                        store.update_portfolio(state.portfolio)
                        store.flush()

                state.recalculate_equity(last.close)
                snap = state.create_snapshot(last.timestamp)
                if store:
                    store.save_snapshot(snap)
                    store.flush()

            backtest.trades = list(state.trades)
            backtest.positions = list(state.positions)
            backtest.metrics = compute_backtest_metrics(
                initial_capital=backtest.initial_capital,
                final_equity=state.portfolio.equity,
                trades=state.trades,
                snapshots=state.snapshots,
                strategy_version=backtest.strategy_instance.strategy_version,
                parameters=backtest.strategy_instance.parameter_overrides,
                dataset_fingerprint=backtest.dataset_fingerprint,
                execution_assumptions={
                    "spread": float(backtest.execution_assumptions.spread),
                    "slippage_pct": float(backtest.execution_assumptions.slippage_pct),
                    "fee_rate": float(backtest.execution_assumptions.fee_rate),
                    "fill_timing": backtest.execution_assumptions.fill_timing,
                },
            )
            if backtest.strategy_instance.strategy_slug == "opening-range-breakout":
                from quantara_engine.backtesting.orb_analytics import attach_orb_analytics

                signals = store.list_backtest_entry_signals(backtest.id) if store else []
                backtest.metrics = attach_orb_analytics(
                    backtest.metrics, backtest.trades, signals
                )
            backtest.status = BacktestStatus.COMPLETED
            backtest.completed_at = datetime.now(timezone.utc)

            if store:
                store.update_backtest_run_completed(
                    run_id=backtest.id,
                    final_capital=state.portfolio.equity,
                    metrics=backtest.metrics,
                    status=BacktestStatus.COMPLETED.value,
                )
                store.flush()

        except Exception as exc:
            backtest.status = BacktestStatus.FAILED
            backtest.error_message = str(exc)
            if store:
                store.session.rollback()
                store.update_backtest_run_completed(
                    run_id=backtest.id,
                    final_capital=Decimal("0"),
                    metrics={},
                    status=BacktestStatus.FAILED.value,
                    error_message=str(exc),
                )
                store.flush()

        return backtest

    def _ensure_backtest_portfolio(
        self,
        store: TradingStore,
        backtest: BacktestRun,
        portfolio_id: str,
    ) -> None:
        from quantara_engine.models.enums import PortfolioMode, PortfolioStatus as OrmPortfolioStatus
        from quantara_engine.models.portfolio import Portfolio as OrmPortfolio

        existing = store.session.get(OrmPortfolio, uuid.UUID(portfolio_id))
        if existing:
            return

        paper = store.session.get(
            OrmPortfolio, uuid.UUID(backtest.strategy_instance.portfolio_id)
        )
        owner_id = paper.owner_id if paper else uuid.uuid4()

        row = OrmPortfolio(
            id=uuid.UUID(portfolio_id),
            owner_id=owner_id,
            name=f"Backtest {backtest.id}",
            mode=PortfolioMode.BACKTEST,
            initial_capital=backtest.initial_capital,
            balance=backtest.initial_capital,
            unrealized_pnl=Decimal("0"),
            equity=backtest.initial_capital,
            exposure_notional=Decimal("0"),
            reserved_capital=Decimal("0"),
            currency="USD",
            status=OrmPortfolioStatus.ACTIVE,
            peak_equity=backtest.initial_capital,
        )
        store.session.add(row)
        store.flush()


def _is_uuid(value: str) -> bool:
    try:
        uuid.UUID(value)
        return True
    except ValueError:
        return False
