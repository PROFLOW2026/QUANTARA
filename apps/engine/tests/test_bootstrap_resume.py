"""Regression tests for partial bootstrap resume (5m sufficient, derived missing)."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from quantara_engine.market_data.registry import get_asset
from quantara_workers.jobs.fetch_data import (
    STRATEGY_MIN_CANDLES,
    _fetch_asset_bulk,
    bootstrap_coverage,
    bootstrap_is_complete,
    bootstrap_missing_timeframes,
)


def _instrument():
    inst = MagicMock()
    inst.id = "573f3aa2-1427-46f6-805b-81570f52fd99"
    return inst


def _counts(**overrides: int) -> dict[str, int]:
    base = {"5m": 0, "15m": 0, "1h": 0}
    base.update(overrides)
    return base


def test_bootstrap_is_complete_all_timeframes():
    assert bootstrap_is_complete(_counts(**{"5m": 200, "15m": 200, "1h": 200}))


def test_bootstrap_missing_timeframes_partial():
    assert bootstrap_missing_timeframes(_counts(**{"5m": 2400, "15m": 0, "1h": 0})) == (
        "15m",
        "1h",
    )


@patch("quantara_workers.jobs.fetch_data.fetch_with_failover")
@patch("quantara_workers.jobs.fetch_data._resume_derived_from_stored_5m", return_value=(0, 900))
def test_fetch_asset_bulk_noop_when_all_complete(mock_resume, mock_fetch):
    store = MagicMock()
    store.count_candles.side_effect = lambda iid, tf: 250
    asset = get_asset("XAUUSD")
    assert asset is not None

    result = _fetch_asset_bulk(store, _instrument(), asset, datetime.now(timezone.utc))

    assert result == (0, 0, None)
    mock_fetch.assert_not_called()
    mock_resume.assert_not_called()


@patch("quantara_workers.jobs.fetch_data.fetch_with_failover")
@patch("quantara_workers.jobs.fetch_data._resume_derived_from_stored_5m", return_value=(0, 850))
def test_fetch_asset_bulk_resume_case_a(mock_resume, mock_fetch):
    """5m=2400, 15m=0, 1h=0 — derive only, no provider fetch."""
    store = MagicMock()
    store.count_candles.side_effect = lambda iid, tf: {"5m": 2400, "15m": 0, "1h": 0}[tf]
    asset = get_asset("XAUUSD")
    assert asset is not None

    count, derived, error = _fetch_asset_bulk(
        store, _instrument(), asset, datetime.now(timezone.utc)
    )

    assert error is None
    assert count == 0
    assert derived == 850
    mock_fetch.assert_not_called()
    mock_resume.assert_called_once()


@patch("quantara_workers.jobs.fetch_data.fetch_with_failover")
@patch("quantara_workers.jobs.fetch_data._resume_derived_from_stored_5m", return_value=(0, 120))
def test_fetch_asset_bulk_resume_case_b(mock_resume, mock_fetch):
    """5m=2400, 15m=500, 1h=150 — resume derived path only."""
    store = MagicMock()
    store.count_candles.side_effect = lambda iid, tf: {"5m": 2400, "15m": 500, "1h": 150}[tf]
    asset = get_asset("NVDA")
    assert asset is not None

    count, derived, error = _fetch_asset_bulk(
        store, _instrument(), asset, datetime.now(timezone.utc)
    )

    assert error is None
    assert count == 0
    assert derived == 120
    mock_fetch.assert_not_called()
    mock_resume.assert_called_once()


@patch("quantara_workers.jobs.fetch_data.TradingStore")
@patch("quantara_workers.jobs.fetch_data.session_scope")
@patch("quantara_workers.jobs.fetch_data._persist_candles_chunked", return_value=0)
@patch("quantara_workers.jobs.fetch_data.fetch_with_failover")
@patch("quantara_workers.jobs.fetch_data._resume_derived_from_stored_5m")
def test_fetch_asset_bulk_case_d_fetches_when_5m_insufficient(
    mock_resume, mock_fetch, mock_persist, mock_session_scope, mock_trading_store_cls
):
    store = MagicMock()
    store.count_candles.side_effect = lambda iid, tf: {"5m": 50, "15m": 0, "1h": 0}[tf]
    asset = get_asset("XAUUSD")
    assert asset is not None

    mock_fetch.return_value = MagicMock(candles=[], provider=None, error="provider down")
    mock_session_scope.return_value.__enter__.return_value = MagicMock()
    mock_trading_store_cls.return_value.count_candles.return_value = 50

    count, derived, error = _fetch_asset_bulk(
        store, _instrument(), asset, datetime.now(timezone.utc)
    )

    mock_fetch.assert_called_once()
    mock_resume.assert_not_called()
    assert "provider down" in (error or "")


def test_bootstrap_coverage_helper():
    store = MagicMock()
    store.count_candles.side_effect = lambda iid, tf: {"5m": 10, "15m": 20, "1h": 30}[tf]
    assert bootstrap_coverage(store, "id") == {"5m": 10, "15m": 20, "1h": 30}
    assert bootstrap_is_complete(bootstrap_coverage(store, "id")) is False
