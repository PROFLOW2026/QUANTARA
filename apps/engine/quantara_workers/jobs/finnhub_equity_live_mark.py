"""Scheduled Finnhub equity LIVE_MARK — ~1 minute during US RTH."""

from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime, timezone

from sqlalchemy.exc import OperationalError

from quantara_engine.db.session import session_scope
from quantara_engine.execution.equity_live_mark import run_finnhub_equity_live_marks
from quantara_engine.persistence.store import TradingStore
from quantara_workers.jobs.worker_run_helpers import save_scheduled_worker_run

logger = logging.getLogger(__name__)

MAX_DEADLOCK_RETRIES = 3
DEADLOCK_RETRY_BASE_SECONDS = 0.25


def finnhub_equity_live_mark_job(store: TradingStore | None = None) -> None:
    started_at = datetime.now(timezone.utc)
    t0 = time.perf_counter()

    def _run(s: TradingStore) -> dict:
        report = run_finnhub_equity_live_marks(s, now=started_at)
        s.session.commit()
        duration_ms = round((time.perf_counter() - t0) * 1000, 1)
        worker_status, _run_status = save_scheduled_worker_run(
            s,
            worker_name="finnhub_equity_live_mark",
            run_id=str(uuid.uuid4()),
            started_at=started_at,
            jobs_processed=int(report.get("fetches") or 0),
            report=report,
        )

        s.update_worker_status(
            "finnhub_equity_live_mark",
            {
                "status": worker_status,
                "last_run": started_at.isoformat(),
                "duration_ms": duration_ms,
                "fetches": report.get("fetches", 0),
                "applied": report.get("applied"),
                "fallbacks": report.get("fallbacks"),
                "skip_reason": report.get("reason"),
                "errors": report.get("errors"),
            },
        )
        return report

    try:
        last_exc: Exception | None = None
        for attempt in range(MAX_DEADLOCK_RETRIES):
            try:
                if store is not None:
                    _run(store)
                else:
                    with session_scope() as session:
                        _run(TradingStore(session))
                return
            except OperationalError as exc:
                last_exc = exc
                if "deadlock" not in str(exc).lower() or attempt >= MAX_DEADLOCK_RETRIES - 1:
                    raise
                time.sleep(DEADLOCK_RETRY_BASE_SECONDS * (2**attempt))
        if last_exc is not None:
            raise last_exc
    except Exception as exc:
        logger.exception("finnhub_equity_live_mark_job failed")
        try:
            with session_scope() as session:
                s = TradingStore(session)
                s.update_worker_status(
                    "finnhub_equity_live_mark",
                    {
                        "status": "error",
                        "last_run": started_at.isoformat(),
                        "error": str(exc),
                    },
                )
                s.save_worker_run(
                    run_id=str(uuid.uuid4()),
                    worker_name="finnhub_equity_live_mark",
                    started_at=started_at,
                    status="failed",
                    errors={"message": str(exc)},
                )
                s.session.commit()
        except Exception:
            logger.exception("Failed to persist finnhub_equity_live_mark error status")
        raise
