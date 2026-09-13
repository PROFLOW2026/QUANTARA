"""Visible canonical mark cadence — API path and continuous crypto marks."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from quantara_engine.api.display_price import resolve_asset_display_price
from quantara_engine.execution.crypto_fast_protection import run_crypto_fast_protection
from quantara_engine.execution.crypto_mark_valuation import (
    FAST_CANONICAL_MARKS_KEY,
    apply_fast_1m_marks,
    display_price_from_canonical_mark,
)
from quantara_engine.models.enums import WorkerRunStatus, coerce_worker_run_status

TZ = timezone.utc


def test_coerce_worker_run_status_skipped():
    assert coerce_worker_run_status("skipped") == WorkerRunStatus.SUCCESS


def test_resolve_asset_display_price_prefers_canonical():
    store = MagicMock()
    ts = datetime(2026, 9, 13, 3, 32, tzinfo=TZ)
    with patch(
        "quantara_engine.api.display_price.display_price_from_canonical_mark",
        return_value=(Decimal("2526.10"), ts),
    ):
        price, at = resolve_asset_display_price(
            store,
            "ETHUSD",
            fallback_price=2500.0,
            fallback_candle=datetime(2026, 9, 13, 3, 30, tzinfo=TZ),
        )
    assert price == 2526.10
    assert at == ts


def test_resolve_asset_display_price_falls_back_to_5m():
    store = MagicMock()
    fallback_at = datetime(2026, 9, 13, 3, 30, tzinfo=TZ)
    with patch(
        "quantara_engine.api.display_price.display_price_from_canonical_mark",
        return_value=None,
    ):
        price, at = resolve_asset_display_price(
            store,
            "ETHUSD",
            fallback_price=2500.0,
            fallback_candle=fallback_at,
        )
    assert price == 2500.0
    assert at == fallback_at


def test_apply_fast_mark_blocks_older_5m_overwrite():
    store = MagicMock()
    store.get_settings_dict.return_value = {
        FAST_CANONICAL_MARKS_KEY: {
            "ETHUSD": {
                "price": "2526.00",
                "at": datetime(2026, 9, 13, 3, 32, tzinfo=TZ).isoformat(),
            }
        }
    }
    store.get_instrument_by_symbol.return_value = MagicMock(id="eth-id")
    with patch(
        "quantara_engine.execution.crypto_mark_valuation._update_research_position_marks",
        return_value=0,
    ), patch(
        "quantara_engine.execution.crypto_mark_valuation._update_live_sim_position_marks",
        return_value=0,
    ):
        apply_fast_1m_marks(
            store,
            {"ETHUSD": (Decimal("2520.00"), datetime(2026, 9, 13, 3, 30, tzinfo=TZ))},
            flush=False,
        )
    stored = store.update_settings.call_args_list[-1][0][1]
    assert stored["ETHUSD"]["price"] == "2526.00"


def test_crypto_fetches_btc_eth_without_open_positions():
    store = MagicMock()
    store.get_settings_dict.return_value = {"paper_trading_enabled": True}

    with patch(
        "quantara_engine.trading.trading_controls.load_trading_control",
        return_value=MagicMock(),
    ), patch(
        "quantara_engine.trading.trading_controls.allows_position_management",
        return_value=True,
    ), patch(
        "quantara_engine.execution.crypto_fast_protection._collect_research_crypto_work",
        return_value=[],
    ), patch(
        "quantara_engine.execution.crypto_fast_protection._collect_live_sim_crypto_rows",
        return_value=[],
    ), patch(
        "quantara_engine.execution.crypto_fast_protection._fetch_and_store_1m",
        return_value=[],
    ) as fetch_mock, patch(
        "quantara_engine.execution.crypto_mark_valuation.apply_crypto_1m_marks",
        return_value={"applied_symbols": []},
    ), patch(
        "quantara_engine.execution.crypto_mark_valuation.prune_crypto_canonical_marks",
    ):
        store.get_instrument_by_symbol.return_value = MagicMock(id="eth")
        store.latest_candle_timestamp.return_value = None
        store.list_candles.return_value = []
        report = run_crypto_fast_protection(store, datetime.now(TZ))

    assert report["status"] == "success"
    assert fetch_mock.call_count == 2


def test_display_price_from_canonical_without_open_position():
    store = MagicMock()
    store.get_settings_dict.return_value = {
        FAST_CANONICAL_MARKS_KEY: {
            "BTCUSD": {
                "price": "60000",
                "at": datetime(2026, 9, 13, 3, 31, tzinfo=TZ).isoformat(),
            }
        }
    }
    canon = display_price_from_canonical_mark(store, "BTCUSD")
    assert canon is not None
    assert canon[0] == Decimal("60000")
