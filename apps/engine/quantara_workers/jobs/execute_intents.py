"""Scheduled lightweight execution of pending Paper intents."""

from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime, timezone

from quantara_engine.competition.constants import ACTIVE_COMPETITION_EXPERIMENT_ID
from quantara_engine.competition.orb_constants import ORB_COMPETITION_EXPERIMENT_ID
from quantara_engine.db.session import session_scope
from quantara_engine.execution.live_intents import execute_pending_intents_live
from quantara_engine.persistence.store import TradingStore
from quantara_engine.trading.trading_controls import allows_new_entries, load_trading_control

logger = logging.getLogger(__name__)


def execute_intents_job(store: TradingStore | None = None) -> None:
    started_at = datetime.now(timezone.utc)
    t0 = time.perf_counter()

    def _run(s: TradingStore) -> dict:
        settings = s.get_settings_dict()
        if not settings.get("paper_trading_enabled", True):
            return {"status": "skipped", "reason": "paper_trading_disabled"}
        if not allows_new_entries(load_trading_control(settings)):
            expired = s.cancel_stale_pending_intents(ACTIVE_COMPETITION_EXPERIMENT_ID, started_at)
            expired += s.cancel_stale_pending_intents(ORB_COMPETITION_EXPERIMENT_ID, started_at)
            return {
                "status": "skipped",
                "reason": "new_entries_blocked",
                "expired_intents": expired,
            }
        report = execute_pending_intents_live(s, started_at)
        duration_ms = round((time.perf_counter() - t0) * 1000, 1)
        status = "healthy" if not report.get("errors") else "degraded"
        s.update_worker_status(
            "execute_intents",
            {
                "status": status,
                "last_run": started_at.isoformat(),
                "duration_ms": duration_ms,
                **report,
            },
        )
        s.save_worker_run(
            run_id=str(uuid.uuid4()),
            worker_name="execute_intents",
            started_at=started_at,
            jobs_processed=report.get("fills_attempted", 0),
            status="success" if status == "healthy" else "partial",
            errors={"items": report.get("errors")} if report.get("errors") else None,
        )
        logger.info(
            "execute_intents completed (expired=%d fills=%d checked=%d %.1fms)",
            report.get("expired_intents", 0),
            report.get("fills_attempted", 0),
            report.get("portfolios_checked", 0),
            duration_ms,
        )
        return report

    try:
        if store is not None:
            _run(store)
        else:
            with session_scope() as session:
                _run(TradingStore(session))
    except Exception as exc:
        logger.exception("execute_intents_job failed")
        try:
            with session_scope() as session:
                s = TradingStore(session)
                s.update_worker_status(
                    "execute_intents",
                    {
                        "status": "error",
                        "last_run": started_at.isoformat(),
                        "error": str(exc),
                    },
                )
        except Exception:
            logger.exception("Failed to persist execute_intents error status")
        raise
