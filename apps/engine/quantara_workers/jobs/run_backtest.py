"""Run backtest jobs on demand."""

from __future__ import annotations

import logging
import uuid
from decimal import Decimal

from quantara_engine.backtesting.runner import BacktestRunner, BacktestRun
from quantara_engine.db.session import session_scope
from quantara_engine.domain.types import ExecutionAssumptions, Mode
from quantara_engine.market_data.adapters.mock import MockMarketDataProvider
from quantara_engine.persistence.store import TradingStore

logger = logging.getLogger(__name__)

OWNER_ID = "00000000-0000-0000-0000-000000000001"


def run_backtest_job(backtest_run_id: str, store: TradingStore | None = None) -> None:
    def _run(s: TradingStore) -> None:
        existing = s.get_backtest(backtest_run_id)
        if existing and existing.get("status") == "completed":
            return

        portfolio = s.get_or_create_paper_portfolio(owner_id=OWNER_ID)
        instance = s.get_paper_strategy_instance(portfolio.id)
        if not instance:
            logger.warning("No strategy instance for backtest")
            return

        instrument = s.get_instrument_by_symbol("XAUUSD")
        if not instrument:
            logger.warning("XAUUSD not found")
            return

        risk_profile = s.get_risk_profile_by_slug("balanced")
        if not risk_profile:
            logger.warning("balanced risk profile not found")
            return

        provider = MockMarketDataProvider()
        candles = provider.generate_candles(instrument.id, instance.timeframe, 250)

        bt = BacktestRun(
            id=backtest_run_id,
            strategy_instance=instance,
            instrument=instrument,
            risk_profile=risk_profile,
            candles=candles,
            initial_capital=Decimal("10000"),
            execution_assumptions=ExecutionAssumptions(),
        )
        runner = BacktestRunner()
        bt = runner.run(bt, store=s)

        s.update_worker_status(
            "backtest_runner",
            {
                "status": "idle",
                "active_jobs": 0,
                "last_run": backtest_run_id,
                "backtest_status": bt.status.value,
            },
        )
        s.save_worker_run(
            run_id=str(uuid.uuid4()),
            worker_name="backtest_runner",
            started_at=bt.started_at or bt.completed_at,
            jobs_processed=1,
        )
        logger.info("Backtest %s completed with status %s", backtest_run_id, bt.status.value)

    if store is not None:
        _run(store)
    else:
        with session_scope() as session:
            _run(TradingStore(session, mode=Mode.BACKTEST, backtest_run_id=backtest_run_id))
