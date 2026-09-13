"""Periodic learning-layer maintenance — observational only."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from quantara_engine.db.session import session_scope
from quantara_engine.learning.activation import ensure_trading_week_baseline
from quantara_engine.learning.daily_loss_shadow import refresh_daily_loss_shadows
from quantara_engine.persistence.store import TradingStore

logger = logging.getLogger(__name__)


def learning_maintenance_job(store: TradingStore | None = None) -> None:
    started_at = datetime.now(timezone.utc)

    def _run(s: TradingStore) -> None:
        baseline = ensure_trading_week_baseline(s, now=started_at)
        n = refresh_daily_loss_shadows(
            s,
            baseline_id=baseline["id"] if baseline else None,
            trade_date=started_at.date(),
        )
        s.update_worker_status(
            "learning_maintenance",
            {
                "status": "ok",
                "last_run": started_at.isoformat(),
                "baseline_id": baseline["id"] if baseline else None,
                "daily_loss_rows": n,
                "observational_only": True,
            },
        )
        s.save_worker_run(
            run_id=str(uuid.uuid4()),
            worker_name="learning_maintenance",
            started_at=started_at,
            jobs_processed=n,
        )

    try:
        if store is not None:
            _run(store)
        else:
            with session_scope() as session:
                _run(TradingStore(session))
    except Exception as exc:
        logger.exception("learning_maintenance_job failed")
        try:
            with session_scope() as session:
                s = TradingStore(session)
                s.update_worker_status(
                    "learning_maintenance",
                    {"status": "error", "last_run": started_at.isoformat(), "error": str(exc)},
                )
                s.save_worker_run(
                    run_id=str(uuid.uuid4()),
                    worker_name="learning_maintenance",
                    started_at=started_at,
                    status="failed",
                    errors={"message": str(exc)},
                )
        except Exception:
            logger.exception("Failed to persist learning_maintenance error status")
        raise
