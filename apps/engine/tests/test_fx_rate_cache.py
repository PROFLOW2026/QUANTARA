"""Hourly canonical JPY→USD FX rate cache."""

from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from quantara_engine.domain.types import Direction, Instrument
from quantara_engine.portfolio.currency import build_currency_context, resolve_fx_rates
from quantara_engine.portfolio.fx_rate_cache import (
    CACHE_SETTINGS_KEY as FX_CACHE_KEY,
    CachedFxRate,
    _serialize_cache,
    get_canonical_jpy_per_usd,
)
from quantara_engine.portfolio.pnl import unrealized_pnl

# Re-export for tests — canonical key lives in fx_rate_cache module.
assert FX_CACHE_KEY == "fx_rate_cache:jpy_usd"


def _gbpjpy() -> Instrument:
    return Instrument(
        id="gbp-id",
        symbol="GBPJPY",
        name="GBP/JPY",
        asset_class="forex",
        base_currency="GBP",
        quote_currency="JPY",
        pip_size=Decimal("0.01"),
        contract_size=Decimal("100000"),
        price_tick_size=Decimal("0.001"),
        quantity_step=Decimal("1000"),
        min_quantity=Decimal("1000"),
    )


class FakeStore:
    def __init__(self, settings: dict | None = None):
        self._settings = dict(settings or {})
        self.session = MagicMock()
        self.session.execute.return_value.scalar.return_value = True

    def get_settings_dict(self) -> dict:
        return self._settings

    def update_settings(self, key: str, value, description: str | None = None, *, flush: bool = True):
        if value is None:
            self._settings.pop(key, None)
        else:
            self._settings[key] = value

    def ensure_usdjpy_conversion_instrument(self) -> None:
        pass

    def resolve_jpy_per_usd(self) -> Decimal:
        return get_canonical_jpy_per_usd(self)


def _fresh_cache(rate: str = "154.25", *, minutes_ago: int = 5) -> dict:
    updated = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    entry = CachedFxRate(rate=Decimal(rate), updated_at=updated, source="test")
    return {FX_CACHE_KEY: _serialize_cache(entry)}


def _stale_cache(rate: str = "150.00", *, hours_ago: int = 2) -> dict:
    updated = datetime.now(timezone.utc) - timedelta(hours=hours_ago)
    entry = CachedFxRate(rate=Decimal(rate), updated_at=updated, source="test")
    return {FX_CACHE_KEY: _serialize_cache(entry)}


@patch("quantara_engine.portfolio.fx_rate_cache._fetch_rate_from_provider")
@patch("quantara_engine.portfolio.fx_rate_cache._fetch_rate_from_db_candles")
def test_1000_calls_within_hour_no_provider(db_mock, provider_mock):
    db_mock.return_value = None
    provider_mock.return_value = (Decimal("999"), "twelvedata")

    store = FakeStore(_fresh_cache("151.5"))
    rates = [get_canonical_jpy_per_usd(store) for _ in range(1000)]

    assert all(r == Decimal("151.5") for r in rates)
    provider_mock.assert_not_called()
    db_mock.assert_not_called()


@patch("quantara_engine.portfolio.fx_rate_cache._fetch_rate_from_provider")
@patch("quantara_engine.portfolio.fx_rate_cache._fetch_rate_from_db_candles")
def test_stale_cache_single_provider_refresh(db_mock, provider_mock):
    db_mock.return_value = None
    provider_mock.return_value = (Decimal("155.0"), "tiingo")

    store = FakeStore(_stale_cache("150.0"))
    first = get_canonical_jpy_per_usd(store)
    rest = [get_canonical_jpy_per_usd(store) for _ in range(999)]

    assert first == Decimal("155.0")
    assert all(r == Decimal("155.0") for r in rest)
    assert provider_mock.call_count == 1


@patch("quantara_engine.portfolio.fx_rate_cache._fetch_rate_from_provider")
@patch("quantara_engine.portfolio.fx_rate_cache._fetch_rate_from_db_candles")
def test_multiple_portfolios_same_cached_rate(db_mock, provider_mock):
    db_mock.return_value = None
    provider_mock.return_value = (Decimal("160"), "tiingo")

    settings = _fresh_cache("148.2")
    stores = [FakeStore(settings) for _ in range(5)]
    resolved = [resolve_fx_rates(s, {"JPY"}) for s in stores]

    assert all(r.quote_per_usd["JPY"] == Decimal("148.2") for r in resolved)
    provider_mock.assert_not_called()


@patch("quantara_engine.portfolio.fx_rate_cache._fetch_rate_from_provider")
@patch("quantara_engine.portfolio.fx_rate_cache._fetch_rate_from_db_candles")
def test_provider_failure_uses_last_known(db_mock, provider_mock):
    db_mock.return_value = None
    provider_mock.side_effect = RuntimeError("credit limit exhausted")

    store = FakeStore(_stale_cache("153.75", hours_ago=3))
    rate = get_canonical_jpy_per_usd(store)

    assert rate == Decimal("153.75")
    assert provider_mock.call_count == 1


@patch("quantara_engine.portfolio.fx_rate_cache._fetch_rate_from_provider")
@patch("quantara_engine.portfolio.fx_rate_cache._fetch_rate_from_db_candles")
def test_provider_failure_no_retry_storm(db_mock, provider_mock):
    db_mock.return_value = None
    provider_mock.side_effect = RuntimeError("credit limit exhausted")

    store = FakeStore(_stale_cache("153.75", hours_ago=3))
    for _ in range(100):
        assert get_canonical_jpy_per_usd(store) == Decimal("153.75")
    assert provider_mock.call_count == 1


@patch("quantara_engine.portfolio.fx_rate_cache._fetch_rate_from_provider")
@patch("quantara_engine.portfolio.fx_rate_cache._fetch_rate_from_db_candles")
def test_db_candle_preferred_over_provider(db_mock, provider_mock):
    db_mock.return_value = Decimal("152.1")
    provider_mock.return_value = (Decimal("999"), "twelvedata")

    store = FakeStore(_stale_cache("150.0"))
    rate = get_canonical_jpy_per_usd(store)

    assert rate == Decimal("152.1")
    provider_mock.assert_not_called()
    assert db_mock.call_count == 1


@patch("quantara_engine.portfolio.fx_rate_cache._fetch_rate_from_provider")
@patch("quantara_engine.portfolio.fx_rate_cache._fetch_rate_from_db_candles")
def test_concurrent_refresh_single_provider_call(db_mock, provider_mock):
    db_mock.return_value = None
    provider_mock.return_value = (Decimal("157"), "tiingo")

    store = FakeStore(_stale_cache("150.0"))
    results: list[Decimal] = []
    barrier = threading.Barrier(8)

    def worker():
        barrier.wait()
        results.append(get_canonical_jpy_per_usd(store))

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(results) == 8
    assert all(r == Decimal("157") for r in results)
    assert provider_mock.call_count == 1


@patch("quantara_engine.portfolio.fx_rate_cache._fetch_rate_from_provider")
@patch("quantara_engine.portfolio.fx_rate_cache._fetch_rate_from_db_candles")
def test_pnl_conversion_uses_cached_rate(db_mock, provider_mock):
    db_mock.return_value = None
    provider_mock.return_value = (Decimal("999"), "twelvedata")

    store = FakeStore(_fresh_cache("150"))
    ctx = build_currency_context(store, [_gbpjpy()])
    inst = _gbpjpy()

    pnl = unrealized_pnl(
        Direction.LONG,
        Decimal("200.000"),
        Decimal("201.000"),
        Decimal("1000"),
        inst,
        ctx.fx_rates,
    )
    # (201-200)*1000 JPY / 150 = 6.67 USD
    assert pnl == Decimal("6.67")
    provider_mock.assert_not_called()


def test_resolve_jpy_per_usd_delegates_to_cache():
    store = FakeStore(_fresh_cache("149.99"))
    assert store.resolve_jpy_per_usd() == Decimal("149.99")
