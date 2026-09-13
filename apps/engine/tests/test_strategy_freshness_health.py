"""Session-aware strategy freshness health tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from quantara_engine.market_data.registry import get_asset
from quantara_engine.market_data.strategy_freshness_health import (
    classify_strategy_candle_health,
    is_strategy_candle_eligible,
)
from quantara_workers.jobs.run_strategy import strategy_freshness_summary

INST_ID = "00000000-0000-4000-8000-000000000001"


def test_sunday_equity_classified_session_closed_not_stale():
    now = datetime(2026, 9, 13, 12, 0, tzinfo=timezone.utc)  # Sunday
    asset = get_asset("NVDA")
    assert asset is not None
    last = now - timedelta(hours=30)
    row = classify_strategy_candle_health(asset, last, now)
    assert row["classification"] == "session_closed"
    assert row["session_open"] is False


def test_sunday_crypto_still_evaluated_for_freshness():
    now = datetime(2026, 9, 13, 12, 0, tzinfo=timezone.utc)
    asset = get_asset("BTCUSD")
    assert asset is not None
    last = now - timedelta(minutes=6)
    row = classify_strategy_candle_health(asset, last, now)
    assert row["session_open"] is True
    assert row["classification"] in ("healthy", "latency_ok")


def test_crypto_6m_wall_age_can_still_be_fresh_by_bar_close():
    now = datetime(2026, 9, 13, 12, 10, tzinfo=timezone.utc)
    asset = get_asset("ETHUSD")
    assert asset is not None
    last = now - timedelta(minutes=6)
    eligible, reason = is_strategy_candle_eligible(asset, last, now)
    assert eligible is True
    assert reason == "eligible"


@patch("quantara_engine.persistence.batch_summary.batch_latest_candle_timestamps")
def test_sunday_summary_not_strategy_error_with_historical_only_failure(mock_batch_ts):
    now = datetime(2026, 9, 13, 12, 0, tzinfo=timezone.utc)
    store = MagicMock()
    store.get_settings_dict.return_value = {
        "worker_status:strategy_runner": {
            "status": "catching_up",
            "mode": "live",
            "historical_error": "bp.symbol missing",
            "last_evaluation_at": (now - timedelta(minutes=2)).isoformat(),
            "jobs_pending": 0,
            "timeframes": {
                "5m": {"backlog": 0},
                "15m": {"backlog": 0},
                "1h": {"backlog": 0},
                "orb_5m": {"backlog": 0},
            },
        },
        "worker_status:data_fetcher": {"status": "healthy"},
    }

    def _inst(sym):
        return MagicMock(id=f"id-{sym}")

    store.get_instrument_by_symbol.side_effect = _inst
    mock_batch_ts.return_value = {
        f"id-BTCUSD": now - timedelta(minutes=5),
        f"id-ETHUSD": now - timedelta(minutes=5),
        f"id-NVDA": now - timedelta(hours=30),
        f"id-TSLA": now - timedelta(hours=30),
        f"id-AMD": now - timedelta(hours=30),
        f"id-COIN": now - timedelta(hours=30),
        f"id-XAUUSD": now - timedelta(hours=14),
        f"id-GBPJPY": now - timedelta(hours=14),
    }

    summary = strategy_freshness_summary(store, now)
    assert summary["healthy"] is True
    assert summary["display_status"] in ("healthy", "catching_up")
    assert summary["market_candle_health"]["NVDA"]["classification"] == "session_closed"
    assert summary["error"] is None


@patch("quantara_engine.persistence.batch_summary.batch_latest_candle_timestamps")
def test_live_strategy_error_surfaces_red_status(mock_batch_ts):
    now = datetime(2026, 9, 13, 12, 0, tzinfo=timezone.utc)
    store = MagicMock()
    store.get_settings_dict.return_value = {
        "worker_status:strategy_runner": {
            "status": "error",
            "mode": "live",
            "live_error": "boom",
            "error": "boom",
            "last_evaluation_at": (now - timedelta(minutes=30)).isoformat(),
            "jobs_pending": 0,
        },
        "worker_status:data_fetcher": {"status": "healthy"},
    }
    store.get_instrument_by_symbol.return_value = MagicMock(id=INST_ID)
    mock_batch_ts.return_value = {INST_ID: now - timedelta(minutes=5)}

    summary = strategy_freshness_summary(store, now)
    assert summary["display_status"] == "error"
    assert summary["error"] == "boom"
    assert summary["healthy"] is False
