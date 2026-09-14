"""Tests for local 1m→5m aggregation and market-data budget isolation."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from quantara_engine.domain.types import Candle
from quantara_engine.market_data.aggregation import (
    aggregate_from_1m,
    aggregate_from_5m,
    incremental_derive_from_1m,
)
from quantara_engine.market_data.adapters.alpaca import AlpacaMarketDataProvider
from quantara_engine.market_data.provider_budgets import (
    can_request,
    can_request_tiingo_candle,
    can_request_tiingo_fx,
)
from quantara_engine.market_data.provider_resolver import _classify_failure
from quantara_engine.market_data.adapters.alpaca import AlpacaError


def _1m(ts: str, o: str, h: str, l: str, c: str, *, source: str = "tiingo") -> Candle:
    return Candle(
        instrument_id="inst-1",
        timeframe="1m",
        timestamp=datetime.fromisoformat(ts).replace(tzinfo=timezone.utc),
        open=Decimal(o),
        high=Decimal(h),
        low=Decimal(l),
        close=Decimal(c),
        volume=Decimal("1"),
        source=source,
        is_complete=True,
    )


def test_a_five_completed_1m_bars_make_one_5m():
    bars = [
        _1m("2026-09-14T10:00:00+00:00", "100", "101", "99", "100.5"),
        _1m("2026-09-14T10:01:00+00:00", "100.5", "102", "100", "101"),
        _1m("2026-09-14T10:02:00+00:00", "101", "103", "100.5", "102"),
        _1m("2026-09-14T10:03:00+00:00", "102", "104", "101", "103"),
        _1m("2026-09-14T10:04:00+00:00", "103", "105", "102", "104"),
    ]
    derived = aggregate_from_1m(bars)
    assert len(derived) == 1
    bar = derived[0]
    assert bar.timeframe == "5m"
    assert bar.timestamp == datetime(2026, 9, 14, 10, 0, tzinfo=timezone.utc)
    assert bar.open == Decimal("100")
    assert bar.high == Decimal("105")
    assert bar.low == Decimal("99")
    assert bar.close == Decimal("104")
    assert bar.volume == Decimal("5")
    assert bar.source == "tiingo"
    assert bar.is_complete is True


def test_b_incomplete_flag_excluded():
    bars = [
        _1m("2026-09-14T10:00:00+00:00", "1", "2", "0.5", "1.5"),
        _1m("2026-09-14T10:01:00+00:00", "1.5", "2.5", "1", "2"),
        _1m("2026-09-14T10:02:00+00:00", "2", "3", "1.5", "2.5"),
        _1m("2026-09-14T10:03:00+00:00", "2.5", "3.5", "2", "3"),
    ]
    partial = _1m("2026-09-14T10:04:00+00:00", "3", "4", "2.5", "3.5")
    partial = Candle(
        instrument_id=partial.instrument_id,
        timeframe=partial.timeframe,
        timestamp=partial.timestamp,
        open=partial.open,
        high=partial.high,
        low=partial.low,
        close=partial.close,
        volume=partial.volume,
        source=partial.source,
        is_complete=False,
    )
    bars.append(partial)
    assert aggregate_from_1m(bars) == []


def test_b_gap_in_1m_sequence_skips_bucket():
    bars = [
        _1m("2026-09-14T10:00:00+00:00", "1", "2", "0.5", "1.5"),
        _1m("2026-09-14T10:01:00+00:00", "1.5", "2.5", "1", "2"),
        _1m("2026-09-14T10:03:00+00:00", "2", "3", "1.5", "2.5"),
        _1m("2026-09-14T10:04:00+00:00", "2.5", "3.5", "2", "3"),
        _1m("2026-09-14T10:05:00+00:00", "3", "4", "2.5", "3.5"),
    ]
    assert aggregate_from_1m(bars) == []


def test_c_d_incremental_derive_only_touched_bucket():
    bars = []
    start = datetime(2026, 9, 14, 10, 0, tzinfo=timezone.utc)
    for i in range(10):
        ts = start + timedelta(minutes=i)
        bars.append(
            _1m(
                ts.isoformat(),
                str(100 + i),
                str(101 + i),
                str(99 + i),
                str(100.5 + i),
                source="tiingo",
            )
        )
    # Touch only second 5m bucket (10:05–10:09)
    touched = [start + timedelta(minutes=7)]
    derived = incremental_derive_from_1m(bars, touched, session_mode="utc")
    assert len(derived) == 1
    assert derived[0].timestamp == datetime(2026, 9, 14, 10, 5, tzinfo=timezone.utc)


def test_e_15m_1h_still_derive_from_aggregated_5m():
    five = []
    start = datetime(2026, 9, 14, 10, 0, tzinfo=timezone.utc)
    for i in range(12):
        ts = start + timedelta(minutes=5 * i)
        five.append(
            Candle(
                instrument_id="inst-1",
                timeframe="5m",
                timestamp=ts,
                open=Decimal(str(100 + i)),
                high=Decimal(str(101 + i)),
                low=Decimal(str(99 + i)),
                close=Decimal(str(100.5 + i)),
                source="tiingo",
                is_complete=True,
            )
        )
    h15 = aggregate_from_5m(five, "15m")
    h1 = aggregate_from_5m(five, "1h")
    assert len(h15) == 4
    assert len(h1) == 1
    assert h1[0].timestamp == start


def test_f_tiingo_24_fx_calls_leave_candle_budget():
    """FX 1m uses fx_rate purpose — does not exhaust candle budget at 24/hour."""
    store = MagicMock()
    store.get_settings_dict.return_value = {
        "provider_budget:tiingo": {
            "provider": "tiingo",
            "date": datetime.now(timezone.utc).date().isoformat(),
            "hour": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H"),
            "used_hour": 24,
            "used_day": 24,
            "status": "healthy",
        }
    }
    assert can_request_tiingo_candle(store)  # 42 - 24 = 18 left
    assert can_request(store, "tiingo", purpose="fx_rate")
    assert can_request_tiingo_fx(store)


def test_g_tiingo_conservation_does_not_block_alpaca_purpose():
    store = MagicMock()
    store.get_settings_dict.return_value = {
        "provider_budget:tiingo": {
            "provider": "tiingo",
            "date": datetime.now(timezone.utc).date().isoformat(),
            "hour": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H"),
            "used_hour": 42,
            "used_day": 42,
            "status": "conservation",
        },
        "provider_budget:alpaca": {
            "provider": "alpaca",
            "date": datetime.now(timezone.utc).date().isoformat(),
            "hour": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H"),
            "used_hour": 0,
            "used_day": 0,
            "used_minute": 0,
            "status": "healthy",
        },
    }
    assert not can_request_tiingo_candle(store)
    assert can_request(store, "alpaca")


def test_i_invalid_alpaca_range_skips_http():
    provider = AlpacaMarketDataProvider.__new__(AlpacaMarketDataProvider)
    provider.api_key = "k"
    provider.api_secret = "s"
    provider.base_url = "https://data.alpaca.markets"
    provider.feed = "iex"
    provider._store = None
    provider._caller = "test"
    provider._priority = None
    provider._asset = None

    called = {"n": 0}

    def _boom(*_a, **_k):
        called["n"] += 1
        raise AssertionError("HTTP must not be called")

    provider._request = _boom  # type: ignore[method-assign]
    future = datetime.now(timezone.utc) + timedelta(hours=1)
    rows, token = provider._fetch_bars(
        symbol="COIN",
        timeframe="5m",
        start=future,
        limit=10,
    )
    assert rows == []
    assert token is None
    assert called["n"] == 0


def test_i_classify_400_no_cooldown():
    should, _ = _classify_failure(AlpacaError("HTTP 400: end should not be before start"))
    assert should is False


def test_tiingo_1m_incremental_fetch_does_not_truncate_gap_fill_window():
    from quantara_engine.market_data.adapters.tiingo import TiingoMarketDataProvider
    from quantara_engine.market_data.registry import get_asset

    asset = get_asset("XAUUSD")
    assert asset is not None
    provider = TiingoMarketDataProvider(
        asset=asset,
        allow_non_canonical_timeframes=True,
    )
    since = datetime(2026, 9, 10, 12, 0, tzinfo=timezone.utc)
    rows = []
    for i in range(60):
        ts = since + timedelta(minutes=i + 1)
        rows.append(
            {
                "date": ts.isoformat().replace("+00:00", "Z"),
                "open": 100 + i,
                "high": 101 + i,
                "low": 99 + i,
                "close": 100.5 + i,
            }
        )

    provider._fetch_rows = MagicMock(return_value=rows)  # type: ignore[method-assign]
    candles = provider.fetch_latest("inst-xau", "1m", since=since)
    assert len(candles) == 60
    assert candles[0].timestamp == since + timedelta(minutes=1)
    assert candles[-1].timestamp == since + timedelta(minutes=60)
    assert len(candles) > 30
    provider._fetch_rows.assert_called_once()
    assert provider._fetch_rows.call_args.kwargs["limit"] == 5000


def test_derive_catchup_scans_since_last_canonical_5m():
    from quantara_engine.market_data.derive_from_1m import derive_higher_from_1m

    store = MagicMock()
    last_5m = datetime(2026, 9, 14, 20, 55, tzinfo=timezone.utc)
    store.latest_candle_timestamp.return_value = last_5m
    bars = []
    start = datetime(2026, 9, 14, 21, 0, tzinfo=timezone.utc)
    for i in range(10):
        ts = start + timedelta(minutes=i)
        bars.append(
            _1m(
                ts.isoformat(),
                str(100 + i),
                str(101 + i),
                str(99 + i),
                str(100.5 + i),
            )
        )
    store.list_candles.return_value = bars
    store.list_recent_candles.return_value = bars[-3:]

    count_5m, higher = derive_higher_from_1m(store, "inst-1", session_mode="utc")
    assert count_5m == 2
    assert higher >= 0
    store.list_candles.assert_called_once()
    since_arg = store.list_candles.call_args.kwargs["since"]
    assert since_arg == last_5m - timedelta(minutes=5)


def test_fx_excluded_from_tiingo_5m_fallback():
    from quantara_engine.market_data.registry import get_asset
    from quantara_engine.market_data.tiingo_fallback_scheduler import _needs_tiingo_fallback

    store = MagicMock()
    xau = get_asset("XAUUSD")
    gbp = get_asset("GBPJPY")
    coin = get_asset("COIN")
    assert xau and gbp and coin
    assert _needs_tiingo_fallback(store, xau) is False
    assert _needs_tiingo_fallback(store, gbp) is False
    assert _needs_tiingo_fallback(store, coin) is False
