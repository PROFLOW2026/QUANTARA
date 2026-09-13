"""Finnhub validation/backup provider tests — no live API key required."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from quantara_engine.market_data.adapters.finnhub import (
    FinnhubMarketDataProvider,
    FinnhubObservation,
    finnhub_configured,
)
from quantara_engine.market_data.finnhub_thresholds import (
    DEFAULT_THRESHOLDS,
    load_thresholds,
    price_within_tolerance,
)
from quantara_engine.market_data.finnhub_validation import (
    STATUS_DIVERGENCE,
    STATUS_OK,
    STATUS_SKIPPED,
    STATUS_STALE_BACKUP,
    compare_quotes,
    run_finnhub_validation,
)
from quantara_engine.market_data.provider_budgets import all_provider_status
from quantara_engine.market_data.provider_resolver import (
    backup_providers_for_asset,
    is_provider_configured,
    is_provider_eligible,
    provider_chain_for_asset,
)
from quantara_engine.market_data.registry import ProviderName, get_asset


class FakeStore:
    def __init__(self, settings: dict | None = None):
        self._settings = dict(settings or {})
        self._writes: list[tuple[str, object]] = []

    def get_settings_dict(self) -> dict:
        return self._settings

    def update_settings(self, key: str, value, description: str | None = None, *, flush: bool = True):
        self._writes.append((key, value))
        if value is None:
            self._settings.pop(key, None)
        else:
            self._settings[key] = value

    def get_instrument_by_symbol(self, symbol: str):
        return None

    def latest_candle_timestamp(self, instrument_id: str, timeframe: str):
        return None

    def list_candles(self, instrument_id: str, timeframe: str, limit: int = 100):
        return []


def _obs(
    asset: str,
    price: str,
    *,
    age_seconds: float = 30.0,
) -> FinnhubObservation:
    now = datetime.now(timezone.utc)
    ts = now - timedelta(seconds=age_seconds)
    return FinnhubObservation(
        provider="finnhub",
        asset=asset,
        provider_symbol="TEST",
        price=Decimal(price),
        timestamp=ts,
        received_at=now,
        data_type="quote",
        freshness_seconds=age_seconds,
    )


@patch("quantara_engine.market_data.adapters.finnhub.settings")
def test_finnhub_disabled_without_key(settings_mock):
    settings_mock.finnhub_api_key = ""
    assert finnhub_configured() is False
    assert is_provider_configured(ProviderName.FINNHUB) is False


@patch("quantara_engine.market_data.adapters.finnhub.settings")
def test_finnhub_configured_with_key(settings_mock):
    settings_mock.finnhub_api_key = "test-key"
    assert finnhub_configured() is True
    assert is_provider_configured(ProviderName.FINNHUB) is True


@patch("quantara_engine.market_data.adapters.finnhub.settings")
def test_finnhub_not_in_primary_chain(settings_mock):
    settings_mock.finnhub_api_key = "test-key"
    asset = get_asset("NVDA")
    assert asset is not None
    chain = provider_chain_for_asset(asset)
    assert ProviderName.FINNHUB not in chain
    assert ProviderName.ALPACA in chain


@patch("quantara_engine.market_data.adapters.finnhub.settings")
def test_finnhub_not_eligible_for_candles(settings_mock):
    settings_mock.finnhub_api_key = "test-key"
    assert is_provider_eligible(FakeStore(), ProviderName.FINNHUB, purpose="candles") is False
    assert is_provider_eligible(FakeStore(), ProviderName.FINNHUB, purpose="validation") is True


def test_registry_finnhub_symbols():
    nvda = get_asset("NVDA")
    btc = get_asset("BTCUSD")
    gbp = get_asset("GBPJPY")
    xau = get_asset("XAUUSD")
    assert nvda is not None and btc is not None and gbp is not None and xau is not None
    assert nvda.provider_symbols["finnhub"] == "NVDA"
    assert btc.provider_symbols["finnhub"] == "COINBASE:BTC-USD"
    assert gbp.provider_symbols["finnhub"] == "OANDA:GBP_JPY"
    assert xau.provider_symbols["finnhub"] == "OANDA:XAU_USD"


def test_adapter_parse_quote_normalization():
    now = datetime.now(timezone.utc)
    payload = {"c": 123.45, "t": int(now.timestamp())}
    obs = FinnhubMarketDataProvider._parse_quote_payload(
        payload,
        asset="NVDA",
        provider_symbol="NVDA",
        received_at=now,
    )
    assert obs is not None
    assert obs.provider == "finnhub"
    assert obs.asset == "NVDA"
    assert obs.price == Decimal("123.45")
    assert obs.data_type == "quote"


def test_price_tolerance_crypto_and_equity():
    btc = get_asset("BTCUSD")
    nvda = get_asset("NVDA")
    assert btc is not None and nvda is not None
    th = DEFAULT_THRESHOLDS
    assert price_within_tolerance(Decimal("100000"), Decimal("100400"), btc, th) is True
    assert price_within_tolerance(Decimal("100000"), Decimal("101000"), btc, th) is False
    assert price_within_tolerance(Decimal("100"), Decimal("100.04"), nvda, th) is True
    assert price_within_tolerance(Decimal("100"), Decimal("102"), nvda, th) is False


def test_stale_finnhub_ignored_for_divergence():
    asset = get_asset("NVDA")
    assert asset is not None
    now = datetime.now(timezone.utc)
    row = compare_quotes(
        asset=asset,
        canonical_price=Decimal("100"),
        canonical_at=now - timedelta(seconds=30),
        canonical_provider="alpaca",
        finnhub_obs=_obs("NVDA", "200", age_seconds=400),
        thresholds=DEFAULT_THRESHOLDS,
        now=now,
    )
    assert row["status"] == STATUS_STALE_BACKUP
    assert row["status_he"] == "נתון גיבוי מיושן"


def test_divergence_warning_only():
    asset = get_asset("NVDA")
    assert asset is not None
    now = datetime.now(timezone.utc)
    row = compare_quotes(
        asset=asset,
        canonical_price=Decimal("100"),
        canonical_at=now - timedelta(seconds=30),
        canonical_provider="alpaca",
        finnhub_obs=_obs("NVDA", "120"),
        thresholds=DEFAULT_THRESHOLDS,
        now=now,
    )
    assert row["status"] == STATUS_DIVERGENCE
    assert "סטיית מחיר" in row["status_he"]


def test_healthy_validation_match():
    asset = get_asset("ETHUSD")
    assert asset is not None
    now = datetime.now(timezone.utc)
    row = compare_quotes(
        asset=asset,
        canonical_price=Decimal("3000"),
        canonical_at=now - timedelta(seconds=20),
        canonical_provider="coinbase",
        finnhub_obs=_obs("ETHUSD", "3005"),
        thresholds=DEFAULT_THRESHOLDS,
        now=now,
    )
    assert row["status"] == STATUS_OK
    assert row["status_he"] == "תקין"


@patch("quantara_engine.market_data.finnhub_validation.finnhub_configured", return_value=False)
def test_missing_key_skips_validation(_mock):
    store = FakeStore()
    report = run_finnhub_validation(store)
    assert report["status"] == STATUS_SKIPPED
    assert "FINNHUB_API_KEY" in report["reason"]


@patch("quantara_engine.market_data.finnhub_validation.finnhub_configured", return_value=True)
@patch("quantara_engine.market_data.finnhub_validation.can_request", return_value=False)
def test_rate_limit_skips_validation(_can, _cfg):
    store = FakeStore()
    report = run_finnhub_validation(store)
    assert report["status"] == STATUS_SKIPPED
    assert "budget" in report["reason"]


@patch("quantara_engine.market_data.adapters.finnhub.finnhub_configured", return_value=True)
def test_provider_health_includes_finnhub_when_configured(_cfg):
    store = FakeStore({"provider_budget:finnhub": {"status": "healthy", "used_hour": 2}})
    providers = all_provider_status(store)
    assert "finnhub" in providers
    assert providers["finnhub"]["role_he"] == "גיבוי ואימות"
    assert providers["finnhub"]["enabled"] is True


@patch("quantara_engine.market_data.adapters.finnhub.finnhub_configured", return_value=False)
def test_provider_health_hides_finnhub_without_key(_cfg):
    providers = all_provider_status(FakeStore())
    assert "finnhub" not in providers


@patch("quantara_engine.market_data.finnhub_capabilities.load_capabilities")
@patch("quantara_engine.market_data.provider_resolver.is_provider_configured", return_value=True)
def test_backup_provider_registered_not_primary(_configured, load_caps):
    load_caps.return_value = {
        "stock_quote": True,
        "per_asset": {"NVDA": {"quote": True}},
    }
    asset = get_asset("NVDA")
    assert asset is not None
    backups = backup_providers_for_asset(asset)
    assert backups == (ProviderName.FINNHUB,)


@patch(
    "quantara_engine.execution.crypto_mark_valuation.get_fast_canonical_mark",
    return_value=(Decimal("100"), datetime.now(timezone.utc)),
)
@patch("quantara_engine.market_data.finnhub_validation.FinnhubMarketDataProvider")
@patch("quantara_engine.market_data.finnhub_validation.finnhub_configured", return_value=True)
@patch("quantara_engine.market_data.finnhub_validation.can_request", return_value=True)
def test_validation_does_not_touch_canonical_marks(_can, _cfg, provider_cls, _mark):
    asset = get_asset("NVDA")
    assert asset is not None
    provider = MagicMock()
    provider.fetch_quote.return_value = _obs("NVDA", "100.05")
    provider_cls.return_value = provider

    store = FakeStore({"finnhub_capabilities": {"probed_at": "2026-01-01", "stock_quote": True}})
    report = run_finnhub_validation(store)
    assert report["status"] == "success"
    assert all(key != "fast_canonical_marks" for key, _ in store._writes)


def test_load_thresholds_from_settings():
    th = load_thresholds({"finnhub_validation_thresholds": {"crypto_pct": "0.01"}})
    assert th.crypto_pct == Decimal("0.01")
