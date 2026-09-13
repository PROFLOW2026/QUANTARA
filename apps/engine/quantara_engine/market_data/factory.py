"""Market data provider factory."""

from __future__ import annotations

from quantara_engine.core.config import settings
from quantara_engine.market_data.adapters.alpaca import AlpacaMarketDataProvider
from quantara_engine.market_data.adapters.coinbase import CoinbaseMarketDataProvider
from quantara_engine.market_data.adapters.finnhub import FinnhubMarketDataProvider
from quantara_engine.market_data.adapters.mock import MockMarketDataProvider
from quantara_engine.market_data.adapters.tiingo import TiingoMarketDataProvider
from quantara_engine.market_data.adapters.twelvedata import TwelveDataMarketDataProvider
from quantara_engine.market_data.provider import MarketDataProvider
from quantara_engine.market_data.registry import AssetDefinition, ProviderName


def get_market_data_provider(
    name: str | None = None,
    *,
    asset: AssetDefinition | None = None,
) -> MarketDataProvider:
    provider = (name or settings.market_data_provider).strip().lower()
    if provider == "mock":
        return MockMarketDataProvider()
    if provider == "twelvedata":
        return TwelveDataMarketDataProvider(asset=asset)
    if provider == "alpaca":
        return AlpacaMarketDataProvider(asset=asset)
    if provider == "tiingo":
        return TiingoMarketDataProvider(asset=asset)
    if provider == "coinbase":
        return CoinbaseMarketDataProvider(asset=asset)
    if provider == "finnhub":
        return FinnhubMarketDataProvider(asset=asset)
    raise ValueError(f"Unknown market data provider: {provider}")


def get_provider_for_asset(asset: AssetDefinition, *, role: str = "primary") -> MarketDataProvider:
    if role == "secondary" and asset.secondary_provider:
        provider_name = asset.secondary_provider.value
    else:
        provider_name = asset.primary_provider.value
    return get_market_data_provider(provider_name, asset=asset)
