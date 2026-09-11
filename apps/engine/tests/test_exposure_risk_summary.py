"""Batched competition exposure / open-risk dashboard aggregation."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch
from uuid import UUID

import pytest

from quantara_engine.api.routes import analytics_assets
from quantara_engine.domain.types import Instrument
from quantara_engine.models.enums import Direction as OrmDirection
from quantara_engine.portfolio.currency import FxRateTable
from quantara_engine.persistence.batch_summary import (
    AssetExposureRiskMetrics,
    CompetitionExposureRiskSummary,
    _position_mark_price,
    batch_competition_exposure_risk_summary,
    batch_entry_actual_risk_by_position,
)


def _uuid(n: int) -> str:
    return f"00000000-0000-4000-8000-{n:012d}"


def _usd_inst(inst_id: str, symbol: str = "BTCUSD") -> Instrument:
    return Instrument(
        id=inst_id,
        symbol=symbol,
        name=symbol,
        asset_class="crypto",
        base_currency="BTC",
        quote_currency="USD",
        pip_size=Decimal("0.01"),
        contract_size=Decimal("1"),
        price_tick_size=Decimal("0.01"),
        quantity_step=Decimal("0.0001"),
        min_quantity=Decimal("0.0001"),
    )


class TestBatchEntryActualRisk:
    def test_empty_positions_returns_empty(self):
        store = MagicMock()
        assert batch_entry_actual_risk_by_position(store, []) == {}


class TestPositionMarkPrice:
    def test_falls_back_to_entry_price_when_mark_zero(self):
        assert _position_mark_price(Decimal("0"), Decimal("70123.45")) == Decimal("70123.45")

    def test_prefers_current_price_when_positive(self):
        assert _position_mark_price(Decimal("70500"), Decimal("70123.45")) == Decimal("70500")


class TestBatchCompetitionExposureRiskSummary:
    def test_no_open_positions_returns_zero_exposure_and_risk(self):
        store = MagicMock()
        store.session.execute.return_value.all.return_value = []

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
        assert summary.open_position_count == 0
        assert by_inst == {}

    def test_aggregates_exposure_risk_and_asset_pct(self):
        store = MagicMock()
        pos_id = _uuid(501)
        inst_id = _uuid(100)
        store.session.execute.return_value.all.return_value = [
            (
                UUID(pos_id),
                UUID(inst_id),
                Decimal("0.5"),
                Decimal("70000"),
                Decimal("69900"),
                Decimal("68000"),
                OrmDirection.LONG,
            ),
        ]
        store.session.scalars.return_value.all.return_value = []

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
            "quantara_engine.persistence.batch_summary._batch_instruments_by_id",
            return_value={inst_id: _usd_inst(inst_id)},
        ), patch(
            "quantara_engine.portfolio.currency.resolve_dashboard_fx_rates",
            return_value=FxRateTable.usd_only(),
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
        assert summary.open_position_count == 1
        assert summary.risk_found_count == 1
        assert summary.risk_missing_count == 0

        asset = by_inst[inst_id]
        assert asset.open_exposure == Decimal("35000.00")
        assert asset.open_risk_usd == Decimal("14.50")

    def test_many_open_positions_have_positive_exposure(self):
        store = MagicMock()
        inst_id = _uuid(100)
        rows = []
        for i in range(25):
            rows.append(
                (
                    UUID(_uuid(600 + i)),
                    UUID(inst_id),
                    Decimal("1"),
                    Decimal("100"),
                    Decimal("99"),
                    Decimal("95"),
                    OrmDirection.LONG,
                )
            )
        store.session.execute.return_value.all.return_value = rows

        with patch(
            "quantara_engine.persistence.batch_summary.batch_portfolio_equity",
            return_value={_uuid(1): Decimal("320000")},
        ), patch(
            "quantara_engine.persistence.batch_summary.batch_entry_actual_risk_by_position",
            return_value={_uuid(600 + i): Decimal("10") for i in range(25)},
        ), patch(
            "quantara_engine.persistence.batch_summary._batch_instruments_by_id",
            return_value={inst_id: _usd_inst(inst_id)},
        ), patch(
            "quantara_engine.portfolio.currency.resolve_dashboard_fx_rates",
            return_value=FxRateTable.usd_only(),
        ), patch(
            "quantara_engine.competition.asset_equity.portfolio_ids_for_symbol",
            return_value=[_uuid(1)],
        ), patch(
            "quantara_engine.competition.asset_equity.nominal_asset_allocated_equity",
            return_value=Decimal("320000"),
        ), patch(
            "quantara_engine.market_data.active_universe.ACTIVE_DB_SYMBOLS",
            ("BTCUSD",),
        ):
            summary, _ = batch_competition_exposure_risk_summary(
                store,
                [_uuid(1)],
                symbol_by_instrument_id={inst_id: "BTCUSD"},
            )

        assert summary.open_position_count == 25
        assert summary.total_open_exposure == Decimal("2500.00")
        assert summary.total_open_risk_usd == Decimal("250.00")

    def test_uses_abs_quantity_and_entry_price_fallback(self):
        store = MagicMock()
        pos_id = _uuid(701)
        inst_id = _uuid(101)
        store.session.execute.return_value.all.return_value = [
            (
                UUID(pos_id),
                UUID(inst_id),
                Decimal("-2"),
                Decimal("0"),
                Decimal("50"),
                Decimal("48"),
                OrmDirection.SHORT,
            ),
        ]

        with patch(
            "quantara_engine.persistence.batch_summary.batch_portfolio_equity",
            return_value={_uuid(1): Decimal("10000")},
        ), patch(
            "quantara_engine.persistence.batch_summary.batch_entry_actual_risk_by_position",
            return_value={pos_id: Decimal("5")},
        ), patch(
            "quantara_engine.persistence.batch_summary._batch_instruments_by_id",
            return_value={inst_id: _usd_inst(inst_id, "NVDA")},
        ), patch(
            "quantara_engine.portfolio.currency.resolve_dashboard_fx_rates",
            return_value=FxRateTable.usd_only(),
        ), patch(
            "quantara_engine.competition.asset_equity.portfolio_ids_for_symbol",
            return_value=[_uuid(1)],
        ), patch(
            "quantara_engine.competition.asset_equity.nominal_asset_allocated_equity",
            return_value=Decimal("10000"),
        ), patch(
            "quantara_engine.market_data.active_universe.ACTIVE_DB_SYMBOLS",
            ("NVDA",),
        ):
            summary, by_inst = batch_competition_exposure_risk_summary(
                store,
                [_uuid(1)],
                symbol_by_instrument_id={inst_id: "NVDA"},
            )

        assert summary.total_open_exposure == Decimal("100.00")
        assert by_inst[inst_id].open_exposure == Decimal("100.00")

    def test_missing_risk_metadata_not_zero(self):
        store = MagicMock()
        pos_id = _uuid(801)
        inst_id = _uuid(102)
        store.session.execute.return_value.all.return_value = [
            (
                UUID(pos_id),
                UUID(inst_id),
                Decimal("1"),
                Decimal("100"),
                Decimal("100"),
                Decimal("0"),
                OrmDirection.LONG,
            ),
        ]

        with patch(
            "quantara_engine.persistence.batch_summary.batch_portfolio_equity",
            return_value={_uuid(1): Decimal("10000")},
        ), patch(
            "quantara_engine.persistence.batch_summary.batch_entry_actual_risk_by_position",
            return_value={},
        ), patch(
            "quantara_engine.persistence.batch_summary._batch_instruments_by_id",
            return_value={inst_id: _usd_inst(inst_id, "NVDA")},
        ), patch(
            "quantara_engine.portfolio.currency.resolve_dashboard_fx_rates",
            return_value=FxRateTable.usd_only(),
        ), patch(
            "quantara_engine.competition.asset_equity.portfolio_ids_for_symbol",
            return_value=[_uuid(1)],
        ), patch(
            "quantara_engine.competition.asset_equity.nominal_asset_allocated_equity",
            return_value=Decimal("10000"),
        ), patch(
            "quantara_engine.market_data.active_universe.ACTIVE_DB_SYMBOLS",
            ("NVDA",),
        ):
            summary, by_inst = batch_competition_exposure_risk_summary(
                store,
                [_uuid(1)],
                symbol_by_instrument_id={inst_id: "NVDA"},
            )

        assert summary.total_open_exposure == Decimal("100.00")
        assert summary.total_open_risk_usd is None
        assert summary.risk_missing_count == 1
        assert by_inst[inst_id].open_risk_usd is None

    def test_asset_exposure_sums_to_total(self):
        store = MagicMock()
        inst_a = _uuid(110)
        inst_b = _uuid(111)
        store.session.execute.return_value.all.return_value = [
            (UUID(_uuid(901)), UUID(inst_a), Decimal("1"), Decimal("10"), Decimal("10"), Decimal("9"), OrmDirection.LONG),
            (UUID(_uuid(902)), UUID(inst_b), Decimal("2"), Decimal("20"), Decimal("20"), Decimal("18"), OrmDirection.LONG),
        ]

        with patch(
            "quantara_engine.persistence.batch_summary.batch_portfolio_equity",
            return_value={_uuid(1): Decimal("10000")},
        ), patch(
            "quantara_engine.persistence.batch_summary.batch_entry_actual_risk_by_position",
            return_value={_uuid(901): Decimal("3"), _uuid(902): Decimal("4")},
        ), patch(
            "quantara_engine.persistence.batch_summary._batch_instruments_by_id",
            return_value={
                inst_a: _usd_inst(inst_a, "BTCUSD"),
                inst_b: _usd_inst(inst_b, "NVDA"),
            },
        ), patch(
            "quantara_engine.portfolio.currency.resolve_dashboard_fx_rates",
            return_value=FxRateTable.usd_only(),
        ), patch(
            "quantara_engine.competition.asset_equity.portfolio_ids_for_symbol",
            side_effect=lambda sym: [_uuid(1)],
        ), patch(
            "quantara_engine.competition.asset_equity.nominal_asset_allocated_equity",
            return_value=Decimal("10000"),
        ), patch(
            "quantara_engine.market_data.active_universe.ACTIVE_DB_SYMBOLS",
            ("BTCUSD", "NVDA"),
        ):
            summary, by_inst = batch_competition_exposure_risk_summary(
                store,
                [_uuid(1)],
                symbol_by_instrument_id={inst_a: "BTCUSD", inst_b: "NVDA"},
            )

        asset_total = sum(row.open_exposure for row in by_inst.values())
        assert asset_total == summary.total_open_exposure
        risk_total = sum(row.open_risk_usd or Decimal("0") for row in by_inst.values())
        assert risk_total == summary.total_open_risk_usd


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
            open_position_count=2,
            risk_found_count=2,
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
    assert payload["summary"]["open_position_count"] == 2

    btc = next(row for row in payload["assets"] if row["db_symbol"] == "BTCUSD")
    assert btc["open_exposure"] == 35000.0
    assert btc["open_risk_usd"] == 72.0
    store.batch_competition_exposure_risk_summary.assert_called_once()


@patch("quantara_engine.persistence.batch_summary.batch_latest_candle_closes")
@patch("quantara_engine.persistence.batch_summary.batch_latest_candle_timestamps")
@patch("quantara_engine.persistence.batch_summary.batch_candle_counts")
def test_analytics_assets_open_positions_without_metrics_use_null_not_zero(
    mock_candle_counts,
    mock_last_ts,
    mock_closes,
):
    store = MagicMock()
    inst_id = "inst-btcusd"
    store.list_all_competition_entries.return_value = (
        [{"portfolio": MagicMock(id="pa")}],
        [],
        [{"portfolio": MagicMock(id="pa")}],
    )
    store.batch_asset_trading_metrics.return_value = {
        inst_id: {
            "open_positions": 3,
            "closed_trades": 0,
            "realized_pnl": 0.0,
            "unrealized_pnl": 1.0,
            "total_pnl": 1.0,
        }
    }
    store.batch_competition_exposure_risk_summary.return_value = (
        CompetitionExposureRiskSummary(
            total_open_exposure=Decimal("0"),
            total_open_risk_usd=None,
            total_equity=Decimal("320000"),
            open_risk_pct=None,
            open_position_count=3,
            risk_missing_count=3,
        ),
        {},
    )
    store.get_instrument_by_symbol.side_effect = lambda sym: MagicMock(id=inst_id)
    mock_candle_counts.return_value = {inst_id: {"5m": 200, "15m": 200, "1h": 200}}
    mock_last_ts.return_value = {inst_id: datetime(2026, 9, 9, 14, 0, tzinfo=timezone.utc)}
    mock_closes.return_value = {inst_id: Decimal("70000")}
    store.get_settings_dict.return_value = {"worker_status:data_fetcher": {"assets": {}}}

    payload = analytics_assets(store)
    btc = next(row for row in payload["assets"] if row["db_symbol"] == "BTCUSD")
    assert btc["open_positions"] == 3
    assert btc["open_exposure"] is None
    assert btc["open_risk_usd"] is None
