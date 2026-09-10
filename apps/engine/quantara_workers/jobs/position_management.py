"""Scheduled open-position SL/TP management — independent of strategy runner."""

from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime, timezone

from sqlalchemy.exc import OperationalError

from quantara_engine.db.session import session_scope
from quantara_engine.execution.position_management import manage_all_open_positions
from quantara_engine.persistence.store import TradingStore

logger = logging.getLogger(__name__)

MAX_DEADLOCK_RETRIES = 3
DEADLOCK_RETRY_BASE_SECONDS = 0.25


def _run_status(report: dict) -> tuple[str, str]:
    failed = int(report.get("positions_failed") or 0)
    attempted = int(report.get("positions_attempted") or 0)
    persist_errors = any(e.get("phase") == "persist" for e in report.get("errors") or [])

    if persist_errors or (failed > 0 and failed == attempted):
        return "failed", "failed"
    if failed > 0:
        return "degraded", "partial"
    return "healthy", "success"


def position_management_job(store: TradingStore | None = None) -> None:
    started_at = datetime.now(timezone.utc)
    t0 = time.perf_counter()

    def _run(s: TradingStore) -> dict:
        settings = s.get_settings_dict()
        if not settings.get("paper_trading_enabled", True):
            return {"status": "skipped", "reason": "paper_trading_disabled"}

        report = manage_all_open_positions(s, started_at)
        s.session.commit()
        duration_ms = round((time.perf_counter() - t0) * 1000, 1)
        worker_status, run_status = _run_status(report)

        s.update_worker_status(
            "position_management",
            {
                "status": worker_status,
                "last_run": started_at.isoformat(),
                "duration_ms": duration_ms,
                "positions_attempted": report.get("positions_attempted", 0),
                "positions_successful": report.get("positions_successful", 0),
                "positions_failed": report.get("positions_failed", 0),
                "positions_closed": report.get("positions_closed", 0),
                "positions_marked": report.get("positions_marked", 0),
                "stop_iteration": report.get("stop_iteration", 0),
                "timing_ms": report.get("timing_ms"),
                "errors": report.get("errors") or None,
            },
        )
        s.save_worker_run(
            run_id=str(uuid.uuid4()),
            worker_name="position_management",
            started_at=started_at,
            jobs_processed=int(report.get("positions_successful") or 0),
            status=run_status,
            errors={"items": report.get("errors")} if report.get("errors") else None,
        )
        logger.info(
            "position_management %s (%d/%d ok, %d closed, %d marked, %.1fms)",
            run_status,
            report.get("positions_successful", 0),
            report.get("positions_attempted", 0),
            report.get("positions_closed", 0),
            report.get("positions_marked", 0),
            duration_ms,
        )
        if run_status == "failed":
            raise RuntimeError(f"position_management failed: {report.get('errors')}")
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
                msg = str(exc).lower()
                if "deadlock" not in msg or attempt >= MAX_DEADLOCK_RETRIES - 1:
                    raise
                delay = DEADLOCK_RETRY_BASE_SECONDS * (2**attempt)
                logger.warning(
                    "position_management deadlock (attempt %d/%d), retry in %.2fs: %s",
                    attempt + 1,
                    MAX_DEADLOCK_RETRIES,
                    delay,
                    exc,
                )
                time.sleep(delay)
        if last_exc is not None:
            raise last_exc
    except Exception as exc:
        logger.exception("position_management_job failed")
        try:
            with session_scope() as session:
                s = TradingStore(session)
                s.update_worker_status(
                    "position_management",
                    {
                        "status": "error",
                        "last_run": started_at.isoformat(),
                        "error": str(exc),
                    },
                )
                s.save_worker_run(
                    run_id=str(uuid.uuid4()),
                    worker_name="position_management",
                    started_at=started_at,
                    status="failed",
                    errors={"message": str(exc)},
                )
        except Exception:
            logger.exception("Failed to persist position_management error status")
        raise
