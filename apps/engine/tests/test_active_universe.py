"""Canonical active universe tests."""

from __future__ import annotations

from quantara_engine.market_data.active_universe import (
    ACTIVE_DB_SYMBOLS,
    REMOVED_DB_SYMBOLS,
    list_active_db_symbols,
    orb_session_for,
)
from quantara_engine.market_data.registry import list_target_assets


def test_active_universe_exactly_eight():
    assert list_active_db_symbols() == (
        "BTCUSD",
        "ETHUSD",
        "XAUUSD",
        "GBPJPY",
        "NVDA",
        "TSLA",
        "AMD",
        "COIN",
    )
    assert tuple(a.db_symbol for a in list_target_assets()) == ACTIVE_DB_SYMBOLS


def test_removed_symbols_not_in_target_assets():
    active = {a.db_symbol for a in list_target_assets()}
    for sym in REMOVED_DB_SYMBOLS:
        assert sym not in active


def test_orb_session_mapping():
    assert orb_session_for("NVDA") == "us_equity_rth"
    assert orb_session_for("BTCUSD") == "crypto_utc_daily"
    assert orb_session_for("XAUUSD") == "fx_utc_daily"


def test_provider_routing_by_asset_class():
    assets = {a.db_symbol: a for a in list_target_assets()}
    assert assets["BTCUSD"].primary_provider.value == "alpaca"
    assert assets["ETHUSD"].primary_provider.value == "alpaca"
    assert assets["XAUUSD"].primary_provider.value == "twelvedata"
    assert assets["GBPJPY"].primary_provider.value == "twelvedata"
    assert assets["NVDA"].secondary_provider.value == "alpaca"
    assert assets["TSLA"].secondary_provider.value == "alpaca"
