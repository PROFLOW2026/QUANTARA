"""Provider HEALTHY must follow candle age, and budget writes must not share ingest locks."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import MagicMock

from quantara_engine.core.config import settings
from quantara_engine.domain.types import Candle
from quantara_engine.market_data.adapters.alpaca import AlpacaMarketDataProvider
from quantara_engine.market_data.adapters.coinbase import CoinbaseMarketDataProvider
from quantara_engine.market_data.registry import get_asset
from quantara_engine.market_data.provider_budgets import (
    provider_candle_freshness,
    record_request,
)


def test_rth_stale_equities_are_not_a_healthy_provider():
    now = datetime(2026, 11, 16, 19, 10, tzinfo=timezone.utc)  # Monday 14:10 ET
    old = datetime(2026, 11, 13, 20, 50, tzinfo=timezone.utc)
    latest = {("NVDA", "1m"): old, ("NVDA", "5m"): old, ("AAPL", "5m"): old}
    verdict = provider_candle_freshness(latest, now)
    assert verdict["alpaca"]["session_open"] is True
    assert verdict["alpaca"]["fresh"] is False
    assert "NVDA" in verdict["alpaca"]["stale_symbols"]
    assert "AAPL" in verdict["alpaca"]["stale_symbols"]
    assert verdict["finnhub"]["fresh"] is False


def test_closed_equity_session_does_not_mark_provider_stale():
    now = datetime(2026, 11, 14, 16, 0, tzinfo=timezone.utc)  # Saturday
    old = datetime(2026, 11, 13, 20, 50, tzinfo=timezone.utc)
    verdict = provider_candle_freshness({("NVDA", "5m"): old}, now)
    assert verdict["alpaca"]["session_open"] is False
    assert verdict["alpaca"]["fresh"] is True


def test_crypto_provider_requires_every_open_symbol():
    now = datetime(2026, 11, 16, 19, 10, tzinfo=timezone.utc)
    fresh = now - timedelta(minutes=2)
    latest = {
        ("BTCUSD", "1m"): fresh,
        ("ETHUSD", "1m"): fresh,
        ("SOLUSD", "5m"): datetime(2026, 11, 14, 15, 25, tzinfo=timezone.utc),
        ("XRPUSD", "5m"): fresh,
    }
    verdict = provider_candle_freshness(latest, now)
    assert verdict["coinbase"]["fresh"] is False
    assert verdict["coinbase"]["stale_symbols"] == ["SOLUSD"]


def test_fx_fallback_provider_is_stale_when_primary_candles_are():
    now = datetime(2026, 11, 16, 19, 10, tzinfo=timezone.utc)
    fresh = now - timedelta(minutes=3)
    old = now - timedelta(hours=5)
    latest = {
        ("XAUUSD", "1m"): fresh,
        ("GBPJPY", "1m"): fresh,
        ("EURUSD", "1m"): old,
        ("USDJPY", "1m"): old,
    }
    verdict = provider_candle_freshness(latest, now)
    assert verdict["tiingo"]["fresh"] is False
    assert "EURUSD" in verdict["tiingo"]["stale_symbols"]
    assert "USDJPY" in verdict["twelvedata"]["stale_symbols"]


def test_fake_store_budget_write_stays_on_caller():
    store = MagicMock()
    store.session = None
    store.get_settings_dict.return_value = {}
    record_request(store, "alpaca", symbol="NVDA", caller="test", success=True)
    store.update_settings.assert_called_once()
    saved = store.update_settings.call_args.args[1]
    assert saved["status"] == "healthy"
    assert saved["last_error"] is None


def test_orm_budget_write_uses_a_separate_transaction(monkeypatch):
    caller = MagicMock()
    monkeypatch.setattr(
        "quantara_engine.market_data.provider_budgets._orm_session",
        lambda store: object(),
    )
    entered: dict[str, bool] = {}

    class _Ctx:
        def __enter__(self):
            entered["yes"] = True
            session = MagicMock()
            return session

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr("quantara_engine.db.session.session_scope", lambda: _Ctx())
    fresh = MagicMock()
    fresh.get_settings_dict.return_value = {}
    monkeypatch.setattr(
        "quantara_engine.market_data.provider_budgets.TradingStore",
        lambda _session: fresh,
    )
    record_request(caller, "alpaca", symbol="NVDA", caller="live", success=False, error="ssl")
    assert entered["yes"] is True
    caller.update_settings.assert_not_called()
    fresh.update_settings.assert_called_once()
    caller.invalidate_settings_cache.assert_called_once()


def test_coinbase_catchup_is_split_under_the_300_bar_cap(monkeypatch):
    provider = CoinbaseMarketDataProvider.__new__(CoinbaseMarketDataProvider)
    provider._asset = get_asset("SOLUSD")
    calls: list[tuple[datetime, datetime]] = []

    def _fetch(instrument_id, timeframe, *, start=None, end=None, limit=300):
        assert limit <= 280
        assert start is not None and end is not None
        calls.append((start, end))
        return [
            Candle(
                instrument_id=instrument_id,
                timeframe=timeframe,
                timestamp=end - timedelta(minutes=5),
                open=Decimal("1"),
                high=Decimal("1"),
                low=Decimal("1"),
                close=Decimal("1"),
                volume=Decimal("1"),
                source="coinbase",
                is_complete=True,
            )
        ]

    provider.fetch_candles = _fetch  # type: ignore[method-assign]
    end = datetime(2026, 11, 16, 19, 0, tzinfo=timezone.utc)
    since = end - timedelta(days=2)

    class _Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return end

    monkeypatch.setattr(
        "quantara_engine.market_data.adapters.coinbase.datetime",
        _Frozen,
    )
    candles = provider.fetch_latest("inst", "5m", since=since)
    assert len(calls) >= 2
    assert calls[0][1] == end
    assert candles


def test_gap_fill_stops_when_the_cursor_does_not_advance(monkeypatch):
    monkeypatch.setattr(settings, "alpaca_api_key_id", "test-key")
    monkeypatch.setattr(settings, "alpaca_api_secret_key", "test-secret")
    provider = AlpacaMarketDataProvider()
    provider._asset = get_asset("NVDA")
    calls = {"n": 0}

    def _stuck(**_kwargs):
        calls["n"] += 1
        if calls["n"] > 3:
            raise AssertionError("gap fill kept requesting after a stuck cursor")
        return (
            [{"t": "2026-11-13T20:50:00+00:00", "o": 1, "h": 1, "l": 1, "c": 1, "v": 1}],
            None,
        )

    provider._fetch_bars = _stuck  # type: ignore[method-assign]
    candles = provider.fetch_gap_fill(
        "inst",
        "5m",
        datetime(2026, 11, 13, 20, 50, tzinfo=timezone.utc),
        end=datetime(2026, 11, 16, 19, 0, tzinfo=timezone.utc),
    )
    assert calls["n"] == 1
    assert candles == []
