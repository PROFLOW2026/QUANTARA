"""Analytics assets aggregates Robot A + Robot B financials."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch

from quantara_engine.api.routes import analytics_assets


@patch("quantara_engine.persistence.batch_summary.batch_latest_candle_closes")
@patch("quantara_engine.persistence.batch_summary.batch_latest_candle_timestamps")
@patch("quantara_engine.persistence.batch_summary.batch_candle_counts")
def test_analytics_assets_combines_robot_a_and_b(
    mock_candle_counts,
    mock_last_ts,
    mock_closes,
):
    store = MagicMock()
    store.list_all_competition_entries.return_value = (
        [{"portfolio": MagicMock(id="pa")}],
        [{"portfolio": MagicMock(id="pb")}],
        [{"portfolio": MagicMock(id="pa")}, {"portfolio": MagicMock(id="pb")}],
    )

    store.batch_asset_trading_metrics.return_value = {
        "inst-nvda": {
            "open_positions": 1,
            "closed_trades": 1,
            "realized_pnl": 50.0,
            "unrealized_pnl": 100.0,
            "total_pnl": 150.0,
        }
    }
    from decimal import Decimal

    from quantara_engine.persistence.batch_summary import CompetitionExposureRiskSummary

    store.batch_competition_exposure_risk_summary.return_value = (
        CompetitionExposureRiskSummary(
            total_open_exposure=Decimal("0"),
            total_open_risk_usd=Decimal("0"),
            total_equity=Decimal("320000"),
            open_risk_pct=0.0,
        ),
        {},
    )

    store.get_instrument_by_symbol.side_effect = lambda sym: MagicMock(id=f"inst-{sym.lower()}")
    mock_candle_counts.return_value = {"inst-nvda": {"5m": 200, "15m": 200, "1h": 200}}
    mock_last_ts.return_value = {
        "inst-nvda": datetime(2026, 9, 9, 14, 0, tzinfo=timezone.utc),
    }
    mock_closes.return_value = {"inst-nvda": Decimal("500")}
    store.get_settings_dict.return_value = {
        "worker_status:data_fetcher": {
            "assets": {
                "NVDA": {"status": "healthy", "provider": "alpaca"},
                "TSLA": {"status": "healthy", "provider": "alpaca"},
            }
        }
    }

    payload = analytics_assets(store)
    by_symbol = {row["db_symbol"]: row for row in payload["assets"]}

    assert by_symbol["NVDA"]["open_positions"] == 1
    assert by_symbol["NVDA"]["unrealized_pnl"] == 100.0
    assert by_symbol["NVDA"]["closed_trades"] == 1
    assert by_symbol["NVDA"]["realized_pnl"] == 50.0
    assert by_symbol["NVDA"]["provider"] == "alpaca"
    store.batch_asset_trading_metrics.assert_called_once()
