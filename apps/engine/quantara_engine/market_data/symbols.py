"""Canonical instrument symbols vs provider-specific symbols."""

from __future__ import annotations

from quantara_engine.market_data.registry import TARGET_ASSETS, get_asset, get_asset_by_canonical

CANONICAL_XAUUSD = "XAU/USD"
TWELVEDATA_XAUUSD = "XAU/USD"

SYMBOL_ALIASES = {
    "XAUUSD": CANONICAL_XAUUSD,
    "XAU/USD": CANONICAL_XAUUSD,
    "BTCUSD": "BTC/USD",
    "BTC/USD": "BTC/USD",
    "ETHUSD": "ETH/USD",
    "ETH/USD": "ETH/USD",
    "GBPJPY": "GBP/JPY",
    "GBP/JPY": "GBP/JPY",
}


def normalize_canonical_symbol(symbol: str) -> str:
    key = symbol.upper().replace(" ", "")
    if key in SYMBOL_ALIASES:
        return SYMBOL_ALIASES[key]
    asset = get_asset(key) or get_asset_by_canonical(symbol)
    if asset:
        return asset.display_symbol
    return symbol.upper()


def normalize_db_symbol(symbol: str) -> str:
    asset = get_asset(symbol) or get_asset_by_canonical(symbol)
    if asset:
        return asset.db_symbol
    key = symbol.upper().replace("/", "").replace(" ", "")
    return SYMBOL_ALIASES.get(key, key).replace("/", "")


def list_target_db_symbols() -> list[str]:
    return [asset.db_symbol for asset in TARGET_ASSETS]
