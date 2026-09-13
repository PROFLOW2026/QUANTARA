"""Scheduled Finnhub validation watchdog — ~5 minute cadence."""

from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime, timezone

from sqlalchemy.exc import OperationalError

from quantara_engine.db.session import session_scope
from quantara_engine.market_data.finnhub_validation import run_finnhub_validation
from quantara_engine.persistence.store import TradingStore
from quantara_workers.jobs.worker_run_helpers import save_scheduled_worker_run

logger = logging.getLogger(__name__)

MAX_DEADLOCK_RETRIES = 3
DEADLOCK_RETRY_BASE_SECONDS = 0.25


def finnhub_validation_job(store: TradingStore | None = None) -> None:
    started_at = datetime.now(timezone.utc)
    t0 = time.perf_counter()

    def _run(s: TradingStore) -> dict:
        report = run_finnhub_validation(s, now=started_at)
        s.session.commit()
        duration_ms = round((time.perf_counter() - t0) * 1000, 1)
        worker_status, _run_status = save_scheduled_worker_run(
            s,
            worker_name="finnhub_validation",
            run_id=str(uuid.uuid4()),
            started_at=started_at,
            jobs_processed=int(report.get("checked") or 0),
            report=report,
        )

        s.update_worker_status(
            "finnhub_validation",
            {
                "status": worker_status,
                "last_run": started_at.isoformat(),
                "duration_ms": duration_ms,
                "checked": report.get("checked", 0),
                "divergences": report.get("divergences", 0),
                "skip_reason": report.get("reason"),
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
        logger.exception("finnhub_validation_job failed")
        try:
            with session_scope() as session:
                s = TradingStore(session)
                s.update_worker_status(
                    "finnhub_validation",
                    {
                        "status": "error",
                        "last_run": started_at.isoformat(),
                        "error": str(exc),
                    },
                )
                s.save_worker_run(
                    run_id=str(uuid.uuid4()),
                    worker_name="finnhub_validation",
                    started_at=started_at,
                    status="failed",
                    errors={"message": str(exc)},
                )
                s.session.commit()
        except Exception:
            logger.exception("Failed to persist finnhub_validation error status")
        raise
