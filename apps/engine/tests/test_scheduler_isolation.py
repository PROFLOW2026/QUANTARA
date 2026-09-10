"""Scheduler configuration regression tests."""

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from quantara_workers.scheduler import WorkerScheduler


def test_strategy_live_job_uses_dedicated_executor_and_no_coalesce():
    ws = WorkerScheduler()
    ws.register_jobs()
    job = ws.scheduler.get_job("run_strategy")
    assert job is not None
    assert job.executor == "strategy_live"
    assert job.coalesce is False
    assert job.max_instances == 1
    assert job.misfire_grace_time == 240


def test_fetch_live_uses_dedicated_executor_not_blocked_by_bulk():
    ws = WorkerScheduler()
    ws.register_jobs()
    live = ws.scheduler.get_job("fetch_live")
    bulk = ws.scheduler.get_job("fetch_bulk")
    assert live is not None
    assert bulk is not None
    assert live.executor == "fetch_live"
    assert bulk.executor == "fetch_bulk"
    assert live.executor != bulk.executor
    assert live.coalesce is False


def test_housekeeping_jobs_share_pool_separate_from_ingest():
    ws = WorkerScheduler()
    ws.register_jobs()
    for job_id in ("execute_intents", "position_management", "snapshot"):
        job = ws.scheduler.get_job(job_id)
        assert job is not None
        assert job.executor == "housekeeping"
        assert job.executor != "fetch_live"
        assert job.executor != "strategy_live"


def test_historical_strategy_job_isolated_from_live():
    ws = WorkerScheduler()
    ws.register_jobs()
    live = ws.scheduler.get_job("run_strategy")
    historical = ws.scheduler.get_job("run_strategy_historical")
    assert live is not None
    assert historical is not None
    assert live.executor == "strategy_live"
    assert historical.executor == "strategy_historical"
    assert live.executor != historical.executor


def test_scheduler_has_isolated_executors():
    ws = WorkerScheduler()
    ws.register_jobs()
    assert {
        "fetch_live",
        "fetch_bulk",
        "housekeeping",
        "strategy_live",
        "strategy_historical",
    }.issubset(set(ws.scheduler._executors.keys()))


def test_live_and_historical_triggers_are_offset():
    ws = WorkerScheduler()
    ws.register_jobs()
    live_trigger = ws.scheduler.get_job("run_strategy").trigger
    hist_trigger = ws.scheduler.get_job("run_strategy_historical").trigger
    assert isinstance(live_trigger, CronTrigger)
    assert isinstance(hist_trigger, CronTrigger)
    assert live_trigger.fields[7].expressions[0].first == 12
    assert hist_trigger.fields[6].expressions[0].first == 10
