"""Closed-session market data — no invalid provider ranges, defer instead of stale."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from quantara_engine.market_data.adapters.twelvedata import TwelveDataMarketDataProvider
from quantara_engine.market_data.registry import get_asset
from quantara_engine.market_data.sessions import is_data_stale_while_session_open, is_forex_session
from quantara_workers.jobs.fetch_data import _should_poll_asset


def test_forex_session_closed_on_saturday():
    saturday = datetime(2026, 9, 12, 12, 0, tzinfo=timezone.utc)
    assert is_forex_session(saturday) is False


def test_xau_deferred_when_session_closed_with_history():
    store = MagicMock()
    store.count_candles.return_value = 500
    asset = get_asset("XAUUSD")
    assert asset is not None
    saturday = datetime(2026, 9, 12, 12, 0, tzinfo=timezone.utc)
    should_poll, reason = _should_poll_asset(
        store,
        asset,
        now=saturday,
        stored=500,
        force_bootstrap=False,
        live=True,
        last_ts=datetime(2026, 9, 11, 20, 0, tzinfo=timezone.utc),
    )
    assert should_poll is False
    assert reason is not None
    assert "market closed" in reason


def test_crypto_unaffected_on_weekend():
    store = MagicMock()
    asset = get_asset("BTCUSD")
    assert asset is not None
    saturday = datetime(2026, 9, 12, 12, 0, tzinfo=timezone.utc)
    should_poll, _ = _should_poll_asset(
        store,
        asset,
        now=saturday,
        stored=500,
        force_bootstrap=False,
        live=True,
        last_ts=datetime(2026, 9, 12, 11, 55, tzinfo=timezone.utc),
    )
    assert should_poll is True


def test_stale_not_flagged_when_session_closed():
    asset = get_asset("GBPJPY")
    assert asset is not None
    saturday = datetime(2026, 9, 12, 12, 0, tzinfo=timezone.utc)
    friday_bar = datetime(2026, 9, 11, 20, 0, tzinfo=timezone.utc)
    assert is_data_stale_while_session_open(asset.trading_sessions, friday_bar, saturday) is False


def test_twelve_data_skips_invalid_range_without_http():
    provider = TwelveDataMarketDataProvider(api_key="test-key", asset=get_asset("XAUUSD"))
    end = datetime(2026, 9, 12, 12, 0, tzinfo=timezone.utc)
    start = end
    with patch.object(provider, "_request") as mock_request:
        rows = provider._time_series("5m", start_date=start, end_date=end)
    assert rows == []
    mock_request.assert_not_called()


def test_twelve_data_fetch_latest_returns_empty_when_since_not_before_now():
    provider = TwelveDataMarketDataProvider(api_key="test-key", asset=get_asset("XAUUSD"))
    future = datetime(2099, 1, 1, 12, 0, tzinfo=timezone.utc)
    with patch.object(provider, "_time_series") as mock_ts:
        candles = provider.fetch_latest("inst-id", "5m", since=future)
    assert candles == []
    mock_ts.assert_not_called()
