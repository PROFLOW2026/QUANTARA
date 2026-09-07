"""Run strategy pipeline on latest candles."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from quantara_engine.core.clock import BacktestClock
from quantara_engine.core.config import settings
from quantara_engine.db.session import session_scope
from quantara_engine.domain.types import ExecutionAssumptions, Mode
from quantara_engine.execution.paper_broker import PaperBrokerAdapter
from quantara_engine.market_data.factory import get_market_data_provider
from quantara_engine.market_data.polling import STRATEGY_MIN_CANDLES
from quantara_engine.pipeline.candle_processor import CandleProcessor
from quantara_engine.persistence.store import TradingStore

logger = logging.getLogger(__name__)

OWNER_ID = "00000000-0000-0000-0000-000000000001"


def run_strategy_job(store: TradingStore | None = None) -> None:
    started_at = datetime.now(timezone.utc)

    def _run(s: TradingStore) -> None:
        settings = s.get_settings_dict()
        if not settings.get("paper_trading_enabled", True):
            return

        portfolio = s.get_or_create_paper_portfolio(
            owner_id=OWNER_ID,
            initial_capital=Decimal(str(settings.get("default_initial_capital", 10000))),
        )
        instance = s.get_paper_strategy_instance(portfolio.id)
        if not instance:
            logger.warning("No active paper strategy instance — run seed first")
            return

        instrument = s.get_instrument_by_symbol("XAUUSD")
        if not instrument:
            logger.warning("XAUUSD instrument not found")
            return

        candles = s.list_candles(instrument.id, instance.timeframe)
        if len(candles) < STRATEGY_MIN_CANDLES:
            if settings.market_data_provider == "mock":
                provider = get_market_data_provider("mock")
                generated = provider.generate_candles(
                    instrument.id, instance.timeframe, STRATEGY_MIN_CANDLES + 50
                )
                for candle in generated:
                    s.upsert_candle(candle)
                candles = s.list_candles(instrument.id, instance.timeframe)
            else:
                logger.warning(
                    "Insufficient real candles (%d/%d) for strategy — skipping run",
                    len(candles),
                    STRATEGY_MIN_CANDLES,
                )
                s.update_worker_status(
                    "strategy_runner",
                    {
                        "status": "waiting",
                        "last_run": started_at.isoformat(),
                        "reason": "insufficient_candles",
                        "candle_count": len(candles),
                    },
                )
                return

        candles = sorted(candles, key=lambda c: c.timestamp)
        state = s.load_portfolio_state(portfolio.id)

        risk_profile = s.get_risk_profile_by_slug(
            settings.get("default_risk_profile", "balanced")
        )
        if not risk_profile:
            logger.warning("Risk profile not found")
            return

        broker = PaperBrokerAdapter(instrument.id, ExecutionAssumptions())
        processor = CandleProcessor(
            portfolio_state=state,
            strategy_instance=instance,
            instrument=instrument,
            risk_profile=risk_profile,
            broker=broker,
            clock=BacktestClock(),
            store=s,
            mode=Mode.PAPER,
        )
        processor.all_candles = candles
        if candles:
            processor.process_candle(len(candles) - 1)

        s.update_worker_status(
            "strategy_runner",
            {
                "status": "healthy",
                "last_run": started_at.isoformat(),
                "jobs_pending": 0,
                "decisions": len(processor.decisions),
            },
        )
        s.save_worker_run(
            run_id=str(uuid.uuid4()),
            worker_name="strategy_runner",
            started_at=started_at,
            jobs_processed=1,
        )
        logger.info("run_strategy pipeline completed")

    if store is not None:
        _run(store)
    else:
        with session_scope() as session:
            _run(TradingStore(session))
