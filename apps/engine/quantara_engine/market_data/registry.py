"""Asset provider registry — routes each canonical symbol to primary/secondary providers."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ProviderName(str, Enum):
    TWELVE_DATA = "twelvedata"
    ALPACA = "alpaca"
    TIINGO = "tiingo"
    MOCK = "mock"


class AssetClass(str, Enum):
    COMMODITY = "commodity"
    FOREX = "forex"
    INDEX = "index"
    STOCK = "stock"
    CRYPTO = "crypto"


@dataclass(frozen=True)
class AssetDefinition:
    canonical_symbol: str
    db_symbol: str
    display_symbol: str
    asset_class: AssetClass
    primary_provider: ProviderName
    secondary_provider: ProviderName | None
    provider_symbols: dict[str, str]
    trading_sessions: dict
    pip_size: str
    price_tick_size: str
    quantity_step: str
    min_quantity: str


TARGET_ASSETS: tuple[AssetDefinition, ...] = (
    AssetDefinition(
        canonical_symbol="XAU/USD",
        db_symbol="XAUUSD",
        display_symbol="XAU/USD",
        asset_class=AssetClass.COMMODITY,
        primary_provider=ProviderName.TWELVE_DATA,
        secondary_provider=None,
        provider_symbols={
            ProviderName.TWELVE_DATA.value: "XAU/USD",
        },
        trading_sessions={"sessions": ["24x5"]},
        pip_size="0.01",
        price_tick_size="0.01",
        quantity_step="0.01",
        min_quantity="0.01",
    ),
    AssetDefinition(
        canonical_symbol="EUR/USD",
        db_symbol="EURUSD",
        display_symbol="EUR/USD",
        asset_class=AssetClass.FOREX,
        primary_provider=ProviderName.TWELVE_DATA,
        secondary_provider=None,
        provider_symbols={
            ProviderName.TWELVE_DATA.value: "EUR/USD",
        },
        trading_sessions={"sessions": ["24x5"]},
        pip_size="0.0001",
        price_tick_size="0.00001",
        quantity_step="1000",
        min_quantity="1000",
    ),
    AssetDefinition(
        canonical_symbol="SPY",
        db_symbol="SPY",
        display_symbol="SPY",
        asset_class=AssetClass.INDEX,
        primary_provider=ProviderName.TIINGO,
        secondary_provider=ProviderName.ALPACA,
        provider_symbols={
            ProviderName.TIINGO.value: "SPY",
            ProviderName.ALPACA.value: "SPY",
        },
        trading_sessions={"sessions": ["us_equity_rth"]},
        pip_size="0.01",
        price_tick_size="0.01",
        quantity_step="1",
        min_quantity="1",
    ),
    AssetDefinition(
        canonical_symbol="QQQ",
        db_symbol="QQQ",
        display_symbol="QQQ",
        asset_class=AssetClass.INDEX,
        primary_provider=ProviderName.TIINGO,
        secondary_provider=ProviderName.ALPACA,
        provider_symbols={
            ProviderName.TIINGO.value: "QQQ",
            ProviderName.ALPACA.value: "QQQ",
        },
        trading_sessions={"sessions": ["us_equity_rth"]},
        pip_size="0.01",
        price_tick_size="0.01",
        quantity_step="1",
        min_quantity="1",
    ),
    AssetDefinition(
        canonical_symbol="NVDA",
        db_symbol="NVDA",
        display_symbol="NVDA",
        asset_class=AssetClass.STOCK,
        primary_provider=ProviderName.TIINGO,
        secondary_provider=ProviderName.ALPACA,
        provider_symbols={
            ProviderName.TIINGO.value: "NVDA",
            ProviderName.ALPACA.value: "NVDA",
        },
        trading_sessions={"sessions": ["us_equity_rth"]},
        pip_size="0.01",
        price_tick_size="0.01",
        quantity_step="1",
        min_quantity="1",
    ),
    AssetDefinition(
        canonical_symbol="AAPL",
        db_symbol="AAPL",
        display_symbol="AAPL",
        asset_class=AssetClass.STOCK,
        primary_provider=ProviderName.TIINGO,
        secondary_provider=ProviderName.ALPACA,
        provider_symbols={
            ProviderName.TIINGO.value: "AAPL",
            ProviderName.ALPACA.value: "AAPL",
        },
        trading_sessions={"sessions": ["us_equity_rth"]},
        pip_size="0.01",
        price_tick_size="0.01",
        quantity_step="1",
        min_quantity="1",
    ),
    AssetDefinition(
        canonical_symbol="MSFT",
        db_symbol="MSFT",
        display_symbol="MSFT",
        asset_class=AssetClass.STOCK,
        primary_provider=ProviderName.TIINGO,
        secondary_provider=ProviderName.ALPACA,
        provider_symbols={
            ProviderName.TIINGO.value: "MSFT",
            ProviderName.ALPACA.value: "MSFT",
        },
        trading_sessions={"sessions": ["us_equity_rth"]},
        pip_size="0.01",
        price_tick_size="0.01",
        quantity_step="1",
        min_quantity="1",
    ),
    AssetDefinition(
        canonical_symbol="BTC/USD",
        db_symbol="BTCUSD",
        display_symbol="BTC/USD",
        asset_class=AssetClass.CRYPTO,
        primary_provider=ProviderName.ALPACA,
        secondary_provider=ProviderName.TIINGO,
        provider_symbols={
            ProviderName.ALPACA.value: "BTC/USD",
            ProviderName.TIINGO.value: "btcusd",
        },
        trading_sessions={"sessions": ["24x7"]},
        pip_size="0.01",
        price_tick_size="0.01",
        quantity_step="0.0001",
        min_quantity="0.0001",
    ),
)

_ASSET_BY_DB: dict[str, AssetDefinition] = {a.db_symbol: a for a in TARGET_ASSETS}
_ASSET_BY_CANONICAL: dict[str, AssetDefinition] = {a.canonical_symbol: a for a in TARGET_ASSETS}


def list_target_assets() -> tuple[AssetDefinition, ...]:
    return TARGET_ASSETS


def get_asset(db_symbol: str) -> AssetDefinition | None:
    return _ASSET_BY_DB.get(db_symbol.upper())


def get_asset_by_canonical(symbol: str) -> AssetDefinition | None:
    key = symbol.upper().replace(" ", "")
    for asset in TARGET_ASSETS:
        if asset.db_symbol == key or asset.canonical_symbol.replace("/", "") == key:
            return asset
        if asset.display_symbol.upper() == symbol.upper():
            return asset
    return _ASSET_BY_CANONICAL.get(symbol)


def provider_symbol(asset: AssetDefinition, provider: ProviderName) -> str:
    return asset.provider_symbols[provider.value]
