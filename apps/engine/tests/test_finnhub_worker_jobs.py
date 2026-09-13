"""Finnhub scheduled worker job bookkeeping tests."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from quantara_workers.jobs.finnhub_equity_live_mark import finnhub_equity_live_mark_job
from quantara_workers.jobs.finnhub_validation import finnhub_validation_job
from quantara_workers.jobs.worker_run_helpers import (
    derive_worker_run_fields,
    save_scheduled_worker_run,
)


class RecordingStore:
    def __init__(self):
        self.session = MagicMock()
        self.worker_status: dict | None = None
        self.worker_runs: list[dict] = []

    def update_worker_status(self, worker_name: str, status: dict) -> None:
        self.worker_status = {"worker_name": worker_name, **status}

    def save_worker_run(self, **kwargs) -> None:
        self.worker_runs.append(kwargs)


def test_save_worker_run_canonical_signature_has_no_jobs_failed():
    import inspect

    from quantara_engine.persistence.store import TradingStore

    sig = inspect.signature(TradingStore.save_worker_run)
    assert "jobs_failed" not in sig.parameters
    assert "duration_ms" not in sig.parameters
    assert "metadata" not in sig.parameters
    assert "errors" in sig.parameters


def test_derive_worker_run_fields_success():
    worker_status, run_status, errors = derive_worker_run_fields(
        {"status": "success", "fetches": 4, "errors": []}
    )
    assert worker_status == "healthy"
    assert run_status == "success"
    assert errors is None


def test_derive_worker_run_fields_partial_symbol_failures():
    worker_status, run_status, errors = derive_worker_run_fields(
        {
            "status": "success",
            "errors": ["NVDA:timeout", "TSLA:empty_quote"],
            "fallbacks": ["NVDA"],
        }
    )
    assert worker_status == "degraded"
    assert run_status == "partial"
    assert errors == {
        "symbols": ["NVDA:timeout", "TSLA:empty_quote"],
        "fallbacks": ["NVDA"],
    }


def test_derive_worker_run_fields_outside_rth_skip():
    worker_status, run_status, errors = derive_worker_run_fields(
        {"status": "skipped", "reason": "outside_us_rth", "fetches": 0}
    )
    assert worker_status == "healthy"
    assert run_status == "skipped"
    assert errors is None


def test_derive_worker_run_fields_validation_divergence():
    worker_status, run_status, errors = derive_worker_run_fields(
        {"status": "success", "checked": 8, "divergences": 2}
    )
    assert worker_status == "degraded"
    assert run_status == "partial"
    assert errors == {"divergences": 2}


def test_save_scheduled_worker_run_uses_canonical_contract():
    store = RecordingStore()
    started_at = datetime.now(timezone.utc)

    worker_status, run_status = save_scheduled_worker_run(
        store,
        worker_name="finnhub_equity_live_mark",
        run_id="run-1",
        started_at=started_at,
        jobs_processed=3,
        report={"status": "success", "errors": [], "fetches": 3},
    )

    assert worker_status == "healthy"
    assert run_status == "success"
    assert len(store.worker_runs) == 1
    run = store.worker_runs[0]
    assert run["worker_name"] == "finnhub_equity_live_mark"
    assert run["jobs_processed"] == 3
    assert run["status"] == "success"
    assert run["errors"] is None
    assert "jobs_failed" not in run


@patch("quantara_workers.jobs.finnhub_equity_live_mark.run_finnhub_equity_live_marks")
def test_finnhub_equity_live_mark_job_full_success(run_mock):
    run_mock.return_value = {
        "status": "success",
        "fetches": 4,
        "applied": ["NVDA", "TSLA"],
        "fallbacks": [],
        "errors": [],
    }
    store = RecordingStore()

    finnhub_equity_live_mark_job(store)

    assert store.worker_status["status"] == "healthy"
    assert store.worker_runs[0]["status"] == "success"
    assert store.worker_runs[0]["jobs_processed"] == 4


@patch("quantara_workers.jobs.finnhub_equity_live_mark.run_finnhub_equity_live_marks")
def test_finnhub_equity_live_mark_job_partial_failure(run_mock):
    run_mock.return_value = {
        "status": "success",
        "fetches": 4,
        "applied": ["NVDA"],
        "fallbacks": ["TSLA"],
        "errors": ["TSLA:stale_quote"],
    }
    store = RecordingStore()

    finnhub_equity_live_mark_job(store)

    assert store.worker_status["status"] == "degraded"
    assert store.worker_runs[0]["status"] == "partial"
    assert store.worker_runs[0]["errors"]["symbols"] == ["TSLA:stale_quote"]


@patch("quantara_workers.jobs.finnhub_equity_live_mark.run_finnhub_equity_live_marks")
def test_finnhub_equity_live_mark_job_outside_rth_skip(run_mock):
    run_mock.return_value = {
        "status": "skipped",
        "reason": "outside_us_rth",
        "fetches": 0,
    }
    store = RecordingStore()

    finnhub_equity_live_mark_job(store)

    assert store.worker_status["status"] == "healthy"
    assert store.worker_status["skip_reason"] == "outside_us_rth"
    assert store.worker_runs[0]["status"] == "skipped"
    assert store.worker_runs[0]["jobs_processed"] == 0


@patch("quantara_workers.jobs.finnhub_equity_live_mark.session_scope")
@patch("quantara_workers.jobs.finnhub_equity_live_mark.run_finnhub_equity_live_marks")
def test_finnhub_equity_live_mark_job_exception_records_failed_run(run_mock, session_scope_mock):
    run_mock.side_effect = RuntimeError("provider exploded")
    store = RecordingStore()
    session_scope_mock.return_value.__enter__.return_value = MagicMock()

    with patch(
        "quantara_workers.jobs.finnhub_equity_live_mark.TradingStore",
        return_value=store,
    ):
        with pytest.raises(RuntimeError, match="provider exploded"):
            finnhub_equity_live_mark_job(None)

    assert store.worker_status["status"] == "error"
    assert store.worker_runs[0]["status"] == "failed"


@patch("quantara_workers.jobs.finnhub_validation.run_finnhub_validation")
def test_finnhub_validation_job_no_typeerror(run_mock):
    run_mock.return_value = {
        "status": "success",
        "checked": 8,
        "divergences": 0,
    }
    store = RecordingStore()

    finnhub_validation_job(store)

    assert store.worker_runs[0]["status"] == "success"
    assert "jobs_failed" not in store.worker_runs[0]
