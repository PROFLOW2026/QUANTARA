"""APScheduler job lifecycle observability — persisted for post-mortems."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from apscheduler.events import (
    EVENT_JOB_ERROR,
    EVENT_JOB_EXECUTED,
    EVENT_JOB_MAX_INSTANCES,
    EVENT_JOB_MISSED,
    EVENT_JOB_SUBMITTED,
    JobExecutionEvent,
    JobSubmissionEvent,
)

logger = logging.getLogger(__name__)

SCHEDULER_EVENTS_KEY = "worker_status:scheduler_events"
MAX_EVENT_LOG = 40


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _append_event(event_type: str, payload: dict[str, Any]) -> None:
    """Best-effort append to settings; never raise into the scheduler thread."""
    try:
        from quantara_engine.db.session import session_scope
        from quantara_engine.persistence.store import TradingStore

        entry = {"at": _now_iso(), "event": event_type, **payload}
        with session_scope() as session:
            store = TradingStore(session)
            settings = store.get_settings_dict()
            log = list(settings.get(SCHEDULER_EVENTS_KEY) or [])
            log.append(entry)
            store.update_settings(
                SCHEDULER_EVENTS_KEY,
                log[-MAX_EVENT_LOG:],
                description="Recent APScheduler job lifecycle events",
            )
    except Exception:
        logger.debug("Failed to persist scheduler event %s", event_type, exc_info=True)


def scheduler_event_listener(event: JobExecutionEvent | JobSubmissionEvent) -> None:
    job_id = getattr(event, "job_id", None)
    code = event.code

    if code == EVENT_JOB_SUBMITTED:
        scheduled = getattr(event, "scheduled_run_times", None)
        payload = {
            "job_id": job_id,
            "scheduled_run_times": [t.isoformat() for t in scheduled] if scheduled else [],
        }
        logger.info("apscheduler submitted job_id=%s scheduled=%s", job_id, payload["scheduled_run_times"])
        _append_event("submitted", payload)
        return

    if code == EVENT_JOB_MAX_INSTANCES:
        payload = {"job_id": job_id}
        logger.warning("apscheduler max_instances reached job_id=%s — invocation skipped", job_id)
        _append_event("max_instances", payload)
        return

    if code == EVENT_JOB_MISSED:
        payload = {
            "job_id": job_id,
            "scheduled_run_time": event.scheduled_run_time.isoformat()
            if getattr(event, "scheduled_run_time", None)
            else None,
        }
        logger.warning("apscheduler misfire job_id=%s scheduled=%s", job_id, payload["scheduled_run_time"])
        _append_event("missed", payload)
        return

    if code == EVENT_JOB_EXECUTED:
        retval = getattr(event, "retval", None)
        payload = {
            "job_id": job_id,
            "scheduled_run_time": event.scheduled_run_time.isoformat()
            if getattr(event, "scheduled_run_time", None)
            else None,
            "finished_at": _now_iso(),
            "returned": retval is not None,
        }
        logger.info("apscheduler executed job_id=%s scheduled=%s", job_id, payload["scheduled_run_time"])
        _append_event("executed", payload)
        return

    if code == EVENT_JOB_ERROR:
        payload = {
            "job_id": job_id,
            "scheduled_run_time": event.scheduled_run_time.isoformat()
            if getattr(event, "scheduled_run_time", None)
            else None,
            "exception": repr(getattr(event, "exception", None)),
            "traceback": getattr(event, "traceback", None),
        }
        logger.error("apscheduler error job_id=%s: %s", job_id, payload["exception"])
        _append_event("error", payload)


def register_scheduler_listeners(scheduler) -> None:
    for code in (
        EVENT_JOB_SUBMITTED,
        EVENT_JOB_EXECUTED,
        EVENT_JOB_ERROR,
        EVENT_JOB_MISSED,
        EVENT_JOB_MAX_INSTANCES,
    ):
        scheduler.add_listener(scheduler_event_listener, code)
