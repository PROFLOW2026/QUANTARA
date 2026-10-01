"""Canonical owner-approved active Paper test universe — single source of truth."""

from __future__ import annotations

# Active polling / strategy / fetch targets (25). Phase 2 adds equities/ETFs/FX/crypto.
ACTIVE_DB_SYMBOLS: tuple[str, ...] = (
    "BTCUSD",
    "ETHUSD",
    "XAUUSD",
    "GBPJPY",
    "NVDA",
    "TSLA",
    "AMD",
    "COIN",
    "AAPL",
    "MSFT",
    "META",
    "AMZN",
    "GOOGL",
    "SPY",
    "QQQ",
    "EURUSD",
    "USDJPY",
    "SOLUSD",
    "PLTR",
    "MSTR",
    "IWM",
    "DIA",
    "GBPUSD",
    "AUDUSD",
    "XRPUSD",
)

# Reserved for staged promotion before active (empty while Phase 1 symbols are active).
RESEARCH_EXTENDED_DB_SYMBOLS: tuple[str, ...] = ()

# Removed from active polling/evaluation; historical DB rows remain queryable.
REMOVED_DB_SYMBOLS: tuple[str, ...] = ()

PHASE1_EXPANDED_DB_SYMBOLS: tuple[str, ...] = (
    "AAPL",
    "MSFT",
    "META",
    "AMZN",
    "GOOGL",
    "SPY",
    "QQQ",
    "EURUSD",
    "USDJPY",
    "SOLUSD",
)

PHASE2_EXPANDED_DB_SYMBOLS: tuple[str, ...] = (
    "PLTR",
    "MSTR",
    "IWM",
    "DIA",
    "GBPUSD",
    "AUDUSD",
    "XRPUSD",
)

# ORB session type per active asset — explicit canonical mapping.
ORB_SESSION_BY_SYMBOL: dict[str, str] = {
    "NVDA": "us_equity_rth",
    "TSLA": "us_equity_rth",
    "AMD": "us_equity_rth",
    "COIN": "us_equity_rth",
    "AAPL": "us_equity_rth",
    "MSFT": "us_equity_rth",
    "META": "us_equity_rth",
    "AMZN": "us_equity_rth",
    "GOOGL": "us_equity_rth",
    "SPY": "us_equity_rth",
    "QQQ": "us_equity_rth",
    "BTCUSD": "crypto_utc_daily",
    "ETHUSD": "crypto_utc_daily",
    "SOLUSD": "crypto_utc_daily",
    "XAUUSD": "fx_utc_daily",
    "GBPJPY": "fx_utc_daily",
    "EURUSD": "fx_utc_daily",
    "USDJPY": "fx_utc_daily",
    "PLTR": "us_equity_rth",
    "MSTR": "us_equity_rth",
    "IWM": "us_equity_rth",
    "DIA": "us_equity_rth",
    "GBPUSD": "fx_utc_daily",
    "AUDUSD": "fx_utc_daily",
    "XRPUSD": "crypto_utc_daily",
}


def list_active_db_symbols() -> tuple[str, ...]:
    return ACTIVE_DB_SYMBOLS


def list_research_db_symbols() -> tuple[str, ...]:
    """Active universe + optional research-only extensions (deduped)."""
    seen: dict[str, None] = {}
    for sym in (*ACTIVE_DB_SYMBOLS, *RESEARCH_EXTENDED_DB_SYMBOLS):
        seen.setdefault(sym, None)
    return tuple(seen.keys())


def is_active_symbol(db_symbol: str) -> bool:
    return db_symbol.upper() in ACTIVE_DB_SYMBOLS


def orb_session_for(db_symbol: str) -> str | None:
    return ORB_SESSION_BY_SYMBOL.get(db_symbol.upper())
