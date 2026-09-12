"""Twelve Data provider health vs usage — separate concepts and bounded sync."""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.exc import OperationalError

from quantara_engine.market_data.adapters.twelvedata import TwelveDataError
from quantara_engine.market_data.credits import (
    DAILY_HARD_LIMIT,
    HEALTH_SETTINGS_KEY,
    HEALTH_SYNC_WALL_TIMEOUT_SECONDS,
    INTERNAL_GUARD_LIMIT,
    SETTINGS_KEY,
    _persist_health_success,
    mark_blocked,
    refresh_twelve_data_health,
    status_payload,
    sync_provider_usage,
)
from quantara_workers.scheduler import WorkerScheduler


def _store_with_state(credits: dict, health: dict | None = None) -> MagicMock:
    store = MagicMock()
    payload = {SETTINGS_KEY: credits}
    if health is not None:
        payload[HEALTH_SETTINGS_KEY] = health
    store.get_settings_dict.return_value = payload
    return store


def test_sync_provider_usage_reads_plan_daily_limit():
    store = _store_with_state({"date": "2026-09-12", "used": 0, "events": []})
    sync_provider_usage(
        store,
        {
            "daily_usage": 2,
            "plan_daily_limit": 800,
            "current_usage": 1,
            "plan_limit": 8,
        },
    )
    saved = store.update_settings.call_args[0][1]
    assert saved["provider_daily_usage"] == 2
    assert saved["provider_daily_limit"] == 800
    assert "health_status" not in saved


@patch("quantara_engine.market_data.credits._upsert_setting_dict")
@patch("quantara_engine.market_data.credits._read_setting_dict")
@patch("quantara_engine.market_data.credits.health_session_scope")
def test_persist_health_success_writes_separate_key(mock_scope, mock_read, mock_upsert):
    session = MagicMock()
    mock_scope.return_value.__enter__.return_value = session
    mock_read.return_value = None
    _persist_health_success({"daily_usage": 18, "plan_daily_limit": 800})
    mock_upsert.assert_called_once()
    key, state = mock_upsert.call_args[0][1], mock_upsert.call_args[0][2]
    assert key == HEALTH_SETTINGS_KEY
    assert state["provider_daily_usage"] == 18
    assert state["provider_daily_limit"] == 800
    assert state["health_status"] == "healthy"
    assert state.get("last_health_sync")


def test_status_healthy_when_synced_with_zero_usage():
    store = _store_with_state(
        {"date": "2026-09-12", "used": 0, "events": []},
        {
            "date": "2026-09-12",
            "provider_daily_usage": 0,
            "provider_daily_limit": 800,
            "last_health_sync": "2026-09-12T10:00:00+00:00",
            "health_status": "healthy",
        },
    )
    payload = status_payload(store)
    assert payload["status"] == "healthy"
    assert payload["used_today"] == 0
    assert payload["provider_plan_limit"] == 800
    assert payload["internal_guard_limit"] == INTERNAL_GUARD_LIMIT


def test_status_unknown_without_sync_or_ledger():
    store = _store_with_state({"date": "2026-09-12", "used": 0, "events": []})
    payload = status_payload(store)
    assert payload["status"] == "unknown"


def test_status_blocked_on_429():
    store = _store_with_state({"date": "2026-09-12", "used": 0, "events": []})
    mark_blocked(store, "HTTP 429: quota exceeded")
    payload = status_payload(store)
    assert payload["status"] == "blocked"


def test_internal_guard_separate_from_provider_health():
    store = _store_with_state(
        {"date": "2026-09-12", "used": 725, "events": []},
        {
            "date": "2026-09-12",
            "provider_daily_usage": 2,
            "provider_daily_limit": 800,
            "last_health_sync": "2026-09-12T10:00:00+00:00",
            "health_status": "healthy",
        },
    )
    payload = status_payload(store)
    assert payload["status"] == "healthy"
    assert payload["internal_guard_active"] is True
    assert payload["used_today"] == 2
    assert payload["provider_plan_limit"] == 800


@patch("quantara_engine.market_data.credits._status_payload_from_sessions")
@patch("quantara_engine.market_data.credits._persist_health_success")
@patch("quantara_engine.market_data.adapters.twelvedata.TwelveDataMarketDataProvider")
@patch("quantara_engine.market_data.credits.health_session_scope")
def test_refresh_health_sync_persists_provider_usage(
    mock_scope, mock_provider_cls, mock_persist, mock_status
):
    session = MagicMock()
    mock_scope.return_value.__enter__.return_value = session
    mock_provider_cls.return_value.fetch_api_usage.return_value = {
        "daily_usage": 2,
        "plan_daily_limit": 800,
    }
    mock_status.return_value = {
        "status": "healthy",
        "used_today": 2,
        "provider_plan_limit": 800,
    }
    payload = refresh_twelve_data_health(force=True)
    mock_persist.assert_called_once()
    assert payload["status"] == "healthy"
    assert payload["used_today"] == 2


@patch("quantara_engine.market_data.credits._status_payload_from_sessions")
@patch("quantara_engine.market_data.credits._persist_health_error")
@patch("quantara_engine.market_data.adapters.twelvedata.TwelveDataMarketDataProvider")
@patch("quantara_engine.market_data.credits.health_session_scope")
def test_refresh_health_records_error_on_provider_failure(
    mock_scope, mock_provider_cls, mock_persist_err, mock_status
):
    session = MagicMock()
    mock_scope.return_value.__enter__.return_value = session
    mock_provider_cls.return_value.fetch_api_usage.side_effect = TwelveDataError(
        "HTTP 503: unavailable", code=503
    )
    mock_status.return_value = {"status": "error", "last_error": "HTTP 503: unavailable"}
    payload = refresh_twelve_data_health(force=True)
    mock_persist_err.assert_called_once()
    assert payload["status"] == "error"


@patch("quantara_engine.market_data.credits._status_payload_from_sessions")
@patch("quantara_engine.market_data.credits._persist_health_success")
@patch("quantara_engine.market_data.adapters.twelvedata.TwelveDataMarketDataProvider")
@patch("quantara_engine.market_data.credits.health_session_scope")
def test_db_lock_contention_exits_within_bounded_time(
    mock_scope, mock_provider_cls, mock_persist, mock_status
):
    session = MagicMock()
    mock_scope.return_value.__enter__.return_value = session
    mock_provider_cls.return_value.fetch_api_usage.return_value = {
        "daily_usage": 0,
        "plan_daily_limit": 800,
    }
    mock_persist.side_effect = OperationalError("canceling statement due to lock timeout", {}, None)
    mock_status.return_value = {"status": "unknown"}
    start = time.monotonic()
    refresh_twelve_data_health(force=True)
    elapsed = time.monotonic() - start
    assert elapsed < HEALTH_SYNC_WALL_TIMEOUT_SECONDS


@patch("quantara_engine.market_data.credits._status_payload_from_sessions")
@patch("quantara_engine.market_data.adapters.twelvedata.TwelveDataMarketDataProvider")
@patch("quantara_engine.market_data.credits.health_session_scope")
def test_http_timeout_does_not_hang_worker_path(mock_scope, mock_provider_cls, mock_status):
    session = MagicMock()
    mock_scope.return_value.__enter__.return_value = session
    mock_provider_cls.return_value.fetch_api_usage.side_effect = TwelveDataError("Network error: timed out")
    mock_status.return_value = {"status": "error", "last_error": "Network error: timed out"}
    start = time.monotonic()
    refresh_twelve_data_health(force=True)
    assert time.monotonic() - start < 5


def test_startup_does_not_block_on_health_sync():
    ws = WorkerScheduler()
    with patch.object(ws.scheduler, "start"):
        with patch("quantara_engine.market_data.credits.maybe_refresh_twelve_data_health") as mock_health:
            ws.start()
            mock_health.assert_not_called()
    startup_job = ws.scheduler.get_job("twelve_data_health_sync_startup")
    hourly_job = ws.scheduler.get_job("twelve_data_health_sync")
    assert startup_job is not None
    assert hourly_job is not None
    assert startup_job.kwargs == {"force": True}
    assert hourly_job.kwargs == {"force": False}
