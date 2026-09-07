"""Canonical instrument symbols vs provider-specific symbols."""

from __future__ import annotations

# QUANTARA canonical display symbol
CANONICAL_XAUUSD = "XAU/USD"

# Twelve Data uses the same pair format for spot gold
TWELVEDATA_XAUUSD = "XAU/USD"

SYMBOL_ALIASES = {
    "XAUUSD": CANONICAL_XAUUSD,
    "XAU/USD": CANONICAL_XAUUSD,
}


def normalize_canonical_symbol(symbol: str) -> str:
    key = symbol.upper().replace(" ", "")
    return SYMBOL_ALIASES.get(key, symbol.upper())
