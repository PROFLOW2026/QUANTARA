"""Run strategy pipeline on latest candles — fans out to competition portfolios."""

from __future__ import annotations

import logging
import time
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
from quantara_engine.market_data.registry import get_asset
from quantara_engine.market_data.symbols import list_target_db_symbols
from quantara_engine.market_data.polling import STRATEGY_MIN_CANDLES, is_bar_complete
from quantara_engine.pipeline.candle_processor import CandleProcessor
from quantara_engine.persistence.store import TradingStore

logger = logging.getLogger(__name__)

FRESHNESS_MAX_AGE_MINUTES = 30
CANDLE_LOOKBACK = STRATEGY_MIN_CANDLES + 50
MAX_HISTORICAL_DECISIONS_PER_RUN = 50


def _check_eligibility(
    s: TradingStore,
    instrument,
    timeframe: str,
    now: datetime,
) -> tuple[bool, str]:
    """Return (eligible, reason) for strategy evaluation on this asset/timeframe."""
    asset = get_asset(instrument.symbol)
    count = s.count_candles(instrument.id, timeframe)
    if count < STRATEGY_MIN_CANDLES:
        return False, f"insufficient_history ({count}/{STRATEGY_MIN_CANDLES})"

    last_ts = s.latest_candle_timestamp(instrument.id, timeframe)
    if not last_ts:
        return False, "no_data"

    age_min = (now - last_ts).total_seconds() / 60
    if age_min >= FRESHNESS_MAX_AGE_MINUTES:
        return False, f"stale_data ({round(age_min, 1)}m)"

    if asset and asset.primary_provider.value == "twelvedata":
        worker = s.get_settings_dict().get("worker_status:data_fetcher") or {}
        wh = (worker.get("assets") or {}).get(asset.db_symbol, {})
        if wh.get("status") == "error" and wh.get("error") and "429" in str(wh.get("error")):
            return False, "provider_blocked"

    return True, "eligible"


def _ensure_candles(s: TradingStore, instrument, timeframe: str, settings_dict: dict) -> list:
    stored = s.count_candles(instrument.id, timeframe)
    if stored < STRATEGY_MIN_CANDLES:
        if settings_dict.get("market_data_provider") == "mock" or settings.market_data_provider == "mock":
            provider = get_market_data_provider("mock")
            generated = provider.generate_candles(
                instrument.id, timeframe, STRATEGY_MIN_CANDLES + 50
            )
            for candle in generated:
                s.upsert_candle(candle)
            stored = s.count_candles(instrument.id, timeframe)
        else:
            logger.warning(
                "Insufficient real candles (%d/%d) for %s — skipping run",
                stored,
                STRATEGY_MIN_CANDLES,
                timeframe,
            )
            return []

    candles = s.list_recent_candles(instrument.id, timeframe, limit=CANDLE_LOOKBACK)
    if len(candles) < STRATEGY_MIN_CANDLES:
        return []
    return candles


def _catchup_indices(
    s: TradingStore,
    candles: list,
    timeframe: str,
    instance_ids: list[str],
    instrument_id: str,
    now: datetime,
) -> list[int]:
    last_processed = s.get_timeframe_group_last_processed(instance_ids, instrument_id)
    return list_catchup_candle_indices(
        candles,
        timeframe,
        last_processed=last_processed,
        now=now,
        already_processed_fn=lambda ts: s.timeframe_group_already_processed(
            instance_ids, instrument_id, ts
        ),
    )


def _process_candle_batch(
    s: TradingStore,
    instrument,
    timeframe: str,
    group: list[dict],
    candles: list,
    candle_index: int,
    started_at: datetime,
    broker: PaperBrokerAdapter,
    latest_completed_ts: datetime | None,
    *,
    allow_live_execution: bool,
) -> int:
    """Process one candle index across all portfolios in the timeframe group."""
    candle = candles[candle_index]
    instance_ids = [entry["instance"].id for entry in group]
    if s.timeframe_group_already_processed(instance_ids, instrument.id, candle.timestamp):
        return 0

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

    total_decisions = 0
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
            latest_completed_timestamp=latest_completed_ts,
            allow_live_execution=allow_live_execution,
            execution_now=started_at,
        )
        processor.all_candles = candles
        pending = s.list_pending_order_intents(portfolio.id, instance.id)
        processor.pending_intents = pending
        processor._persisted_intents = {intent.id for intent in pending}
        processor.process_candle(candle_index, shared_signal=shared_signal)
        total_decisions += len(processor.decisions)

    mode_label = "live" if allow_live_execution else "historical"
    logger.info(
        "Processed %s %s candle %s (%d portfolios)",
        timeframe,
        mode_label,
        candle.timestamp.isoformat(),
        len(group),
    )
    return total_decisions


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
    eligible, skip_reason = _check_eligibility(s, instrument, timeframe, started_at)
    if not eligible:
        return 0, 0, {
            **s.get_timeframe_execution_status(
                instrument.id, timeframe, [e["instance"].id for e in group], started_at
            ),
            "skipped": True,
            "skip_reason": skip_reason,
        }

    candles = _ensure_candles(s, instrument, timeframe, settings_dict)
    if len(candles) < STRATEGY_MIN_CANDLES:
        return 0, 0, s.get_timeframe_execution_status(
            instrument.id, timeframe, [e["instance"].id for e in group], started_at
        )

    instance_ids = [entry["instance"].id for entry in group]
    indices = _catchup_indices(s, candles, timeframe, instance_ids, instrument.id, started_at)
    if not indices:
        return 0, 0, s.get_timeframe_execution_status(
            instrument.id, timeframe, instance_ids, started_at
        )

    live_idx = indices[-1]
    historical_indices = indices[:-1]
    if len(historical_indices) > MAX_HISTORICAL_DECISIONS_PER_RUN:
        historical_indices = historical_indices[:MAX_HISTORICAL_DECISIONS_PER_RUN]

    latest_completed_ts: datetime | None = None
    for candle in candles:
        if is_bar_complete(candle.timestamp, timeframe, started_at):
            latest_completed_ts = candle.timestamp

    total_decisions = 0
    candles_processed = 0

    # Live path first: SL/TP, pending execution, and current-bar strategy.
    total_decisions += _process_candle_batch(
        s,
        instrument,
        timeframe,
        group,
        candles,
        live_idx,
        started_at,
        broker,
        latest_completed_ts,
        allow_live_execution=True,
    )
    candles_processed += 1

    # Bounded historical catch-up: decisions/analytics only, no Paper exposure.
    for candle_index in historical_indices:
        total_decisions += _process_candle_batch(
            s,
            instrument,
            timeframe,
            group,
            candles,
            candle_index,
            started_at,
            broker,
            latest_completed_ts,
            allow_live_execution=False,
        )
        candles_processed += 1

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
        s.save_worker_run(
            run_id=str(uuid.uuid4()),
            worker_name="strategy_runner",
            started_at=started_at,
            jobs_processed=0,
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
        "run_strategy completed (%d candle-batches, %d portfolio-runs, %d decisions, backlog=%d)",
        groups_evaluated,
        portfolios_touched,
        total_decisions,
        overall_backlog,
    )
    return portfolios_touched


def run_strategy_job(store: TradingStore | None = None) -> None:
    started_at = datetime.now(timezone.utc)
    t0 = time.perf_counter()

    def _run(s: TradingStore) -> int:
        settings_dict = s.get_settings_dict()
        if not settings_dict.get("paper_trading_enabled", True):
            return 0

        if s.list_competition_entries():
            return _process_competition(s, started_at)
        logger.warning("No active competition portfolios — strategy runner idle")
        return 0

    try:
        if store is not None:
            jobs = _run(store)
        else:
            with session_scope() as session:
                jobs = _run(TradingStore(session))
        duration_ms = round((time.perf_counter() - t0) * 1000, 1)
        logger.info("run_strategy_job finished in %.1fms (jobs=%d)", duration_ms, jobs)
    except Exception as exc:
        duration_ms = round((time.perf_counter() - t0) * 1000, 1)
        logger.exception("run_strategy_job failed after %.1fms", duration_ms)
        try:
            with session_scope() as session:
                s = TradingStore(session)
                s.update_worker_status(
                    "strategy_runner",
                    {
                        "status": "error",
                        "last_run": started_at.isoformat(),
                        "duration_ms": duration_ms,
                        "error": str(exc),
                    },
                )
                s.save_worker_run(
                    run_id=str(uuid.uuid4()),
                    worker_name="strategy_runner",
                    started_at=started_at,
                    status="error",
                    errors={"message": str(exc)},
                )
        except Exception:
            logger.exception("Failed to persist strategy_runner error status")
        raise
