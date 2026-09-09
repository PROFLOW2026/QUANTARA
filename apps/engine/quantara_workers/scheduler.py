"""APScheduler-based worker scheduler with isolated executors per job class."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from apscheduler.executors.pool import ThreadPoolExecutor
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from quantara_workers.jobs.execute_intents import execute_intents_job
from quantara_workers.jobs.fetch_data import fetch_bulk_job, fetch_live_job
from quantara_workers.jobs.position_management import position_management_job
from quantara_workers.jobs.run_backtest import run_backtest_job
from quantara_workers.jobs.run_strategy import run_strategy_historical_job, run_strategy_job
from quantara_workers.jobs.snapshot import snapshot_job
from quantara_workers.scheduler_events import register_scheduler_listeners

logger = logging.getLogger(__name__)

# Ingest / housekeeping jobs — must never wait on strategy backlog.
_INGEST_OPTS = {
    "executor": "ingest",
    "max_instances": 1,
    "coalesce": True,
    "misfire_grace_time": 120,
}

# Live strategy — short, bounded; never coalesce missed cycles.
_LIVE_STRATEGY_OPTS = {
    "executor": "strategy_live",
    "max_instances": 1,
    "coalesce": False,
    "misfire_grace_time": 240,
}

# Historical catch-up — separate thread pool; cannot block live or ingest.
_HISTORICAL_STRATEGY_OPTS = {
    "executor": "strategy_historical",
    "max_instances": 1,
    "coalesce": True,
    "misfire_grace_time": 300,
}


class WorkerScheduler:
    def __init__(self) -> None:
        executors = {
            # Shared pool for fetch, position management, snapshot, bulk.
            "ingest": ThreadPoolExecutor(max_workers=4),
            # Dedicated single-worker pools so a long strategy run cannot
            # monopolize the ingest executor or block its own live schedule.
            "strategy_live": ThreadPoolExecutor(max_workers=1),
            "strategy_historical": ThreadPoolExecutor(max_workers=1),
        }
        self.scheduler = BackgroundScheduler(
            timezone="UTC",
            executors=executors,
            job_defaults={"max_instances": 1},
        )
        self._started = False

    def register_jobs(self) -> None:
        # Priority: ingest -> manage exits/marks -> live strategy -> snapshot -> bulk history
        self.scheduler.add_job(
            fetch_live_job,
            CronTrigger(minute="*/5", second=0),
            id="fetch_live",
            replace_existing=True,
            **_INGEST_OPTS,
        )
        self.scheduler.add_job(
            run_strategy_job,
            CronTrigger(minute="*/5", second=12),
            id="run_strategy",
            replace_existing=True,
            **_LIVE_STRATEGY_OPTS,
        )
        self.scheduler.add_job(
            execute_intents_job,
            CronTrigger(minute="*/5", second=25),
            id="execute_intents",
            replace_existing=True,
            **_INGEST_OPTS,
        )
        self.scheduler.add_job(
            position_management_job,
            CronTrigger(minute="*/5", second=38),
            id="position_management",
            replace_existing=True,
            **_INGEST_OPTS,
        )
        self.scheduler.add_job(
            snapshot_job,
            CronTrigger(minute="*/5", second=50),
            id="snapshot",
            replace_existing=True,
            **_INGEST_OPTS,
        )
        self.scheduler.add_job(
            fetch_bulk_job,
            "interval",
            minutes=30,
            id="fetch_bulk",
            replace_existing=True,
            **_INGEST_OPTS,
        )
        # Bounded historical catch-up — offset from live cycle; execution disabled.
        self.scheduler.add_job(
            run_strategy_historical_job,
            CronTrigger(minute="10,40", second=0),
            id="run_strategy_historical",
            replace_existing=True,
            **_HISTORICAL_STRATEGY_OPTS,
        )

    def start(self) -> None:
        if not self._started:
            self.register_jobs()
            register_scheduler_listeners(self.scheduler)
            self.scheduler.start()
            self._started = True
            logger.info("Worker scheduler started at %s", datetime.now(timezone.utc))

    def shutdown(self) -> None:
        if self._started:
            self.scheduler.shutdown(wait=False)
            self._started = False

    def enqueue_backtest(self, backtest_run_id: str) -> None:
        self.scheduler.add_job(
            run_backtest_job,
            args=[backtest_run_id],
            id=f"backtest-{backtest_run_id}",
            replace_existing=True,
            executor="ingest",
        )
