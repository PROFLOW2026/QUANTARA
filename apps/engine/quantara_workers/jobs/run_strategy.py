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
from quantara_engine.competition.constants import ACTIVE_COMPETITION_EXPERIMENT_ID
from quantara_engine.market_data.polling import (
    STRATEGY_MIN_CANDLES,
    bar_staleness_minutes,
    is_bar_complete,
    is_market_data_fresh,
    max_staleness_minutes,
)
from quantara_engine.pipeline.candle_processor import CandleProcessor
from quantara_engine.persistence.store import TradingStore

logger = logging.getLogger(__name__)

CANDLE_LOOKBACK = STRATEGY_MIN_CANDLES + 50
MAX_HISTORICAL_DECISIONS_PER_RUN = 50
LIVE_CYCLE_MAX_SECONDS = 120
LIVE_PRIORITY_SYMBOLS = ("BTCUSD", "EURUSD", "XAUUSD")
HISTORICAL_CYCLE_MAX_SECONDS = 240
STRATEGY_STALL_THRESHOLD_MINUTES = 12


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

    if not is_market_data_fresh(last_ts, timeframe, now):
        stale_after_close = round(bar_staleness_minutes(last_ts, timeframe, now), 1)
        limit = round(max_staleness_minutes(timeframe), 1)
        return False, f"stale_data ({stale_after_close}m since close, limit {limit}m)"

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
    processed = s.fully_processed_candle_timestamps(instance_ids, instrument_id)
    return list_catchup_candle_indices(
        candles,
        timeframe,
        last_processed=None,
        now=now,
        processed_timestamps=processed,
    )


def _ordered_symbols() -> list[str]:
    """Process liquid 24/7 competition assets before optional equity symbols."""
    all_symbols = list_target_db_symbols()
    priority = [sym for sym in LIVE_PRIORITY_SYMBOLS if sym in all_symbols]
    remainder = [sym for sym in all_symbols if sym not in priority]
    return priority + remainder


def _commit_progress(s: TradingStore) -> None:
    """Persist decisions/status incrementally so long cycles remain observable."""
    s.session.commit()


def _mark_cycle_started(
    s: TradingStore,
    *,
    run_id: str,
    started_at: datetime,
    mode: str,
) -> None:
    s.update_worker_status(
        "strategy_runner",
        {
            "status": "running",
            "mode": mode,
            "cycle_run_id": run_id,
            "cycle_started_at": started_at.isoformat(),
            "last_run": started_at.isoformat(),
        },
    )
    _commit_progress(s)


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
    per_portfolio_eval: bool = False,
) -> int:
    """Process one candle index across all portfolios in the timeframe group."""
    candle = candles[candle_index]
    instance_ids = [entry["instance"].id for entry in group]
    if s.timeframe_group_already_processed(instance_ids, instrument.id, candle.timestamp):
        return 0

    shared_signal = None
    if not per_portfolio_eval:
        template = group[0]
        eval_processor = CandleProcessor(
            portfolio_state=s.load_portfolio_state(template["portfolio"].id),
            strategy_instance=template["instance"],
            instrument=instrument,
            risk_profile=template["risk_profile"],
            broker=broker,
            clock=BacktestClock(),
            store=s,
            mode=Mode.PAPER,
            execution_now=started_at,
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
            manage_exits=False,
        )
        processor.all_candles = candles
        pending = s.list_pending_order_intents(portfolio.id, instance.id)
        processor.pending_intents = pending
        processor._persisted_intents = {intent.id for intent in pending}
        if per_portfolio_eval:
            signal, _ = processor.evaluate_signal(candle_index)
            processor.process_candle(candle_index, shared_signal=signal)
        else:
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


def _latest_completed_timestamp(
    candles: list,
    timeframe: str,
    started_at: datetime,
) -> datetime | None:
    latest_completed_ts: datetime | None = None
    for candle in candles:
        if is_bar_complete(candle.timestamp, timeframe, started_at):
            latest_completed_ts = candle.timestamp
    return latest_completed_ts


def _process_timeframe_group(
    s: TradingStore,
    instrument,
    timeframe: str,
    group: list[dict],
    settings_dict: dict,
    started_at: datetime,
    broker: PaperBrokerAdapter,
    *,
    live_only: bool,
    historical_only: bool = False,
    time_budget_sec: float | None = None,
    deadline: float | None = None,
    per_portfolio_eval: bool = False,
) -> tuple[int, int, dict]:
    """Process missed completed candles. Live path always runs before historical."""
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

    latest_completed_ts = _latest_completed_timestamp(candles, timeframe, started_at)
    total_decisions = 0
    candles_processed = 0

    if historical_only:
        historical_indices = indices[:-1] if len(indices) > 1 else []
        if len(historical_indices) > MAX_HISTORICAL_DECISIONS_PER_RUN:
            historical_indices = historical_indices[:MAX_HISTORICAL_DECISIONS_PER_RUN]
        for candle_index in historical_indices:
            if deadline is not None and time.perf_counter() >= deadline:
                logger.info(
                    "Historical time budget reached for %s %s",
                    instrument.symbol,
                    timeframe,
                )
                break
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
                per_portfolio_eval=per_portfolio_eval,
            )
            candles_processed += 1
    else:
        live_idx = indices[-1]
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
            per_portfolio_eval=per_portfolio_eval,
        )
        candles_processed += 1

        if not live_only:
            historical_indices = indices[:-1]
            if len(historical_indices) > MAX_HISTORICAL_DECISIONS_PER_RUN:
                historical_indices = historical_indices[:MAX_HISTORICAL_DECISIONS_PER_RUN]
            for candle_index in historical_indices:
                if deadline is not None and time.perf_counter() >= deadline:
                    break
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
                    per_portfolio_eval=per_portfolio_eval,
                )
                candles_processed += 1

    tf_status = s.get_timeframe_execution_status(
        instrument.id, timeframe, instance_ids, started_at
    )
    return candles_processed, total_decisions, tf_status


def _process_experiment(
    s: TradingStore,
    entries: list[dict],
    symbols: list[str],
    timeframes: tuple[str, ...],
    settings_dict: dict,
    started_at: datetime,
    *,
    live_only: bool,
    historical_only: bool,
    time_budget_sec: float,
    deadline: float,
    per_portfolio_eval: bool = False,
) -> tuple[int, int, int, dict[str, dict], dict[str, dict], list[str]]:
    if not entries:
        return 0, 0, 0, {}, {}, []

    by_timeframe: dict[str, list[dict]] = defaultdict(list)
    for entry in entries:
        by_timeframe[entry["instance"].timeframe].append(entry)

    total_decisions = 0
    groups_evaluated = 0
    portfolios_touched = 0
    timeframe_status: dict[str, dict] = {}
    instrument_status: dict[str, dict] = {}
    skipped_reasons: list[str] = []

    for symbol in symbols:
        if time.perf_counter() >= deadline:
            logger.warning("Strategy cycle time budget exhausted during %s scan", symbol)
            break

        instrument = s.get_instrument_by_symbol(symbol)
        if not instrument:
            logger.warning("Instrument %s not found — skipping", symbol)
            continue

        broker = PaperBrokerAdapter(instrument.id, ExecutionAssumptions())
        symbol_tf_status: dict[str, dict] = {}

        for timeframe in timeframes:
            if time.perf_counter() >= deadline:
                break
            group = by_timeframe.get(timeframe, [])
            if not group:
                continue
            if per_portfolio_eval:
                group = [
                    e for e in group if e["instance"].instrument_id == instrument.id
                ]
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
                live_only=live_only,
                historical_only=historical_only,
                time_budget_sec=time_budget_sec,
                deadline=deadline,
                per_portfolio_eval=per_portfolio_eval,
            )
            symbol_tf_status[timeframe] = tf_status
            if tf_status.get("skipped"):
                reason = str(tf_status.get("skip_reason") or "skipped")
                skipped_reasons.append(f"{symbol}/{timeframe}:{reason}")
            if candles_processed:
                groups_evaluated += 1
                portfolios_touched += len(group) * candles_processed
                total_decisions += decisions
                _commit_progress(s)

        instrument_status[symbol] = symbol_tf_status
        for timeframe, st in symbol_tf_status.items():
            prev = timeframe_status.setdefault(timeframe, {"backlog": 0, "instances": 0})
            prev["backlog"] = int(prev.get("backlog", 0)) + int(st.get("backlog", 0))
            prev["instances"] = int(prev.get("instances", 0)) + int(st.get("instances", 0))

    return (
        groups_evaluated,
        portfolios_touched,
        total_decisions,
        timeframe_status,
        instrument_status,
        skipped_reasons,
    )


def _process_competition(
    s: TradingStore,
    started_at: datetime,
    *,
    run_id: str,
    live_only: bool,
    historical_only: bool = False,
    time_budget_sec: float,
) -> int:
    from quantara_engine.competition.constants import TIMEFRAME_ORDER
    from quantara_engine.competition.orb_constants import ORB_ASSETS, ORB_TIMEFRAME

    settings_dict = s.get_settings_dict()
    deadline = time.perf_counter() + time_budget_sec

    robot_a = s.list_competition_entries()
    (
        groups_a,
        touched_a,
        decisions_a,
        tf_a,
        inst_a,
        skipped_a,
    ) = _process_experiment(
        s,
        robot_a,
        _ordered_symbols(),
        TIMEFRAME_ORDER,
        settings_dict,
        started_at,
        live_only=live_only,
        historical_only=historical_only,
        time_budget_sec=time_budget_sec,
        deadline=deadline,
    )

    orb_entries = s.list_orb_competition_entries()
    (
        groups_b,
        touched_b,
        decisions_b,
        tf_b,
        inst_b,
        skipped_b,
    ) = _process_experiment(
        s,
        orb_entries,
        list(ORB_ASSETS),
        (ORB_TIMEFRAME,),
        settings_dict,
        started_at,
        live_only=live_only,
        historical_only=historical_only,
        time_budget_sec=time_budget_sec,
        deadline=deadline,
        per_portfolio_eval=True,
    )

    groups_evaluated = groups_a + groups_b
    portfolios_touched = touched_a + touched_b
    total_decisions = decisions_a + decisions_b
    skipped_reasons = skipped_a + skipped_b
    timeframe_status = {**tf_a, **{f"orb_{k}": v for k, v in tf_b.items()}}
    instrument_status = {**inst_a, **{f"orb_{k}": v for k, v in inst_b.items()}}
    entries_count = len(robot_a) + len(orb_entries)

    overall_backlog = sum(
        int(st.get("backlog", 0))
        for st in (*tf_a.values(), *tf_b.values())
    )
    duration_ms = round((time.perf_counter() - (deadline - time_budget_sec)) * 1000, 1)

    if groups_evaluated == 0:
        reason = "no_eligible_bars"
        if skipped_reasons:
            reason = skipped_reasons[0]
        s.update_worker_status(
            "strategy_runner",
            {
                "status": "waiting",
                "mode": "historical" if historical_only else "live",
                "last_run": started_at.isoformat(),
                "last_finish": datetime.now(timezone.utc).isoformat(),
                "duration_ms": duration_ms,
                "reason": reason,
                "competition_portfolios": entries_count,
                "timeframes": timeframe_status,
                "instruments": instrument_status,
                "jobs_pending": overall_backlog,
            },
        )
        s.save_worker_run(
            run_id=run_id,
            worker_name="strategy_runner",
            started_at=started_at,
            jobs_processed=0,
        )
        _commit_progress(s)
        return 0

    overall_status = "catching_up" if overall_backlog > 0 else "healthy"
    s.update_worker_status(
        "strategy_runner",
        {
            "status": overall_status,
            "mode": "historical" if historical_only else "live",
            "last_run": started_at.isoformat(),
            "last_finish": datetime.now(timezone.utc).isoformat(),
            "duration_ms": duration_ms,
            "jobs_pending": overall_backlog,
            "decisions": total_decisions,
            "competition_portfolios": entries_count,
            "timeframe_groups_evaluated": groups_evaluated,
            "timeframes": timeframe_status,
            "instruments": instrument_status,
            "last_evaluation_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    s.save_worker_run(
        run_id=run_id,
        worker_name="strategy_runner",
        started_at=started_at,
        jobs_processed=groups_evaluated,
    )
    logger.info(
        "run_strategy %s completed (%d candle-batches, %d portfolio-runs, %d decisions, backlog=%d)",
        "historical" if historical_only else "live",
        groups_evaluated,
        portfolios_touched,
        total_decisions,
        overall_backlog,
    )
    _commit_progress(s)
    return portfolios_touched


def _execute_strategy_cycle(
    *,
    live_only: bool,
    historical_only: bool,
    time_budget_sec: float,
    store: TradingStore | None = None,
) -> None:
    started_at = datetime.now(timezone.utc)
    run_id = str(uuid.uuid4())
    t0 = time.perf_counter()
    mode = "historical" if historical_only else "live"

    def _run(s: TradingStore) -> int:
        settings_dict = s.get_settings_dict()
        if not settings_dict.get("paper_trading_enabled", True):
            return 0

        _mark_cycle_started(s, run_id=run_id, started_at=started_at, mode=mode)
        expired_intents = s.cancel_stale_pending_intents(ACTIVE_COMPETITION_EXPERIMENT_ID, started_at)
        from quantara_engine.competition.orb_constants import ORB_COMPETITION_EXPERIMENT_ID

        expired_intents += s.cancel_stale_pending_intents(
            ORB_COMPETITION_EXPERIMENT_ID, started_at
        )
        if expired_intents:
            logger.info("Expired %d stale pending_execution intent(s)", expired_intents)
            _commit_progress(s)
        if not s.list_competition_entries():
            logger.warning("No active competition portfolios — strategy runner idle")
            s.update_worker_status(
                "strategy_runner",
                {
                    "status": "idle",
                    "mode": mode,
                    "last_run": started_at.isoformat(),
                    "reason": "no_competition_entries",
                },
            )
            s.save_worker_run(
                run_id=run_id,
                worker_name="strategy_runner",
                started_at=started_at,
                jobs_processed=0,
            )
            _commit_progress(s)
            return 0

        return _process_competition(
            s,
            started_at,
            run_id=run_id,
            live_only=live_only,
            historical_only=historical_only,
            time_budget_sec=time_budget_sec,
        )

    try:
        if store is not None:
            jobs = _run(store)
        else:
            with session_scope() as session:
                jobs = _run(TradingStore(session))
        duration_ms = round((time.perf_counter() - t0) * 1000, 1)
        logger.info(
            "run_strategy_%s_job finished in %.1fms (jobs=%d)",
            mode,
            duration_ms,
            jobs,
        )
    except Exception as exc:
        duration_ms = round((time.perf_counter() - t0) * 1000, 1)
        logger.exception("run_strategy_%s_job failed after %.1fms", mode, duration_ms)
        try:
            with session_scope() as session:
                s = TradingStore(session)
                s.update_worker_status(
                    "strategy_runner",
                    {
                        "status": "error",
                        "mode": mode,
                        "last_run": started_at.isoformat(),
                        "duration_ms": duration_ms,
                        "error": str(exc),
                    },
                )
                s.save_worker_run(
                    run_id=run_id,
                    worker_name="strategy_runner",
                    started_at=started_at,
                    status="failed",
                    errors={"message": str(exc)},
                )
        except Exception:
            logger.exception("Failed to persist strategy_runner error status")
        raise


def run_strategy_job(store: TradingStore | None = None) -> None:
    """Scheduled live path — newest eligible completed candle only, bounded runtime."""
    _execute_strategy_cycle(
        live_only=True,
        historical_only=False,
        time_budget_sec=LIVE_CYCLE_MAX_SECONDS,
        store=store,
    )


def run_strategy_historical_job(store: TradingStore | None = None) -> None:
    """Bounded historical catch-up — execution disabled, separate scheduler slot."""
    _execute_strategy_cycle(
        live_only=True,
        historical_only=True,
        time_budget_sec=HISTORICAL_CYCLE_MAX_SECONDS,
        store=store,
    )


def strategy_freshness_summary(store: TradingStore, now: datetime | None = None) -> dict:
    """Runtime freshness snapshot for API/UI — read-only helper."""
    now = now or datetime.now(timezone.utc)
    settings = store.get_settings_dict()
    runner = settings.get("worker_status:strategy_runner") or {}
    fetcher = settings.get("worker_status:data_fetcher") or {}

    last_eval = runner.get("last_evaluation_at") or runner.get("last_finish") or runner.get("last_run")
    eval_age_min = None
    if last_eval:
        try:
            eval_age_min = round(
                (now - datetime.fromisoformat(str(last_eval).replace("Z", "+00:00"))).total_seconds()
                / 60,
                1,
            )
        except ValueError:
            pass

    cycle_started = runner.get("cycle_started_at")
    running_stalled = False
    if runner.get("status") == "running" and cycle_started:
        try:
            started = datetime.fromisoformat(str(cycle_started).replace("Z", "+00:00"))
            running_stalled = (now - started).total_seconds() / 60 > STRATEGY_STALL_THRESHOLD_MINUTES
        except ValueError:
            pass

    market_ages: dict[str, float | None] = {}
    for sym in ("BTCUSD", "EURUSD", "XAUUSD"):
        inst = store.get_instrument_by_symbol(sym)
        if not inst:
            continue
        ts = store.latest_candle_timestamp(inst.id, "5m")
        market_ages[sym] = round((now - ts).total_seconds() / 60, 1) if ts else None

    backlog = int(runner.get("jobs_pending") or 0)
    healthy = (
        runner.get("status") in ("healthy", "catching_up", "waiting")
        and not running_stalled
        and (eval_age_min is None or eval_age_min < STRATEGY_STALL_THRESHOLD_MINUTES)
    )

    return {
        "healthy": healthy,
        "stalled": running_stalled or (
            eval_age_min is not None and eval_age_min >= STRATEGY_STALL_THRESHOLD_MINUTES
        ),
        "status": runner.get("status"),
        "mode": runner.get("mode"),
        "last_evaluation_at": last_eval,
        "evaluation_age_minutes": eval_age_min,
        "backlog": backlog,
        "market_candle_age_minutes": market_ages,
        "fetch_status": fetcher.get("status"),
    }
