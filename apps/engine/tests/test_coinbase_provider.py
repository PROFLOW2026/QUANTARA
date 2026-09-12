"""Coinbase public market data provider tests."""

from unittest.mock import MagicMock, patch

from quantara_engine.market_data.adapters.coinbase import CoinbaseMarketDataProvider
from quantara_engine.market_data.registry import ProviderName, TARGET_ASSETS


def _btc_asset():
    return next(a for a in TARGET_ASSETS if a.db_symbol == "BTCUSD")


def test_coinbase_product_id():
    provider = CoinbaseMarketDataProvider(asset=_btc_asset())
    assert provider._product_id() == "BTC-USD"


def test_coinbase_source_tag():
    assert CoinbaseMarketDataProvider.source == "coinbase"


def test_registry_crypto_primary_coinbase():
    asset = _btc_asset()
    assert asset.primary_provider == ProviderName.COINBASE
    assert asset.secondary_provider == ProviderName.ALPACA


def test_coinbase_provider_eligible_without_api_key():
    from quantara_engine.market_data.provider_resolver import is_provider_eligible
    from quantara_engine.market_data.registry import ProviderName
    from tests.test_provider_failover import FakeStore

    assert is_provider_eligible(FakeStore(), ProviderName.COINBASE)
