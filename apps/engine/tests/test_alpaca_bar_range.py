"""Focused tests for Alpaca bars range construction (UTC + completed-minute semantics)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

from quantara_engine.market_data.adapters.alpaca import AlpacaMarketDataProvider
from quantara_engine.market_data.alpaca_bar_range import (
    exclusive_end_utc,
    format_alpaca_ts,
    latest_completed_bar_open,
    resolve_bars_request_range,
    utc_for_alpaca,
)


IL = ZoneInfo("Asia/Jerusalem")


def _provider() -> AlpacaMarketDataProvider:
    provider = AlpacaMarketDataProvider.__new__(AlpacaMarketDataProvider)
    provider.api_key = "k"
    provider.api_secret = "s"
    provider.base_url = "https://data.alpaca.markets"
    provider.feed = "iex"
    provider._store = None
    provider._caller = "test"
    provider._priority = None
    provider._asset = None
    return provider


def test_il_since_formats_as_utc_not_wall_clock():
    """Root-cause regression: +03:00 since must not become mislabeled Z."""
    since_il = datetime(2026, 9, 24, 22, 32, tzinfo=IL)
    assert format_alpaca_ts(since_il) == "2026-09-24T19:32:00Z"
    assert format_alpaca_ts(since_il) != "2026-09-24T22:32:00Z"


def test_start_lt_end_executes_http():
    provider = _provider()
    captured: dict[str, str] = {}

    def _request(url: str, _sym: str) -> dict:
        captured["url"] = url
        return {"bars": []}

    provider._request = _request  # type: ignore[method-assign]
    since_il = datetime(2026, 9, 24, 22, 17, tzinfo=IL)
    now = datetime(2026, 9, 24, 19, 59, 37, tzinfo=timezone.utc)
    provider._fetch_bars(
        symbol="NVDA",
        timeframe="1m",
        start=since_il - timedelta(minutes=15),
        limit=10,
        end=now,
    )
    assert "start=" in captured["url"]
    assert "end=" in captured["url"]
    assert "2026-09-24T19%3A02%3A00Z" in captured["url"] or "2026-09-24T19:02:00Z" in captured["url"]
    assert "2026-09-24T19%3A59%3A00Z" in captured["url"] or "2026-09-24T19:59:00Z" in captured["url"]


def test_start_ge_end_no_http_no_provider_error():
    provider = _provider()
    provider._provider_ticker = lambda: "NVDA"  # type: ignore[method-assign]
    called = {"n": 0}

    def _boom(*_a, **_k):
        called["n"] += 1
        raise AssertionError("HTTP must not be called")

    provider._request = _boom  # type: ignore[method-assign]
    # latest persisted = last completed bar; next bar not closed yet
    since = datetime(2026, 9, 24, 19, 54, tzinfo=timezone.utc)
    now = datetime(2026, 9, 24, 19, 55, 20, tzinfo=timezone.utc)
    with patch("quantara_engine.market_data.adapters.alpaca.datetime") as mock_dt:
        mock_dt.now.return_value = now
        mock_dt.side_effect = datetime
        candles = provider.fetch_latest("inst", "1m", since=since)
    assert candles == []
    assert called["n"] == 0


def test_resolve_none_when_start_equals_exclusive_end():
    now = datetime(2026, 9, 24, 19, 55, 30, tzinfo=timezone.utc)
    end_excl = exclusive_end_utc(now, "1m")
    assert resolve_bars_request_range(end_excl, now, timeframe="1m") is None


@pytest.mark.parametrize(
    ("second", "expected_completed_open", "expected_exclusive_end"),
    [
        (0, 54, 55),
        (1, 54, 55),
        (30, 54, 55),
        (59, 54, 55),
    ],
)
def test_minute_boundary_latest_completed(
    second: int, expected_completed_open: int, expected_exclusive_end: int
):
    now = datetime(2026, 9, 24, 19, 55, second, tzinfo=timezone.utc)
    assert latest_completed_bar_open(now, "1m") == datetime(
        2026, 9, 24, 19, expected_completed_open, tzinfo=timezone.utc
    )
    assert exclusive_end_utc(now, "1m") == datetime(
        2026, 9, 24, 19, expected_exclusive_end, tzinfo=timezone.utc
    )


def test_minute_rollover_at_xx_00():
    now = datetime(2026, 9, 24, 20, 0, 1, tzinfo=timezone.utc)
    assert latest_completed_bar_open(now, "1m") == datetime(
        2026, 9, 24, 19, 59, tzinfo=timezone.utc
    )
    assert exclusive_end_utc(now, "1m") == datetime(2026, 9, 24, 20, 0, tzinfo=timezone.utc)


def test_utc_normalization_naive_assumes_utc():
    naive = datetime(2026, 9, 24, 19, 32)
    assert utc_for_alpaca(naive).tzinfo == timezone.utc
    assert format_alpaca_ts(naive) == "2026-09-24T19:32:00Z"


def test_valid_catch_up_range_parses_bars():
    provider = _provider()
    since_il = datetime(2026, 9, 24, 22, 32, tzinfo=IL)
    now = datetime(2026, 9, 24, 19, 40, tzinfo=timezone.utc)

    def _request(_url: str, _sym: str) -> dict:
        return {
            "bars": [
                {"t": "2026-09-24T19:33:00Z", "o": 1, "h": 2, "l": 0.5, "c": 1.5, "v": 10},
                {"t": "2026-09-24T19:34:00Z", "o": 1.5, "h": 2.5, "l": 1, "c": 2, "v": 11},
            ]
        }

    provider._request = _request  # type: ignore[method-assign]
    rows, _ = provider._fetch_bars(
        symbol="NVDA",
        timeframe="1m",
        start=since_il - timedelta(minutes=15),
        limit=100,
        end=now,
    )
    candles = provider._to_candles(rows, "inst-nvda", "1m", since=since_il)
    assert len(candles) == 2
    assert candles[0].timestamp == datetime(2026, 9, 24, 19, 33, tzinfo=timezone.utc)
