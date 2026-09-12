"""Deterministic provider failover and cooldown tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from quantara_engine.domain.types import Candle
from quantara_engine.market_data.adapters.alpaca import AlpacaError
from quantara_engine.market_data.adapters.tiingo import TiingoError
from quantara_engine.market_data.adapters.twelvedata import TwelveDataError
from quantara_engine.market_data.credits import mark_blocked
from quantara_engine.market_data.provider_cooldown import is_in_cooldown, mark_cooldown
from quantara_engine.market_data.provider_resolver import (
    dedupe_complete_candles,
    fetch_with_failover,
    has_eligible_provider,
    is_provider_eligible,
    provider_chain_for_asset,
)
from quantara_engine.market_data.registry import AssetClass, AssetDefinition, ProviderName, get_asset


def _gbpjpy_asset() -> AssetDefinition:
    asset = get_asset("GBPJPY")
    assert asset is not None
    return asset


def _xau_asset() -> AssetDefinition:
    asset = get_asset("XAUUSD")
    assert asset is not None
    return asset


class FakeStore:
    def __init__(self, settings: dict | None = None):
        self._settings = dict(settings or {})

    def get_settings_dict(self) -> dict:
        return self._settings

    def update_settings(self, key: str, value, description: str | None = None, *, flush: bool = True):
        if value is None:
            self._settings.pop(key, None)
        else:
            self._settings[key] = value


def _candle(ts: datetime, *, source: str = "twelvedata") -> Candle:
    return Candle(
        instrument_id="inst-1",
        timeframe="5m",
        timestamp=ts,
        open=Decimal("1"),
        high=Decimal("2"),
        low=Decimal("0.5"),
        close=Decimal("1.5"),
        volume=None,
        source=source,
        is_complete=True,
    )


@patch("quantara_engine.market_data.provider_resolver.settings")
@patch("quantara_engine.market_data.provider_resolver.get_market_data_provider")
def test_twelve_data_healthy_primary_used(provider_factory, settings_mock):
    settings_mock.market_data_api_key = "td-key"
    settings_mock.tiingo_api_key = "tiingo-key"
    settings_mock.alpaca_api_key_id = ""
    settings_mock.alpaca_api_secret_key = ""
    asset = _gbpjpy_asset()
    primary = MagicMock()
    primary.fetch_latest.return_value = [_candle(datetime(2026, 9, 11, 10, 0, tzinfo=timezone.utc))]
    provider_factory.return_value = primary

    store = FakeStore()
    outcome = fetch_with_failover(
        store,
        asset,
        caller="test",
        priority=3,
        fetch_fn=lambda p: p.fetch_latest("inst-1", "5m"),
    )
    assert outcome.provider == ProviderName.TWELVE_DATA
    assert len(outcome.candles) == 1
    provider_factory.assert_called_once_with("twelvedata", asset=asset)


@patch("quantara_engine.market_data.provider_resolver.settings")
@patch("quantara_engine.market_data.provider_resolver.get_market_data_provider")
def test_twelve_data_quota_exhausted_tiingo_fallback(provider_factory, settings_mock):
    settings_mock.market_data_api_key = "td-key"
    settings_mock.tiingo_api_key = "tiingo-key"
    settings_mock.alpaca_api_key_id = ""
    settings_mock.alpaca_api_secret_key = ""
    asset = _gbpjpy_asset()
    store = FakeStore()
    mark_blocked(store, "HTTP 429: quota exhausted")

    td = MagicMock()
    td.fetch_latest.side_effect = TwelveDataError("blocked", code=429)
    tiingo = MagicMock()
    tiingo.fetch_latest.return_value = [_candle(datetime(2026, 9, 11, 10, 5, tzinfo=timezone.utc), source="tiingo")]

    def _factory(name, asset=None):
        return td if name == "twelvedata" else tiingo

    provider_factory.side_effect = _factory

    outcome = fetch_with_failover(
        store,
        asset,
        caller="test",
        priority=3,
        fetch_fn=lambda p: p.fetch_latest("inst-1", "5m"),
    )
    assert outcome.provider == ProviderName.TIINGO
    assert outcome.candles[0].source == "tiingo"


@patch("quantara_engine.market_data.provider_resolver.settings")
@patch("quantara_engine.market_data.provider_resolver.get_market_data_provider")
def test_twelve_data_timeout_uses_fallback(provider_factory, settings_mock):
    settings_mock.market_data_api_key = "td-key"
    settings_mock.tiingo_api_key = "tiingo-key"
    settings_mock.alpaca_api_key_id = ""
    settings_mock.alpaca_api_secret_key = ""
    asset = _gbpjpy_asset()
    store = FakeStore()

    td = MagicMock()
    td.fetch_latest.side_effect = TwelveDataError("Network error: timeout")
    tiingo = MagicMock()
    tiingo.fetch_latest.return_value = [_candle(datetime(2026, 9, 11, 10, 10, tzinfo=timezone.utc), source="tiingo")]

    provider_factory.side_effect = lambda name, asset=None: td if name == "twelvedata" else tiingo

    outcome = fetch_with_failover(
        store,
        asset,
        caller="test",
        priority=3,
        fetch_fn=lambda p: p.fetch_latest("inst-1", "5m"),
    )
    assert outcome.provider == ProviderName.TIINGO
    assert is_in_cooldown(store, "twelvedata")


@patch("quantara_engine.market_data.provider_resolver.settings")
@patch("quantara_engine.market_data.provider_resolver.get_market_data_provider")
def test_tiingo_unavailable_next_or_empty(provider_factory, settings_mock):
    settings_mock.market_data_api_key = "td-key"
    settings_mock.tiingo_api_key = "tiingo-key"
    settings_mock.alpaca_api_key_id = ""
    settings_mock.alpaca_api_secret_key = ""
    asset = _gbpjpy_asset()
    store = FakeStore()
    mark_cooldown(store, "tiingo", reason="HTTP 503")

    td = MagicMock()
    td.fetch_latest.side_effect = TwelveDataError("Network error", code=503)
    provider_factory.return_value = td

    outcome = fetch_with_failover(
        store,
        asset,
        caller="test",
        priority=3,
        fetch_fn=lambda p: p.fetch_latest("inst-1", "5m"),
    )
    assert outcome.provider is None
    assert outcome.candles == []


def test_provider_switch_no_duplicate_candle():
    ts = datetime(2026, 9, 11, 10, 0, tzinfo=timezone.utc)
    candles = [
        _candle(ts, source="twelvedata"),
        _candle(ts, source="tiingo"),
        _candle(ts + timedelta(minutes=5), source="tiingo"),
    ]
    deduped = dedupe_complete_candles(candles)
    assert len(deduped) == 2
    assert deduped[0].timestamp == ts
    assert deduped[1].timestamp == ts + timedelta(minutes=5)


def test_provider_switch_no_lookahead():
    ts = datetime(2026, 9, 11, 10, 0, tzinfo=timezone.utc)
    incomplete = Candle(
        instrument_id="inst-1",
        timeframe="5m",
        timestamp=ts + timedelta(minutes=5),
        open=Decimal("1"),
        high=Decimal("2"),
        low=Decimal("0.5"),
        close=Decimal("1.5"),
        volume=None,
        source="tiingo",
        is_complete=False,
    )
    deduped = dedupe_complete_candles([_candle(ts), incomplete])
    assert len(deduped) == 1


def test_different_provider_timestamps_normalized():
    ts = datetime(2026, 9, 11, 10, 0, tzinfo=timezone.utc)
    c1 = _candle(ts.replace(tzinfo=None).replace(tzinfo=timezone.utc), source="twelvedata")
    c2 = _candle(ts, source="tiingo")
    deduped = dedupe_complete_candles([c1, c2])
    assert len(deduped) == 1


@patch("quantara_engine.market_data.provider_resolver.settings")
@patch("quantara_engine.market_data.provider_resolver.get_market_data_provider")
def test_repeated_cycles_no_twelve_data_hammering(provider_factory, settings_mock):
    settings_mock.market_data_api_key = "td-key"
    settings_mock.tiingo_api_key = "tiingo-key"
    settings_mock.alpaca_api_key_id = ""
    settings_mock.alpaca_api_secret_key = ""
    asset = _gbpjpy_asset()
    store = FakeStore()
    mark_blocked(store, "HTTP 429: quota exhausted")

    tiingo = MagicMock()
    tiingo.fetch_latest.return_value = [_candle(datetime(2026, 9, 11, 10, 0, tzinfo=timezone.utc), source="tiingo")]
    provider_factory.return_value = tiingo

    for _ in range(5):
        fetch_with_failover(
            store,
            asset,
            caller="test",
            priority=3,
            fetch_fn=lambda p: p.fetch_latest("inst-1", "5m"),
        )

    # Tiingo only — Twelve Data never instantiated when blocked.
    assert all(call.args[0] == "tiingo" for call in provider_factory.call_args_list)


def _nvda_asset() -> AssetDefinition:
    asset = get_asset("NVDA")
    assert asset is not None
    return asset


def _btc_asset() -> AssetDefinition:
    asset = get_asset("BTCUSD")
    assert asset is not None
    return asset


@patch("quantara_engine.market_data.provider_resolver.settings")
@patch("quantara_engine.market_data.provider_resolver.get_market_data_provider")
def test_alpaca_healthy_primary_used(provider_factory, settings_mock):
    settings_mock.market_data_api_key = ""
    settings_mock.tiingo_api_key = "tiingo-key"
    settings_mock.alpaca_api_key_id = "alpaca-key"
    settings_mock.alpaca_api_secret_key = "alpaca-secret"
    asset = _nvda_asset()
    alpaca = MagicMock()
    alpaca.fetch_latest.return_value = [_candle(datetime(2026, 9, 11, 14, 30, tzinfo=timezone.utc), source="alpaca")]
    provider_factory.return_value = alpaca

    store = FakeStore()
    outcome = fetch_with_failover(
        store,
        asset,
        caller="test",
        priority=3,
        fetch_fn=lambda p: p.fetch_latest("inst-1", "5m"),
    )
    assert outcome.provider == ProviderName.ALPACA
    provider_factory.assert_called_once_with("alpaca", asset=asset)


@patch("quantara_engine.market_data.provider_resolver.settings")
@patch("quantara_engine.market_data.provider_resolver.get_market_data_provider")
def test_alpaca_failure_tiingo_fallback(provider_factory, settings_mock):
    settings_mock.market_data_api_key = ""
    settings_mock.tiingo_api_key = "tiingo-key"
    settings_mock.alpaca_api_key_id = "alpaca-key"
    settings_mock.alpaca_api_secret_key = "alpaca-secret"
    asset = _nvda_asset()
    store = FakeStore()

    alpaca = MagicMock()
    alpaca.fetch_latest.side_effect = AlpacaError("Network error: timeout")
    tiingo = MagicMock()
    tiingo.fetch_latest.return_value = [_candle(datetime(2026, 9, 11, 14, 35, tzinfo=timezone.utc), source="tiingo")]

    provider_factory.side_effect = lambda name, asset=None: alpaca if name == "alpaca" else tiingo

    outcome = fetch_with_failover(
        store,
        asset,
        caller="test",
        priority=3,
        fetch_fn=lambda p: p.fetch_latest("inst-1", "5m"),
    )
    assert outcome.provider == ProviderName.TIINGO
    assert is_in_cooldown(store, "alpaca")


@patch("quantara_engine.market_data.provider_resolver.settings")
@patch("quantara_engine.market_data.provider_resolver.get_market_data_provider")
def test_alpaca_failure_no_fallback_empty(provider_factory, settings_mock):
    settings_mock.market_data_api_key = ""
    settings_mock.tiingo_api_key = ""
    settings_mock.alpaca_api_key_id = "alpaca-key"
    settings_mock.alpaca_api_secret_key = "alpaca-secret"
    asset = _nvda_asset()
    store = FakeStore()
    alpaca = MagicMock()
    alpaca.fetch_latest.side_effect = AlpacaError("HTTP 503")
    provider_factory.return_value = alpaca

    outcome = fetch_with_failover(
        store,
        asset,
        caller="test",
        priority=3,
        fetch_fn=lambda p: p.fetch_latest("inst-1", "5m"),
    )
    assert outcome.provider is None
    assert outcome.candles == []


@patch("quantara_engine.market_data.provider_resolver.settings")
@patch("quantara_engine.market_data.provider_resolver.get_market_data_provider")
def test_alpaca_cooldown_recovery_after_expiry(provider_factory, settings_mock):
    settings_mock.market_data_api_key = ""
    settings_mock.tiingo_api_key = "tiingo-key"
    settings_mock.alpaca_api_key_id = "alpaca-key"
    settings_mock.alpaca_api_secret_key = "alpaca-secret"
    asset = _btc_asset()
    store = FakeStore()
    mark_cooldown(store, "alpaca", reason="timeout")

    coinbase = MagicMock()
    coinbase.fetch_latest.return_value = [
        _candle(datetime(2026, 9, 11, 10, 0, tzinfo=timezone.utc), source="coinbase")
    ]
    tiingo = MagicMock()
    tiingo.fetch_latest.return_value = [_candle(datetime(2026, 9, 11, 10, 0, tzinfo=timezone.utc), source="tiingo")]

    def _factory(name, asset=None):
        if name == "coinbase":
            return coinbase
        return tiingo

    provider_factory.side_effect = _factory

    outcome = fetch_with_failover(
        store,
        asset,
        caller="test",
        priority=3,
        fetch_fn=lambda p: p.fetch_latest("inst-1", "5m"),
    )
    assert outcome.provider == ProviderName.COINBASE

    from quantara_engine.market_data.provider_cooldown import clear_cooldown

    clear_cooldown(store, "alpaca")
    alpaca = MagicMock()
    alpaca.fetch_latest.return_value = [_candle(datetime(2026, 9, 11, 10, 5, tzinfo=timezone.utc), source="alpaca")]

    def _factory2(name, asset=None):
        if name == "alpaca":
            return alpaca
        if name == "coinbase":
            return coinbase
        return tiingo

    provider_factory.side_effect = _factory2
    outcome2 = fetch_with_failover(
        store,
        asset,
        caller="test",
        priority=3,
        fetch_fn=lambda p: p.fetch_latest("inst-1", "5m"),
    )
    # BTC primary is Coinbase; Alpaca is secondary on crypto assets.
    assert outcome2.provider == ProviderName.COINBASE


def test_btc_provider_chain():
    chain = provider_chain_for_asset(_btc_asset())
    assert chain == (ProviderName.COINBASE, ProviderName.ALPACA, ProviderName.TIINGO)


def test_nvda_provider_chain():
    chain = provider_chain_for_asset(_nvda_asset())
    assert chain == (ProviderName.ALPACA, ProviderName.TIINGO)


def test_gbpjpy_provider_chain():
    chain = provider_chain_for_asset(_gbpjpy_asset())
    assert chain == (ProviderName.TWELVE_DATA, ProviderName.TIINGO)


def test_xauusd_provider_chain():
    chain = provider_chain_for_asset(_xau_asset())
    assert chain == (ProviderName.TWELVE_DATA, ProviderName.TIINGO)


@patch("quantara_engine.market_data.provider_resolver.settings")
def test_has_eligible_provider_when_primary_blocked(settings_mock):
    settings_mock.market_data_api_key = "key"
    settings_mock.tiingo_api_key = "tiingo-key"
    settings_mock.alpaca_api_key_id = ""
    settings_mock.alpaca_api_secret_key = ""

    store = FakeStore()
    mark_blocked(store, "429")
    asset = _gbpjpy_asset()
    assert has_eligible_provider(store, asset)


@patch("quantara_engine.portfolio.fx_rate_cache._fetch_rate_from_tiingo")
@patch("quantara_engine.portfolio.fx_rate_cache._fetch_rate_from_db_candles")
def test_jpy_conversion_cached_hourly_no_per_position_calls(db_mock, tiingo_mock):
    from quantara_engine.portfolio.fx_rate_cache import get_canonical_jpy_per_usd

    db_mock.return_value = None
    tiingo_mock.return_value = Decimal("148.5")

    store = FakeStore()
    stale = datetime.now(timezone.utc) - timedelta(hours=2)
    store.update_settings(
        "fx_rate_cache:jpy_usd",
        {
            "rate": "150.0",
            "updated_at": stale.isoformat(),
            "source": "test",
        },
    )

    rates = [get_canonical_jpy_per_usd(store) for _ in range(50)]
    assert all(r == Decimal("148.5") for r in rates)
    assert tiingo_mock.call_count == 1


@patch("quantara_engine.portfolio.fx_rate_cache._fetch_rate_from_twelve_data")
@patch("quantara_engine.portfolio.fx_rate_cache._fetch_rate_from_tiingo")
@patch("quantara_engine.portfolio.fx_rate_cache._fetch_rate_from_db_candles")
def test_jpy_conversion_tiingo_before_twelve_data(db_mock, tiingo_mock, td_mock):
    from quantara_engine.portfolio.fx_rate_cache import get_canonical_jpy_per_usd

    db_mock.return_value = None
    tiingo_mock.return_value = Decimal("149.2")
    td_mock.return_value = Decimal("999")

    store = FakeStore()
    stale = datetime.now(timezone.utc) - timedelta(hours=3)
    store.update_settings(
        "fx_rate_cache:jpy_usd",
        {"rate": "150", "updated_at": stale.isoformat(), "source": "test"},
    )

    rate = get_canonical_jpy_per_usd(store)
    assert rate == Decimal("149.2")
    tiingo_mock.assert_called_once()
    td_mock.assert_not_called()
