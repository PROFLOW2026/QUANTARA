"""Shared bookkeeping helpers for scheduled worker jobs."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from quantara_engine.persistence.store import TradingStore


def derive_worker_run_fields(report: dict[str, Any]) -> tuple[str, str, dict[str, Any] | None]:
    """Map a job report to worker_status, persisted run status, and optional errors."""
    report_status = str(report.get("status", "success")).lower()

    if report_status in {"skipped", "waiting"}:
        return "healthy", "skipped", None

    symbol_errors = report.get("errors") or []
    if symbol_errors:
        return (
            "degraded",
            "partial",
            {
                "symbols": list(symbol_errors),
                "fallbacks": report.get("fallbacks"),
            },
        )

    divergences = int(report.get("divergences") or 0)
    if divergences > 0:
        return "degraded", "partial", {"divergences": divergences}

    if report_status in {"success", "ok", "healthy"}:
        return "healthy", "success", None

    return "degraded", report_status, {"report_status": report_status}


def save_scheduled_worker_run(
    store: TradingStore,
    *,
    worker_name: str,
    run_id: str,
    started_at: datetime,
    jobs_processed: int,
    report: dict[str, Any],
) -> tuple[str, str]:
    """Persist worker run using the canonical TradingStore.save_worker_run contract."""
    worker_status, run_status, errors = derive_worker_run_fields(report)
    store.save_worker_run(
        run_id=run_id,
        worker_name=worker_name,
        started_at=started_at,
        jobs_processed=jobs_processed,
        status=run_status,
        errors=errors,
    )
    return worker_status, run_status
