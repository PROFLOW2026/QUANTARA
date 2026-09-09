"""Scheduled open-position SL/TP management — independent of strategy runner."""

from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime, timezone

from quantara_engine.db.session import session_scope
from quantara_engine.execution.position_management import manage_all_open_positions
from quantara_engine.persistence.store import TradingStore

logger = logging.getLogger(__name__)


def position_management_job(store: TradingStore | None = None) -> None:
    started_at = datetime.now(timezone.utc)
    t0 = time.perf_counter()

    def _run(s: TradingStore) -> dict:
        settings = s.get_settings_dict()
        if not settings.get("paper_trading_enabled", True):
            return {"status": "skipped", "reason": "paper_trading_disabled"}

        report = manage_all_open_positions(s, started_at)
        duration_ms = round((time.perf_counter() - t0) * 1000, 1)
        status = "healthy" if not report.get("errors") else "degraded"
        s.update_worker_status(
            "position_management",
            {
                "status": status,
                "last_run": started_at.isoformat(),
                "duration_ms": duration_ms,
                "positions_checked": report.get("positions_checked", 0),
                "exits": report.get("exits", 0),
                "errors": report.get("errors") or None,
            },
        )
        s.save_worker_run(
            run_id=str(uuid.uuid4()),
            worker_name="position_management",
            started_at=started_at,
            jobs_processed=report.get("positions_checked", 0),
            status="success" if status == "healthy" else "error",
            errors={"items": report.get("errors")} if report.get("errors") else None,
        )
        logger.info(
            "position_management completed (%d checked, %d exits, %.1fms)",
            report.get("positions_checked", 0),
            report.get("exits", 0),
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
                    status="error",
                    errors={"message": str(exc)},
                )
        except Exception:
            logger.exception("Failed to persist position_management error status")
        raise
