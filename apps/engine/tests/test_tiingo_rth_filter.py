"""Tiingo US equity fallback must preserve RTH-only bars."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import patch

from quantara_engine.domain.types import Candle
from quantara_engine.market_data.adapters.tiingo import TiingoMarketDataProvider
from quantara_engine.market_data.registry import get_asset


def test_tiingo_equity_filters_non_rth_candles():
    asset = get_asset("NVDA")
    assert asset is not None
    provider = TiingoMarketDataProvider(asset=asset)
    rth_ts = datetime(2026, 9, 9, 14, 30, tzinfo=timezone.utc)
    pre_ts = datetime(2026, 9, 9, 12, 0, tzinfo=timezone.utc)
    rows = [
        {"date": pre_ts.isoformat(), "open": 100, "high": 101, "low": 99, "close": 100, "volume": 1},
        {"date": rth_ts.isoformat(), "open": 100, "high": 101, "low": 99, "close": 100, "volume": 1},
    ]
    candles = provider._to_candles(rows, "inst-1", "5m")
    assert len(candles) == 1
    assert candles[0].timestamp == rth_ts


def test_tiingo_crypto_flattens_price_data():
    asset = get_asset("BTCUSD")
    assert asset is not None
    provider = TiingoMarketDataProvider(asset=asset)
    nested = [
        {
            "ticker": "btcusd",
            "priceData": [
                {"date": "2026-09-01T00:00:00+00:00", "open": 1, "high": 2, "low": 0.5, "close": 1.5},
                {"date": "2026-09-01T00:05:00+00:00", "open": 1.5, "high": 2, "low": 1, "close": 1.8},
            ],
        }
    ]
    with patch.object(provider, "_request", return_value=nested):
        rows = provider._fetch_rows("5m", datetime(2026, 9, 1, tzinfo=timezone.utc), limit=10)
    assert len(rows) == 2
    assert "date" in rows[0]


def test_tiingo_crypto_does_not_filter_rth():
    asset = get_asset("BTCUSD")
    assert asset is not None
    provider = TiingoMarketDataProvider(asset=asset)
    ts = datetime(2026, 9, 9, 3, 0, tzinfo=timezone.utc)
    rows = [{"date": ts.isoformat(), "open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": 1}]
    with patch.object(provider, "_parse_row", wraps=provider._parse_row) as parse:
        candles = provider._to_candles(rows, "inst-1", "5m")
    assert len(candles) == 1
