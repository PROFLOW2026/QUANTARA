"""Run strategy pipeline on latest candles — fans out to competition portfolios."""

from __future__ import annotations

import logging
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal

from quantara_engine.competition.constants import TIMEFRAME_ORDER
from quantara_engine.core.clock import BacktestClock
from quantara_engine.core.config import settings
from quantara_engine.db.session import session_scope
from quantara_engine.domain.types import ExecutionAssumptions, Mode
from quantara_engine.execution.paper_broker import PaperBrokerAdapter
from quantara_engine.market_data.factory import get_market_data_provider
from quantara_engine.market_data.polling import STRATEGY_MIN_CANDLES, is_bar_complete
from quantara_engine.pipeline.candle_processor import CandleProcessor
from quantara_engine.persistence.store import TradingStore

logger = logging.getLogger(__name__)

OWNER_ID = "00000000-0000-0000-0000-000000000001"


def _ensure_candles(s: TradingStore, instrument, timeframe: str, settings_dict: dict) -> list:
    candles = s.list_candles(instrument.id, timeframe)
    if len(candles) >= STRATEGY_MIN_CANDLES:
        return sorted(candles, key=lambda c: c.timestamp)

    if settings_dict.get("market_data_provider") == "mock" or settings.market_data_provider == "mock":
        provider = get_market_data_provider("mock")
        generated = provider.generate_candles(
            instrument.id, timeframe, STRATEGY_MIN_CANDLES + 50
        )
        for candle in generated:
            s.upsert_candle(candle)
        candles = s.list_candles(instrument.id, timeframe)
        return sorted(candles, key=lambda c: c.timestamp)

    logger.warning(
        "Insufficient real candles (%d/%d) for %s — skipping run",
        len(candles),
        STRATEGY_MIN_CANDLES,
        timeframe,
    )
    return []


def _process_timeframe_group(
    s: TradingStore,
    instrument,
    timeframe: str,
    group: list[dict],
    settings_dict: dict,
    started_at: datetime,
    broker: PaperBrokerAdapter,
) -> tuple[int, int]:
    """Evaluate one signal for timeframe and fan out to risk portfolios."""
    candles = _ensure_candles(s, instrument, timeframe, settings_dict)
    if len(candles) < STRATEGY_MIN_CANDLES:
        return 0, 0

    candle_index = len(candles) - 1
    candle = candles[candle_index]

    if not is_bar_complete(candle.timestamp, timeframe, started_at):
        return 0, 0

    instance_ids = [entry["instance"].id for entry in group]
    if s.timeframe_group_already_processed(instance_ids, candle.timestamp):
        return 0, 0

    template = group[0]
    eval_processor = CandleProcessor(
        portfolio_state=s.load_portfolio_state(template["portfolio"].id),
        strategy_instance=template["instance"],
        instrument=instrument,
        risk_profile=template["risk_profile"],
        broker=broker,
        clock=BacktestClock(),
        store=None,
        mode=Mode.PAPER,
    )
    eval_processor.all_candles = candles
    shared_signal, _ = eval_processor.evaluate_signal(candle_index)

    decisions = 0
    for entry in group:
        portfolio = entry["portfolio"]
        instance = entry["instance"]
        risk_profile = entry["risk_profile"]
        state = s.load_portfolio_state(portfolio.id)
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
        pending = s.list_pending_order_intents(portfolio.id, instance.id)
        processor.pending_intents = pending
        processor._persisted_intents = {intent.id for intent in pending}
        processor.process_candle(candle_index, shared_signal=shared_signal)
        decisions += len(processor.decisions)

    return len(group), decisions


def _process_competition(s: TradingStore, started_at: datetime) -> int:
    entries = s.list_competition_entries()
    if not entries:
        return 0

    instrument = s.get_instrument_by_symbol("XAUUSD")
    if not instrument:
        logger.warning("XAUUSD instrument not found")
        return 0

    settings_dict = s.get_settings_dict()
    by_timeframe: dict[str, list[dict]] = defaultdict(list)
    for entry in entries:
        by_timeframe[entry["instance"].timeframe].append(entry)

    broker = PaperBrokerAdapter(instrument.id, ExecutionAssumptions())
    total_decisions = 0
    groups_evaluated = 0
    portfolios_touched = 0

    for timeframe in TIMEFRAME_ORDER:
        group = by_timeframe.get(timeframe, [])
        if not group:
            continue
        touched, decisions = _process_timeframe_group(
            s,
            instrument,
            timeframe,
            group,
            settings_dict,
            started_at,
            broker,
        )
        if touched:
            groups_evaluated += 1
            portfolios_touched += touched
            total_decisions += decisions

    if groups_evaluated == 0:
        s.update_worker_status(
            "strategy_runner",
            {
                "status": "waiting",
                "last_run": started_at.isoformat(),
                "reason": "no_completed_bars",
                "competition_portfolios": len(entries),
            },
        )
        return 0

    s.update_worker_status(
        "strategy_runner",
        {
            "status": "healthy",
            "last_run": started_at.isoformat(),
            "jobs_pending": 0,
            "decisions": total_decisions,
            "competition_portfolios": len(entries),
            "timeframe_groups_evaluated": groups_evaluated,
        },
    )
    s.save_worker_run(
        run_id=str(uuid.uuid4()),
        worker_name="strategy_runner",
        started_at=started_at,
        jobs_processed=groups_evaluated,
    )
    logger.info(
        "run_strategy competition fan-out completed (%d groups, %d portfolios, %d decisions)",
        groups_evaluated,
        portfolios_touched,
        total_decisions,
    )
    return portfolios_touched


def _process_legacy(s: TradingStore, started_at: datetime) -> None:
    settings_dict = s.get_settings_dict()
    portfolio = s.get_or_create_paper_portfolio(
        owner_id=OWNER_ID,
        initial_capital=Decimal(str(settings_dict.get("default_initial_capital", 10000))),
    )
    instance = s.get_paper_strategy_instance(portfolio.id)
    if not instance:
        logger.warning("No active paper strategy instance — run seed first")
        return

    instrument = s.get_instrument_by_symbol("XAUUSD")
    if not instrument:
        logger.warning("XAUUSD instrument not found")
        return

    candles = _ensure_candles(s, instrument, instance.timeframe, settings_dict)
    if len(candles) < STRATEGY_MIN_CANDLES:
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

    state = s.load_portfolio_state(portfolio.id)
    risk_profile = s.get_risk_profile_by_slug(
        settings_dict.get("default_risk_profile", "balanced")
    )
    if not risk_profile:
        risk_profile = s.get_risk_profile_by_id(instance.risk_profile_id)
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
    pending = s.list_pending_order_intents(portfolio.id, instance.id)
    processor.pending_intents = pending
    processor._persisted_intents = {intent.id for intent in pending}
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
    logger.info("run_strategy legacy pipeline completed")


def run_strategy_job(store: TradingStore | None = None) -> None:
    started_at = datetime.now(timezone.utc)

    def _run(s: TradingStore) -> None:
        settings_dict = s.get_settings_dict()
        if not settings_dict.get("paper_trading_enabled", True):
            return

        if s.list_competition_entries():
            _process_competition(s, started_at)
        else:
            _process_legacy(s, started_at)

    if store is not None:
        _run(store)
    else:
        with session_scope() as session:
            _run(TradingStore(session))
