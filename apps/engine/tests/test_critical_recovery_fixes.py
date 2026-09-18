"""Targeted tests for critical recovery fixes."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from quantara_engine.market_data.provider_budgets import record_provider_error
from quantara_engine.market_data.registry import get_asset
from quantara_engine.market_data.strategy_freshness_health import (
    aggregate_market_health,
    classify_strategy_candle_health,
)


def test_record_provider_error_import_path():
    store = MagicMock()
    record_provider_error(store, "coinbase", "HTTP 503: unavailable", symbol="BTC-USD")
    store.update_settings.assert_called()


def test_crypto_health_stale_when_1m_authority_stale():
    asset = get_asset("BTCUSD")
    now = datetime(2026, 9, 19, 12, 0, tzinfo=timezone.utc)
    last_5m = now - timedelta(minutes=6)
    last_1m = now - timedelta(hours=50)
    row = classify_strategy_candle_health(asset, last_5m, now, last_1m=last_1m)
    assert row["classification"] == "stale"
    assert row["protection_1m_stale"] is True
    summary = aggregate_market_health({"BTCUSD": row})
    assert "BTCUSD" in summary["stale_open_assets"]


def test_crypto_protection_import_loads():
    from quantara_engine.market_data.adapters.coinbase import CoinbaseMarketDataProvider
    from quantara_engine.market_data.provider_budgets import record_provider_error

    assert callable(record_provider_error)
    assert CoinbaseMarketDataProvider is not None


def test_missed_exit_recovery_module_imports():
    from quantara_engine.live_sim.missed_exit_recovery import recover_missed_live_sim_exits

    assert callable(recover_missed_live_sim_exits)
