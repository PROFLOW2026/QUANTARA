"""Scheduled 1-minute BTC/ETH position protection — independent of strategy cadence."""

from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime, timezone

from sqlalchemy.exc import OperationalError

from quantara_engine.db.session import session_scope
from quantara_engine.execution.crypto_fast_protection import run_crypto_fast_protection
from quantara_engine.persistence.store import TradingStore

logger = logging.getLogger(__name__)

MAX_DEADLOCK_RETRIES = 3
DEADLOCK_RETRY_BASE_SECONDS = 0.25


def crypto_fast_protection_job(store: TradingStore | None = None) -> None:
    started_at = datetime.now(timezone.utc)
    t0 = time.perf_counter()

    def _run(s: TradingStore) -> dict:
        report = run_crypto_fast_protection(s, started_at)
        s.session.commit()
        duration_ms = round((time.perf_counter() - t0) * 1000, 1)
        status = report.get("status", "success")
        worker_status = "healthy" if status in ("success", "skipped") else "degraded"

        s.update_worker_status(
            "crypto_fast_protection",
            {
                "status": worker_status,
                "last_run": started_at.isoformat(),
                "duration_ms": duration_ms,
                "fetches": report.get("fetches", 0),
                "symbols": report.get("symbols"),
                "research": report.get("research"),
                "live_sim": report.get("live_sim"),
                "skip_reason": report.get("reason"),
            },
        )
        s.save_worker_run(
            run_id=str(uuid.uuid4()),
            worker_name="crypto_fast_protection",
            started_at=started_at,
            jobs_processed=int(report.get("fetches") or 0),
            status=status,
            errors=None,
        )
        logger.info(
            "crypto_fast_protection %s (fetches=%s, research_closed=%s, live_sim_closed=%s, %.1fms)",
            status,
            report.get("fetches", 0),
            (report.get("research") or {}).get("closed", 0),
            (report.get("live_sim") or {}).get("closed", 0),
            duration_ms,
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
                msg = str(exc).lower()
                if "deadlock" not in msg or attempt >= MAX_DEADLOCK_RETRIES - 1:
                    raise
                delay = DEADLOCK_RETRY_BASE_SECONDS * (2**attempt)
                logger.warning(
                    "crypto_fast_protection deadlock (attempt %d/%d), retry in %.2fs: %s",
                    attempt + 1,
                    MAX_DEADLOCK_RETRIES,
                    delay,
                    exc,
                )
                time.sleep(delay)
        if last_exc is not None:
            raise last_exc
    except Exception as exc:
        logger.exception("crypto_fast_protection_job failed")
        try:
            with session_scope() as session:
                s = TradingStore(session)
                s.update_worker_status(
                    "crypto_fast_protection",
                    {
                        "status": "error",
                        "last_run": started_at.isoformat(),
                        "error": str(exc),
                    },
                )
                s.save_worker_run(
                    run_id=str(uuid.uuid4()),
                    worker_name="crypto_fast_protection",
                    started_at=started_at,
                    status="failed",
                    errors={"message": str(exc)},
                )
        except Exception:
            logger.exception("Failed to persist crypto_fast_protection error status")
        raise
