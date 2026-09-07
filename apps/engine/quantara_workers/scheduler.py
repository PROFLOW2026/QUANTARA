"""APScheduler-based worker scheduler."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from apscheduler.schedulers.background import BackgroundScheduler

from quantara_workers.jobs.fetch_data import fetch_data_job
from quantara_workers.jobs.run_backtest import run_backtest_job
from quantara_workers.jobs.run_strategy import run_strategy_job
from quantara_workers.jobs.snapshot import snapshot_job

logger = logging.getLogger(__name__)


class WorkerScheduler:
    def __init__(self) -> None:
        self.scheduler = BackgroundScheduler(timezone="UTC")
        self._started = False

    def register_jobs(self) -> None:
        self.scheduler.add_job(
            fetch_data_job,
            "interval",
            minutes=5,
            id="fetch_data",
            replace_existing=True,
        )
        self.scheduler.add_job(
            run_strategy_job,
            "interval",
            minutes=5,
            id="run_strategy",
            replace_existing=True,
        )
        self.scheduler.add_job(
            snapshot_job,
            "interval",
            minutes=5,
            id="snapshot",
            replace_existing=True,
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
