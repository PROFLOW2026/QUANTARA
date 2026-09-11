"""Batched competition exposure / open-risk dashboard aggregation."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch
from uuid import UUID

import pytest

from quantara_engine.api.routes import analytics_assets
from quantara_engine.persistence.batch_summary import (
    AssetExposureRiskMetrics,
    CompetitionExposureRiskSummary,
    batch_competition_exposure_risk_summary,
    batch_entry_actual_risk_by_position,
)


def _uuid(n: int) -> str:
    return f"00000000-0000-4000-8000-{n:012d}"


class TestBatchEntryActualRisk:
    def test_empty_positions_returns_empty(self):
        store = MagicMock()
        assert batch_entry_actual_risk_by_position(store, []) == {}


class TestBatchCompetitionExposureRiskSummary:
    def test_no_open_positions_returns_zero_exposure_and_risk(self):
        store = MagicMock()
        store.session.execute.return_value.all.return_value = []
        store.session.scalar.return_value = Decimal("320000")

        with patch(
            "quantara_engine.persistence.batch_summary.batch_portfolio_equity",
            return_value={_uuid(1): Decimal("2000")},
        ):
            summary, by_inst = batch_competition_exposure_risk_summary(
                store,
                [_uuid(1)],
                symbol_by_instrument_id={_uuid(100): "BTCUSD"},
            )

        assert summary.total_open_exposure == Decimal("0")
        assert summary.total_open_risk_usd == Decimal("0")
        assert summary.open_risk_pct == 0.0
        assert by_inst == {}

    def test_aggregates_exposure_risk_and_asset_pct(self):
        store = MagicMock()
        pos_id = _uuid(501)
        inst_id = _uuid(100)
        store.session.execute.return_value.all.return_value = [
            (UUID(pos_id), UUID(inst_id), Decimal("0.5"), Decimal("70000")),
        ]

        with patch(
            "quantara_engine.persistence.batch_summary.batch_portfolio_equity",
            return_value={
                _uuid(1): Decimal("2000"),
                _uuid(2): Decimal("2000"),
            },
        ), patch(
            "quantara_engine.persistence.batch_summary.batch_entry_actual_risk_by_position",
            return_value={pos_id: Decimal("14.50")},
        ), patch(
            "quantara_engine.competition.asset_equity.portfolio_ids_for_symbol",
            return_value=[_uuid(1), _uuid(2)],
        ), patch(
            "quantara_engine.competition.asset_equity.nominal_asset_allocated_equity",
            return_value=Decimal("4000"),
        ), patch(
            "quantara_engine.market_data.active_universe.ACTIVE_DB_SYMBOLS",
            ("BTCUSD",),
        ):
            summary, by_inst = batch_competition_exposure_risk_summary(
                store,
                [_uuid(1), _uuid(2)],
                symbol_by_instrument_id={inst_id: "BTCUSD"},
            )

        assert summary.total_open_exposure == Decimal("35000.00")
        assert summary.total_open_risk_usd == Decimal("14.50")
        assert summary.total_equity == Decimal("4000")
        assert summary.open_risk_pct == pytest.approx(0.36, abs=0.01)

        asset = by_inst[inst_id]
        assert asset.open_exposure == Decimal("35000.00")
        assert asset.open_risk_usd == Decimal("14.50")
        assert asset.open_risk_pct == pytest.approx(0.36, abs=0.01)
        assert asset.global_risk_cap_pct == 2.0


@patch("quantara_engine.persistence.batch_summary.batch_latest_candle_closes")
@patch("quantara_engine.persistence.batch_summary.batch_latest_candle_timestamps")
@patch("quantara_engine.persistence.batch_summary.batch_candle_counts")
def test_analytics_assets_includes_exposure_risk_summary(
    mock_candle_counts,
    mock_last_ts,
    mock_closes,
):
    store = MagicMock()
    inst_id = "inst-btcusd"
    store.list_all_competition_entries.return_value = (
        [{"portfolio": MagicMock(id="pa")}],
        [{"portfolio": MagicMock(id="pb")}],
        [{"portfolio": MagicMock(id="pa")}, {"portfolio": MagicMock(id="pb")}],
    )
    store.batch_asset_trading_metrics.return_value = {
        inst_id: {
            "open_positions": 2,
            "closed_trades": 1,
            "realized_pnl": 10.0,
            "unrealized_pnl": 5.0,
            "total_pnl": 15.0,
        }
    }
    store.batch_competition_exposure_risk_summary.return_value = (
        CompetitionExposureRiskSummary(
            total_open_exposure=Decimal("82450"),
            total_open_risk_usd=Decimal("315"),
            total_equity=Decimal("320000"),
            open_risk_pct=0.10,
        ),
        {
            inst_id: AssetExposureRiskMetrics(
                open_exposure=Decimal("35000"),
                open_risk_usd=Decimal("72"),
                asset_allocated_equity=Decimal("10000"),
                open_risk_pct=0.72,
                global_risk_cap_pct=2.0,
            )
        },
    )
    store.get_instrument_by_symbol.side_effect = lambda sym: MagicMock(id=f"inst-{sym.lower()}")
    mock_candle_counts.return_value = {inst_id: {"5m": 200, "15m": 200, "1h": 200}}
    mock_last_ts.return_value = {
        inst_id: datetime(2026, 9, 9, 14, 0, tzinfo=timezone.utc),
    }
    mock_closes.return_value = {inst_id: Decimal("70000")}
    store.get_settings_dict.return_value = {
        "worker_status:data_fetcher": {
            "assets": {"BTCUSD": {"status": "healthy", "provider": "tiingo"}},
        }
    }

    payload = analytics_assets(store)
    assert payload["summary"]["open_exposure"] == 82450.0
    assert payload["summary"]["open_risk_usd"] == 315.0
    assert payload["summary"]["open_risk_pct"] == 0.10

    btc = next(row for row in payload["assets"] if row["db_symbol"] == "BTCUSD")
    assert btc["open_exposure"] == 35000.0
    assert btc["open_risk_usd"] == 72.0
    assert btc["open_risk_pct"] == 0.72
    assert btc["global_risk_cap_pct"] == 2.0
    store.batch_competition_exposure_risk_summary.assert_called_once()
