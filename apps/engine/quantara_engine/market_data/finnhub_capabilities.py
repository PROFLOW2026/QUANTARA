"""Verified Finnhub capability profile — updated by live probes, not guessed."""

from __future__ import annotations

from typing import Any

SETTINGS_KEY = "finnhub_capabilities"

# Verified on free tier (audit_finnhub_live.json, 2026-09-08) when key present.
# Crypto/candles blocked on that account; stocks quote real-time/delayed OK.
VERIFIED_RATE_LIMIT_PER_MINUTE = 60
INTERNAL_MINUTE_LIMIT = 50
INTERNAL_HOURLY_LIMIT = 150
VALIDATION_INTERVAL_MINUTES = 5

DEFAULT_PROFILE: dict[str, Any] = {
    "verified_rate_limit_per_minute": VERIFIED_RATE_LIMIT_PER_MINUTE,
    "internal_minute_limit": INTERNAL_MINUTE_LIMIT,
    "internal_hourly_limit": INTERNAL_HOURLY_LIMIT,
    "stock_quote": True,
    "stock_candle_1m": False,
    "crypto_quote": False,
    "crypto_candle_1m": False,
    "forex_quote": None,
    "forex_candle_1m": False,
    "xau_quote": None,
    "websocket_available": True,
    "probed_at": None,
    "per_asset": {},
}


def load_capabilities(settings_dict: dict[str, Any] | None = None) -> dict[str, Any]:
    raw = (settings_dict or {}).get(SETTINGS_KEY)
    if not isinstance(raw, dict):
        return dict(DEFAULT_PROFILE)
    merged = dict(DEFAULT_PROFILE)
    merged.update(raw)
    per_asset = raw.get("per_asset")
    merged["per_asset"] = dict(per_asset) if isinstance(per_asset, dict) else {}
    return merged


def asset_quote_capable(capabilities: dict[str, Any], db_symbol: str) -> bool:
    per = capabilities.get("per_asset") or {}
    entry = per.get(db_symbol.upper())
    if isinstance(entry, dict) and "quote" in entry:
        return bool(entry.get("quote"))
    sym = db_symbol.upper()
    if sym in {"NVDA", "TSLA", "AMD", "COIN"}:
        return bool(capabilities.get("stock_quote"))
    if sym in {"BTCUSD", "ETHUSD"}:
        return bool(capabilities.get("crypto_quote"))
    if sym == "GBPJPY":
        fq = capabilities.get("forex_quote")
        return fq is True if fq is not None else False
    if sym == "XAUUSD":
        xq = capabilities.get("xau_quote")
        if xq is not None:
            return bool(xq)
        fq = capabilities.get("forex_quote")
        return fq is True if fq is not None else False
    return False


def asset_candle_1m_capable(capabilities: dict[str, Any], db_symbol: str) -> bool:
    per = capabilities.get("per_asset") or {}
    entry = per.get(db_symbol.upper())
    if isinstance(entry, dict) and "candle_1m" in entry:
        return bool(entry.get("candle_1m"))
    sym = db_symbol.upper()
    if sym in {"NVDA", "TSLA", "AMD", "COIN"}:
        return bool(capabilities.get("stock_candle_1m"))
    if sym in {"BTCUSD", "ETHUSD"}:
        return bool(capabilities.get("crypto_candle_1m"))
    if sym in {"GBPJPY", "XAUUSD"}:
        return bool(capabilities.get("forex_candle_1m"))
    return False


def expected_validation_calls_per_hour(asset_count: int = 8) -> int:
    cycles = 60 // VALIDATION_INTERVAL_MINUTES
    return asset_count * cycles


def expected_validation_calls_per_day(asset_count: int = 8) -> int:
    return expected_validation_calls_per_hour(asset_count) * 24
