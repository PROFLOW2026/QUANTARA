"""Asset provider registry — routes each canonical symbol to primary/secondary providers."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from quantara_engine.market_data.active_universe import ACTIVE_DB_SYMBOLS


class ProviderName(str, Enum):
    TWELVE_DATA = "twelvedata"
    ALPACA = "alpaca"
    TIINGO = "tiingo"
    COINBASE = "coinbase"
    FINNHUB = "finnhub"
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


def _equity(db_symbol: str, display: str | None = None) -> AssetDefinition:
    sym = display or db_symbol
    return AssetDefinition(
        canonical_symbol=sym,
        db_symbol=db_symbol,
        display_symbol=sym,
        asset_class=AssetClass.STOCK,
        primary_provider=ProviderName.ALPACA,
        secondary_provider=ProviderName.TIINGO,
        provider_symbols={
            ProviderName.ALPACA.value: db_symbol,
            ProviderName.TIINGO.value: db_symbol,
            ProviderName.FINNHUB.value: db_symbol,
        },
        trading_sessions={"sessions": ["us_equity_rth"]},
        pip_size="0.01",
        price_tick_size="0.01",
        quantity_step="1",
        min_quantity="1",
    )


def _crypto(db_symbol: str, canonical: str, tiingo_ticker: str) -> AssetDefinition:
    coinbase_product = canonical.replace("/", "-")
    return AssetDefinition(
        canonical_symbol=canonical,
        db_symbol=db_symbol,
        display_symbol=canonical,
        asset_class=AssetClass.CRYPTO,
        primary_provider=ProviderName.COINBASE,
        secondary_provider=ProviderName.ALPACA,
        provider_symbols={
            ProviderName.COINBASE.value: coinbase_product,
            ProviderName.ALPACA.value: canonical,
            ProviderName.TIINGO.value: tiingo_ticker,
            ProviderName.FINNHUB.value: f"COINBASE:{coinbase_product}",
        },
        trading_sessions={"sessions": ["24x7"]},
        pip_size="0.01",
        price_tick_size="0.01",
        quantity_step="0.0001",
        min_quantity="0.0001",
    )


TARGET_ASSETS: tuple[AssetDefinition, ...] = (
    _crypto("BTCUSD", "BTC/USD", "btcusd"),
    _crypto("ETHUSD", "ETH/USD", "ethusd"),
    AssetDefinition(
        canonical_symbol="XAU/USD",
        db_symbol="XAUUSD",
        display_symbol="XAU/USD",
        asset_class=AssetClass.COMMODITY,
        primary_provider=ProviderName.TWELVE_DATA,
        secondary_provider=ProviderName.TIINGO,
        provider_symbols={
            ProviderName.TWELVE_DATA.value: "XAU/USD",
            ProviderName.TIINGO.value: "xauusd",
            ProviderName.FINNHUB.value: "OANDA:XAU_USD",
        },
        trading_sessions={"sessions": ["24x5"]},
        pip_size="0.01",
        price_tick_size="0.01",
        quantity_step="0.01",
        min_quantity="0.01",
    ),
    AssetDefinition(
        canonical_symbol="GBP/JPY",
        db_symbol="GBPJPY",
        display_symbol="GBP/JPY",
        asset_class=AssetClass.FOREX,
        primary_provider=ProviderName.TWELVE_DATA,
        secondary_provider=ProviderName.TIINGO,
        provider_symbols={
            ProviderName.TWELVE_DATA.value: "GBP/JPY",
            ProviderName.TIINGO.value: "gbpjpy",
            ProviderName.FINNHUB.value: "OANDA:GBP_JPY",
        },
        trading_sessions={"sessions": ["24x5"]},
        pip_size="0.01",
        price_tick_size="0.001",
        quantity_step="1000",
        min_quantity="1000",
    ),
    _equity("NVDA"),
    _equity("TSLA"),
    _equity("AMD"),
    _equity("COIN"),
)

assert tuple(a.db_symbol for a in TARGET_ASSETS) == ACTIVE_DB_SYMBOLS, (
    "registry TARGET_ASSETS must match active_universe.ACTIVE_DB_SYMBOLS"
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
