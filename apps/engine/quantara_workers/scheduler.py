"""APScheduler-based worker scheduler."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from quantara_workers.jobs.fetch_data import fetch_bulk_job, fetch_live_job
from quantara_workers.jobs.position_management import position_management_job
from quantara_workers.jobs.run_backtest import run_backtest_job
from quantara_workers.jobs.run_strategy import run_strategy_job
from quantara_workers.jobs.snapshot import snapshot_job

logger = logging.getLogger(__name__)

_JOB_OPTS = {
    "max_instances": 1,
    "coalesce": True,
    "misfire_grace_time": 120,
}


class WorkerScheduler:
    def __init__(self) -> None:
        self.scheduler = BackgroundScheduler(timezone="UTC")
        self._started = False

    def register_jobs(self) -> None:
        # Priority: ingest -> manage exits/marks -> strategy -> snapshot -> bulk history
        self.scheduler.add_job(
            fetch_live_job,
            CronTrigger(minute="*/5", second=0),
            id="fetch_live",
            replace_existing=True,
            **_JOB_OPTS,
        )
        self.scheduler.add_job(
            position_management_job,
            CronTrigger(minute="*/5", second=20),
            id="position_management",
            replace_existing=True,
            **_JOB_OPTS,
        )
        self.scheduler.add_job(
            run_strategy_job,
            CronTrigger(minute="*/5", second=35),
            id="run_strategy",
            replace_existing=True,
            **_JOB_OPTS,
        )
        self.scheduler.add_job(
            snapshot_job,
            CronTrigger(minute="*/5", second=50),
            id="snapshot",
            replace_existing=True,
            **_JOB_OPTS,
        )
        self.scheduler.add_job(
            fetch_bulk_job,
            "interval",
            minutes=30,
            id="fetch_bulk",
            replace_existing=True,
            **_JOB_OPTS,
        )

    def start(self) -> None:
        if not self._started:
            self.register_jobs()
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
        )
