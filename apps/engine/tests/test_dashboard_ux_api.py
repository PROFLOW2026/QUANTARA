"""Dashboard UX presentation-layer API tests."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

from quantara_workers.jobs.run_strategy import strategy_freshness_summary

INST_ID = "00000000-0000-4000-8000-000000000001"


@patch("quantara_engine.persistence.batch_summary.batch_latest_candle_timestamps")
def test_strategy_freshness_splits_live_and_historical_backlog(mock_batch_ts):
    now = datetime(2026, 9, 9, 14, 0, tzinfo=timezone.utc)
    store = MagicMock()
    store.get_settings_dict.return_value = {
        "worker_status:strategy_runner": {
            "status": "catching_up",
            "last_evaluation_at": (now - timedelta(minutes=1)).isoformat(),
            "jobs_pending": 737,
            "timeframes": {
                "5m": {"backlog": 0, "status": "healthy"},
                "15m": {"backlog": 604, "status": "catching_up"},
                "1h": {"backlog": 133, "status": "catching_up"},
                "orb_5m": {"backlog": 0, "status": "healthy"},
            },
        },
        "worker_status:data_fetcher": {"status": "healthy"},
    }
    store.get_instrument_by_symbol.return_value = MagicMock(id=INST_ID)
    mock_batch_ts.return_value = {INST_ID: now - timedelta(minutes=5)}

    summary = strategy_freshness_summary(store, now)

    assert summary["live_backlog"] == 0
    assert summary["historical_backlog"] == 737
    assert summary["backlog"] == 737
    assert summary["healthy"] is True


@patch("quantara_engine.persistence.batch_summary.batch_latest_candle_timestamps")
def test_decisions_by_asset_exposes_entry_signal_and_position_open(mock_batch_ts):
    from quantara_engine.api.routes import decisions_by_asset

    store = MagicMock()
    store.resolve_instrument_display_symbols.return_value = {"inst-btc": "BTC/USD"}
    store.build_instance_strategy_identity_map.return_value = {
        "ia1": {
            "robot_label": "Robot A",
            "strategy_slug": "gold-trend-pullback",
            "strategy_name": "Trend Pullback",
        },
    }

    decision = MagicMock()
    decision.id = "d-signal"
    decision.candle_timestamp = datetime(2026, 9, 9, 14, 0, tzinfo=timezone.utc)
    decision.decision_type.value = "buy_signal"
    decision.message = "Pullback to EMA20 in uptrend"
    decision.instrument_id = "inst-btc"
    decision.strategy_instance_id = "ia1"
    decision.metadata = {}

    open_position = MagicMock()
    open_position.instrument_id = "inst-btc"

    store.list_latest_decisions_by_asset_timeframe.return_value = [decision]
    store.list_competition_positions.return_value = [open_position]
    store.get_settings_dict.return_value = {
        "worker_status:strategy_runner": {"status": "healthy", "jobs_pending": 0},
        "worker_status:data_fetcher": {"status": "healthy"},
    }
    store.get_instrument_by_symbol.return_value = MagicMock(id=INST_ID)
    mock_batch_ts.return_value = {
        INST_ID: datetime(2026, 9, 9, 13, 55, tzinfo=timezone.utc)
    }

    payload = decisions_by_asset(store, timeframe="5m")
    row = payload["decisions"][0]

    assert row["entry_signal"] is True
    assert row["trade_opened"] is True
    assert row["position_open"] is True
    assert row["robot_label"] == "Robot A"


@patch("quantara_engine.persistence.batch_summary.batch_latest_candle_timestamps")
def test_decisions_by_asset_position_open_false_without_positions(mock_batch_ts):
    from quantara_engine.api.routes import decisions_by_asset

    store = MagicMock()
    store.resolve_instrument_display_symbols.return_value = {"inst-spy": "SPY"}
    store.build_instance_strategy_identity_map.return_value = {
        "ib1": {
            "robot_label": "Robot B",
            "strategy_slug": "opening-range-breakout",
            "strategy_name": "Opening Range Breakout",
        },
    }

    decision = MagicMock()
    decision.id = "d-orb"
    decision.candle_timestamp = datetime(2026, 9, 9, 14, 0, tzinfo=timezone.utc)
    decision.decision_type.value = "buy_signal"
    decision.message = "breakout_long_confirmed"
    decision.instrument_id = "inst-spy"
    decision.strategy_instance_id = "ib1"
    decision.metadata = {}

    store.list_latest_decisions_by_asset_timeframe.return_value = [decision]
    store.list_competition_positions.return_value = []
    store.get_settings_dict.return_value = {
        "worker_status:strategy_runner": {"status": "healthy", "jobs_pending": 0},
        "worker_status:data_fetcher": {"status": "healthy"},
    }
    store.get_instrument_by_symbol.return_value = MagicMock(id=INST_ID)
    mock_batch_ts.return_value = {
        INST_ID: datetime(2026, 9, 9, 13, 55, tzinfo=timezone.utc)
    }

    payload = decisions_by_asset(store, timeframe="5m")
    row = payload["decisions"][0]

    assert row["entry_signal"] is True
    assert row["position_open"] is False
    assert row["robot_label"] == "Robot B"
    assert row["message"] == "breakout_long_confirmed"


def test_hebrew_i18n_has_orb_reason_and_deferred_labels():
    repo_root = Path(__file__).resolve().parents[3]
    he_path = repo_root / "apps" / "web" / "messages" / "he.json"
    payload = json.loads(he_path.read_text(encoding="utf-8"))

    signals = payload["signals"]
    assert signals["orb_waiting_for_breakout"] == "ממתינים לפריצה"
    assert signals["orb_breakout_long_confirmed"] == "פריצה כלפי מעלה אושרה"

    home = payload["home"]
    assert home["asset_status_deferred"] == "ממתין לעדכון הבא"
    assert home["entry_signal"] == "איתות כניסה"
    assert home["strategy_historical_backlog"] == "תור עיבוד היסטורי"
    assert home["strategy_live_backlog"] == "תור מסחר חי"


def test_dashboard_page_no_gold_snapshot_card():
    repo_root = Path(__file__).resolve().parents[3]
    page_path = repo_root / "apps" / "web" / "app" / "(dashboard)" / "page.tsx"
    source = page_path.read_text(encoding="utf-8")

    assert "gold_snapshot" not in source
    assert "getCandlesLatest" not in source
    assert "PriceDisplay" not in source
