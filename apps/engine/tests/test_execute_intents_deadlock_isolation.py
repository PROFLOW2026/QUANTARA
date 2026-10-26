"""execute_intents — Live Sim resume isolated from Paper failures."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from quantara_workers.jobs.execute_intents import execute_intents_job


def test_live_sim_resume_runs_when_paper_cycle_raises():
    resume_report = {
        "pending_found": 1,
        "resumed": 1,
        "expired": 0,
        "not_ready": 0,
        "broker_rejected": 0,
        "filled": 0,
    }
    status_calls: list[dict] = []

    def capture_status(worker_name, payload):
        status_calls.append(payload)

    with patch(
        "quantara_workers.jobs.execute_intents.session_scope",
    ) as scope:
        sessions = []

        def make_session():
            session = MagicMock()
            sessions.append(session)
            return session

        scope.side_effect = lambda: MagicMock(
            __enter__=lambda s: make_session(),
            __exit__=lambda s, *a: None,
        )
        with patch(
            "quantara_workers.jobs.execute_intents.TradingStore",
        ) as store_cls:
            store = MagicMock()
            store.get_settings_dict.return_value = {
                "paper_trading_enabled": True,
                "competition_execute_intents_enabled": True,
                "trading_control": {},
            }
            store_cls.return_value = store
            store.update_worker_status = MagicMock(side_effect=capture_status)
            store.save_worker_run = MagicMock()

            with patch(
                "quantara_workers.jobs.execute_intents.execute_paper_pending_intents_only",
                side_effect=RuntimeError("paper deadlock simulated"),
            ), patch(
                "quantara_workers.jobs.execute_intents._run_live_sim_resume",
                return_value=(resume_report, None),
            ):
                execute_intents_job()

    assert status_calls
    last = status_calls[-1]
    assert last["status"] == "degraded"
    assert last["live_sim_resumed"] == 1
    assert "paper_error" in last


def test_paper_disabled_still_resumes_live_sim():
    status_calls: list[dict] = []

    with patch(
        "quantara_workers.jobs.execute_intents._run_live_sim_resume",
        return_value=({"resumed": 2, "pending_found": 2}, None),
    ), patch(
        "quantara_workers.jobs.execute_intents.session_scope",
    ) as scope:
        session = MagicMock()
        scope.return_value.__enter__ = lambda s: session
        scope.return_value.__exit__ = lambda s, *a: None

        with patch("quantara_workers.jobs.execute_intents.TradingStore") as store_cls:
            store = MagicMock()
            store.get_settings_dict.return_value = {"paper_trading_enabled": False}
            store_cls.return_value = store

            def capture_status(_n, payload):
                status_calls.append(payload)

            store.update_worker_status = MagicMock(side_effect=capture_status)
            store.save_worker_run = MagicMock()

            execute_intents_job()

    assert status_calls[-1]["status"] == "healthy"
    assert status_calls[-1]["live_sim_resumed"] == 2
