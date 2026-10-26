"""Scheduled lightweight execution of pending Paper intents."""

from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime, timezone

from sqlalchemy.exc import OperationalError

from quantara_engine.competition.constants import ACTIVE_COMPETITION_EXPERIMENT_ID
from quantara_engine.competition.orb_constants import ORB_COMPETITION_EXPERIMENT_ID
from quantara_engine.db.session import session_scope
from quantara_engine.execution.live_intents import (
    _merge_live_sim_report,
    execute_paper_pending_intents_only,
    run_live_sim_allocation_resume,
)
from quantara_engine.persistence.store import TradingStore
from quantara_engine.trading.trading_controls import allows_new_entries, load_trading_control

logger = logging.getLogger(__name__)

MAX_DEADLOCK_RETRIES = 3
DEADLOCK_RETRY_BASE_SECONDS = 0.25
# When false, skip competition Paper intent fills but still resume Live Sim pending allocations.
COMPETITION_INTENT_EXECUTION_SETTING = "competition_execute_intents_enabled"


def _run_live_sim_resume(started_at: datetime) -> tuple[dict, str | None]:
    """Isolated session so Paper failures never block Live Sim recovery."""
    try:
        with session_scope() as session:
            s = TradingStore(session)
            report = run_live_sim_allocation_resume(s, started_at)
            session.commit()
            return report, None
    except Exception as exc:
        logger.exception("live_sim allocation resume failed")
        return {}, str(exc)


def execute_intents_job(store: TradingStore | None = None) -> None:
    started_at = datetime.now(timezone.utc)
    t0 = time.perf_counter()

    def _run_paper_cycle(s: TradingStore) -> dict:
        settings = s.get_settings_dict()
        if not settings.get("paper_trading_enabled", True):
            return {"status": "skipped", "reason": "paper_trading_disabled"}
        # Default off: Live Sim resume is the critical path; Paper fills are optional Research work.
        if not settings.get(COMPETITION_INTENT_EXECUTION_SETTING, False):
            return {"status": "skipped", "reason": "competition_execute_intents_disabled"}
        if not allows_new_entries(load_trading_control(settings)):
            expired = s.cancel_stale_pending_intents(ACTIVE_COMPETITION_EXPERIMENT_ID, started_at)
            expired += s.cancel_stale_pending_intents(ORB_COMPETITION_EXPERIMENT_ID, started_at)
            return {
                "status": "skipped",
                "reason": "new_entries_blocked",
                "expired_intents": expired,
            }
        return execute_paper_pending_intents_only(s, started_at)

    paper_report: dict = {"status": "skipped", "reason": "not_run"}
    paper_error: str | None = None

    if store is not None:
        try:
            paper_report = _run_paper_cycle(store)
            store.session.commit()
        except Exception as exc:
            store.session.rollback()
            paper_error = str(exc)
            logger.exception("execute_intents paper cycle failed")
    else:
        for attempt in range(MAX_DEADLOCK_RETRIES):
            try:
                with session_scope() as session:
                    paper_report = _run_paper_cycle(TradingStore(session))
                    session.commit()
                paper_error = None
                break
            except OperationalError as exc:
                paper_error = str(exc)
                if "deadlock" not in paper_error.lower() or attempt >= MAX_DEADLOCK_RETRIES - 1:
                    logger.exception(
                        "execute_intents paper cycle failed (attempt %d)", attempt + 1
                    )
                    break
                time.sleep(DEADLOCK_RETRY_BASE_SECONDS * (2**attempt))
            except Exception as exc:
                paper_error = str(exc)
                logger.exception("execute_intents paper cycle failed")
                break

    live_sim_report, live_sim_error = _run_live_sim_resume(started_at)
    duration_ms = round((time.perf_counter() - t0) * 1000, 1)

    merged = dict(paper_report)
    _merge_live_sim_report(merged, live_sim_report)

    if live_sim_error:
        status = "error"
        merged["live_sim_error"] = live_sim_error
    elif paper_error:
        status = "degraded"
        merged["paper_error"] = paper_error
    elif merged.get("errors"):
        status = "degraded"
    elif merged.get("status") == "skipped":
        status = "healthy"
    else:
        status = "healthy"

    try:
        with session_scope() as session:
            s = TradingStore(session)
            s.update_worker_status(
                "execute_intents",
                {
                    **merged,
                    "status": status,
                    "last_run": started_at.isoformat(),
                    "duration_ms": duration_ms,
                    "paper_status": merged.get("status"),
                    "paper_reason": merged.get("reason"),
                },
            )
            s.save_worker_run(
                run_id=str(uuid.uuid4()),
                worker_name="execute_intents",
                started_at=started_at,
                jobs_processed=merged.get("fills_attempted", 0)
                + merged.get("live_sim_resumed", 0),
                status="success" if status == "healthy" else "partial",
                errors={
                    "items": merged.get("errors"),
                    "paper_error": paper_error,
                    "live_sim_error": live_sim_error,
                }
                if (merged.get("errors") or paper_error or live_sim_error)
                else None,
            )
            session.commit()
    except Exception:
        logger.exception("Failed to persist execute_intents status")

    logger.info(
        "execute_intents completed status=%s paper_fills=%d live_sim=%d %.1fms",
        status,
        merged.get("fills_attempted", 0),
        merged.get("live_sim_resumed", 0),
        duration_ms,
    )

    if live_sim_error:
        raise RuntimeError(live_sim_error)
