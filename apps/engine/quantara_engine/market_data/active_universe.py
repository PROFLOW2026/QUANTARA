"""Canonical owner-approved active Paper test universe — single source of truth."""

from __future__ import annotations

# Exactly 8 active Paper targets. All workers, API, UI, and runners derive from here.
ACTIVE_DB_SYMBOLS: tuple[str, ...] = (
    "BTCUSD",
    "ETHUSD",
    "XAUUSD",
    "GBPJPY",
    "NVDA",
    "TSLA",
    "AMD",
    "COIN",
)

# Removed from active polling/evaluation; historical DB rows remain queryable.
REMOVED_DB_SYMBOLS: tuple[str, ...] = (
    "EURUSD",
    "SPY",
    "QQQ",
    "AAPL",
    "MSFT",
)

# ORB session type per active asset — explicit canonical mapping.
ORB_SESSION_BY_SYMBOL: dict[str, str] = {
    "NVDA": "us_equity_rth",
    "TSLA": "us_equity_rth",
    "AMD": "us_equity_rth",
    "COIN": "us_equity_rth",
    "BTCUSD": "crypto_utc_daily",
    "ETHUSD": "crypto_utc_daily",
    "XAUUSD": "fx_utc_daily",
    "GBPJPY": "fx_utc_daily",
}


def list_active_db_symbols() -> tuple[str, ...]:
    return ACTIVE_DB_SYMBOLS


def is_active_symbol(db_symbol: str) -> bool:
    return db_symbol.upper() in ACTIVE_DB_SYMBOLS


def orb_session_for(db_symbol: str) -> str | None:
    return ORB_SESSION_BY_SYMBOL.get(db_symbol.upper())
