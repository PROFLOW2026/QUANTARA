"""Canonical active universe tests."""

from __future__ import annotations

from quantara_engine.market_data.active_universe import (
    ACTIVE_DB_SYMBOLS,
    PHASE1_EXPANDED_DB_SYMBOLS,
    REMOVED_DB_SYMBOLS,
    list_active_db_symbols,
    orb_session_for,
)
from quantara_engine.market_data.registry import list_target_assets


def test_active_universe_eighteen():
    assert len(list_active_db_symbols()) == 25
    assert list_active_db_symbols() == ACTIVE_DB_SYMBOLS
    assert tuple(a.db_symbol for a in list_target_assets()) == ACTIVE_DB_SYMBOLS
    assert set(PHASE1_EXPANDED_DB_SYMBOLS).issubset(set(ACTIVE_DB_SYMBOLS))


def test_removed_symbols_not_in_target_assets():
    active = {a.db_symbol for a in list_target_assets()}
    for sym in REMOVED_DB_SYMBOLS:
        assert sym not in active


def test_orb_session_mapping():
    assert orb_session_for("NVDA") == "us_equity_rth"
    assert orb_session_for("BTCUSD") == "crypto_utc_daily"
    assert orb_session_for("SOLUSD") == "crypto_utc_daily"
    assert orb_session_for("XAUUSD") == "fx_utc_daily"
    assert orb_session_for("EURUSD") == "fx_utc_daily"


def test_provider_routing_by_asset_class():
    assets = {a.db_symbol: a for a in list_target_assets()}
    assert assets["BTCUSD"].primary_provider.value == "coinbase"
    assert assets["ETHUSD"].primary_provider.value == "coinbase"
    assert assets["SOLUSD"].primary_provider.value == "coinbase"
    assert assets["XAUUSD"].primary_provider.value == "tiingo"
    assert assets["GBPJPY"].primary_provider.value == "tiingo"
    assert assets["EURUSD"].primary_provider.value == "tiingo"
    assert assets["NVDA"].primary_provider.value == "alpaca"
    assert assets["NVDA"].secondary_provider.value == "tiingo"
    assert assets["AAPL"].primary_provider.value == "alpaca"
    assert assets["SPY"].primary_provider.value == "alpaca"
    assert assets["BTCUSD"].secondary_provider.value == "alpaca"
