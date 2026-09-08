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
from quantara_engine.execution.catch_up import list_catchup_candle_indices
from quantara_engine.execution.paper_broker import PaperBrokerAdapter
from quantara_engine.market_data.factory import get_market_data_provider
from quantara_engine.market_data.symbols import list_target_db_symbols
from quantara_engine.market_data.polling import STRATEGY_MIN_CANDLES, is_bar_complete
from quantara_engine.pipeline.candle_processor import CandleProcessor
from quantara_engine.persistence.store import TradingStore

logger = logging.getLogger(__name__)


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


def _catchup_indices(
    s: TradingStore,
    candles: list,
    timeframe: str,
    instance_ids: list[str],
    now: datetime,
) -> list[int]:
    last_processed = s.get_timeframe_group_last_processed(instance_ids)
    return list_catchup_candle_indices(
        candles,
        timeframe,
        last_processed=last_processed,
        now=now,
        already_processed_fn=lambda ts: s.timeframe_group_already_processed(instance_ids, ts),
    )


def _process_timeframe_group(
    s: TradingStore,
    instrument,
    timeframe: str,
    group: list[dict],
    settings_dict: dict,
    started_at: datetime,
    broker: PaperBrokerAdapter,
) -> tuple[int, int, dict]:
    """Process all missed completed candles sequentially, oldest → newest."""
    candles = _ensure_candles(s, instrument, timeframe, settings_dict)
    if len(candles) < STRATEGY_MIN_CANDLES:
        return 0, 0, s.get_timeframe_execution_status(
            instrument.id, timeframe, [e["instance"].id for e in group], started_at
        )

    instance_ids = [entry["instance"].id for entry in group]
    indices = _catchup_indices(s, candles, timeframe, instance_ids, started_at)
    if not indices:
        return 0, 0, s.get_timeframe_execution_status(
            instrument.id, timeframe, instance_ids, started_at
        )

    total_decisions = 0
    candles_processed = 0
    template = group[0]

    for candle_index in indices:
        candle = candles[candle_index]
        if s.timeframe_group_already_processed(instance_ids, candle.timestamp):
            continue

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
            total_decisions += len(processor.decisions)

        candles_processed += 1
        logger.info(
            "Processed %s candle %s (%d portfolios, backlog remaining)",
            timeframe,
            candle.timestamp.isoformat(),
            len(group),
        )

    tf_status = s.get_timeframe_execution_status(
        instrument.id, timeframe, instance_ids, started_at
    )
    return candles_processed, total_decisions, tf_status


def _process_competition(s: TradingStore, started_at: datetime) -> int:
    entries = s.list_competition_entries()
    if not entries:
        return 0

    settings_dict = s.get_settings_dict()
    by_timeframe: dict[str, list[dict]] = defaultdict(list)
    for entry in entries:
        by_timeframe[entry["instance"].timeframe].append(entry)

    total_decisions = 0
    groups_evaluated = 0
    portfolios_touched = 0
    timeframe_status: dict[str, dict] = {}
    instrument_status: dict[str, dict] = {}

    for symbol in list_target_db_symbols():
        instrument = s.get_instrument_by_symbol(symbol)
        if not instrument:
            logger.warning("Instrument %s not found — skipping", symbol)
            continue

        broker = PaperBrokerAdapter(instrument.id, ExecutionAssumptions())
        symbol_tf_status: dict[str, dict] = {}

        for timeframe in TIMEFRAME_ORDER:
            group = by_timeframe.get(timeframe, [])
            if not group:
                continue
            candles_processed, decisions, tf_status = _process_timeframe_group(
                s,
                instrument,
                timeframe,
                group,
                settings_dict,
                started_at,
                broker,
            )
            symbol_tf_status[timeframe] = tf_status
            if candles_processed:
                groups_evaluated += 1
                portfolios_touched += len(group) * candles_processed
                total_decisions += decisions

        instrument_status[symbol] = symbol_tf_status
        for timeframe, st in symbol_tf_status.items():
            prev = timeframe_status.setdefault(timeframe, {"backlog": 0, "instances": 0})
            prev["backlog"] = int(prev.get("backlog", 0)) + int(st.get("backlog", 0))
            prev["instances"] = int(prev.get("instances", 0)) + int(st.get("instances", 0))

    overall_backlog = sum(st.get("backlog", 0) for st in timeframe_status.values())
    overall_status = "catching_up" if overall_backlog > 0 else "healthy"

    if groups_evaluated == 0:
        s.update_worker_status(
            "strategy_runner",
            {
                "status": "waiting",
                "last_run": started_at.isoformat(),
                "reason": "no_completed_bars",
                "competition_portfolios": len(entries),
                "timeframes": timeframe_status,
                "instruments": instrument_status,
            },
        )
        return 0

    s.update_worker_status(
        "strategy_runner",
        {
            "status": overall_status,
            "last_run": started_at.isoformat(),
            "jobs_pending": overall_backlog,
            "decisions": total_decisions,
            "competition_portfolios": len(entries),
            "timeframe_groups_evaluated": groups_evaluated,
            "timeframes": timeframe_status,
            "instruments": instrument_status,
        },
    )
    s.save_worker_run(
        run_id=str(uuid.uuid4()),
        worker_name="strategy_runner",
        started_at=started_at,
        jobs_processed=groups_evaluated,
    )
    logger.info(
        "run_strategy catch-up completed (%d candle-batches, %d portfolio-runs, %d decisions, backlog=%d)",
        groups_evaluated,
        portfolios_touched,
        total_decisions,
        overall_backlog,
    )
    return portfolios_touched


def run_strategy_job(store: TradingStore | None = None) -> None:
    started_at = datetime.now(timezone.utc)

    def _run(s: TradingStore) -> None:
        settings_dict = s.get_settings_dict()
        if not settings_dict.get("paper_trading_enabled", True):
            return

        if s.list_competition_entries():
            _process_competition(s, started_at)
        else:
            logger.warning("No active competition portfolios — strategy runner idle")

    if store is not None:
        _run(store)
    else:
        with session_scope() as session:
            _run(TradingStore(session))
