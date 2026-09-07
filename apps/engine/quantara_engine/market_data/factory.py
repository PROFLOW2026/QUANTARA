"""Market data provider factory."""

from __future__ import annotations

from quantara_engine.core.config import settings
from quantara_engine.market_data.adapters.mock import MockMarketDataProvider
from quantara_engine.market_data.adapters.twelvedata import TwelveDataMarketDataProvider
from quantara_engine.market_data.provider import MarketDataProvider


def get_market_data_provider(name: str | None = None) -> MarketDataProvider:
    provider = (name or settings.market_data_provider).strip().lower()
    if provider == "mock":
        return MockMarketDataProvider()
    if provider == "twelvedata":
        return TwelveDataMarketDataProvider()
    raise ValueError(f"Unknown MARKET_DATA_PROVIDER: {provider}")
