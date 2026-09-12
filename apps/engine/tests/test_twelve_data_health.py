"""Twelve Data provider health vs usage — separate concepts."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from quantara_engine.market_data.adapters.twelvedata import TwelveDataError
from quantara_engine.market_data.credits import (
    DAILY_HARD_LIMIT,
    INTERNAL_GUARD_LIMIT,
    mark_blocked,
    refresh_twelve_data_health,
    status_payload,
    sync_provider_usage,
)


def _store_with_state(state: dict) -> MagicMock:
    store = MagicMock()
    store.get_settings_dict.return_value = {"provider_credits:twelvedata": state}
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
    assert saved["health_status"] == "healthy"
    assert saved.get("last_health_sync")


def test_status_healthy_when_synced_with_zero_usage():
    store = _store_with_state(
        {
            "date": "2026-09-12",
            "used": 0,
            "events": [],
            "provider_daily_usage": 0,
            "provider_daily_limit": 800,
            "last_health_sync": "2026-09-12T10:00:00+00:00",
            "health_status": "healthy",
        }
    )
    payload = status_payload(store)
    assert payload["status"] == "healthy"
    assert payload["used_today"] == 0
    assert payload["provider_plan_limit"] == 800
    assert payload["daily_limit"] == 800
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
        {
            "date": "2026-09-12",
            "used": 725,
            "events": [],
            "provider_daily_usage": 2,
            "provider_daily_limit": 800,
            "last_health_sync": "2026-09-12T10:00:00+00:00",
            "health_status": "healthy",
        }
    )
    payload = status_payload(store)
    assert payload["status"] == "healthy"
    assert payload["internal_guard_active"] is True
    assert payload["used_today"] == 2
    assert payload["provider_plan_limit"] == 800


@patch("quantara_engine.market_data.adapters.twelvedata.TwelveDataMarketDataProvider")
@patch("quantara_engine.persistence.store.TradingStore")
@patch("quantara_engine.db.session.session_scope")
def test_refresh_health_sync_persists_provider_usage(
    mock_session_scope, mock_store_cls, mock_provider_cls
):
    session = MagicMock()
    mock_session_scope.return_value.__enter__.return_value = session
    store = MagicMock()
    mock_store_cls.return_value = store
    saved_state: dict = {"date": "2026-09-12", "used": 0, "events": []}

    def _update_settings(_key, value, *args, **kwargs):
        saved_state.clear()
        saved_state.update(value)

    store.get_settings_dict.side_effect = lambda: {"provider_credits:twelvedata": dict(saved_state)}
    store.update_settings.side_effect = _update_settings

    mock_provider_cls.return_value.fetch_api_usage.return_value = {
        "daily_usage": 2,
        "plan_daily_limit": 800,
    }
    payload = refresh_twelve_data_health(force=True)

    assert payload["status"] == "healthy"
    assert payload["used_today"] == 2
    assert payload["provider_plan_limit"] == 800


@patch("quantara_engine.market_data.adapters.twelvedata.TwelveDataMarketDataProvider")
@patch("quantara_engine.persistence.store.TradingStore")
@patch("quantara_engine.db.session.session_scope")
def test_refresh_health_records_error_on_provider_failure(
    mock_session_scope, mock_store_cls, mock_provider_cls
):
    session = MagicMock()
    mock_session_scope.return_value.__enter__.return_value = session
    store = MagicMock()
    mock_store_cls.return_value = store
    saved_state: dict = {"date": "2026-09-12", "used": 0, "events": []}

    def _update_settings(_key, value, *args, **kwargs):
        saved_state.clear()
        saved_state.update(value)

    store.get_settings_dict.side_effect = lambda: {"provider_credits:twelvedata": dict(saved_state)}
    store.update_settings.side_effect = _update_settings

    mock_provider_cls.return_value.fetch_api_usage.side_effect = TwelveDataError(
        "HTTP 503: unavailable", code=503
    )
    payload = refresh_twelve_data_health(force=True)

    assert payload["status"] == "error"
    assert payload.get("last_error")
