"""USD account-currency exposure conversion for dashboard analytics."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock, patch
from uuid import UUID

import pytest

from quantara_engine.domain.types import Instrument
from quantara_engine.models.enums import Direction as OrmDirection
from quantara_engine.persistence.batch_summary import (
    _position_exposure_usd,
    batch_competition_exposure_risk_summary,
)
from quantara_engine.portfolio.currency import FxRateTable


def _uuid(n: int) -> str:
    return f"00000000-0000-4000-8000-{n:012d}"


def _gbpjpy_inst(inst_id: str) -> Instrument:
    return Instrument(
        id=inst_id,
        symbol="GBPJPY",
        name="GBP/JPY",
        asset_class="forex",
        base_currency="GBP",
        quote_currency="JPY",
        pip_size=Decimal("0.01"),
        contract_size=Decimal("100000"),
        price_tick_size=Decimal("0.001"),
        quantity_step=Decimal("1000"),
        min_quantity=Decimal("1000"),
    )


def _usd_inst(inst_id: str, symbol: str) -> Instrument:
    return Instrument(
        id=inst_id,
        symbol=symbol,
        name=symbol,
        asset_class="crypto" if symbol == "BTCUSD" else "commodity",
        base_currency=symbol[:3],
        quote_currency="USD",
        pip_size=Decimal("0.01"),
        contract_size=Decimal("1"),
        price_tick_size=Decimal("0.01"),
        quantity_step=Decimal("0.0001"),
        min_quantity=Decimal("0.0001"),
    )


class TestPositionExposureUsd:
    def test_gbpjpy_regression_example(self):
        fx = FxRateTable.with_jpy(Decimal("150"))
        inst = _gbpjpy_inst(_uuid(1))
        exposure = _position_exposure_usd(
            quantity=Decimal("100000"),
            mark=Decimal("207"),
            instrument=inst,
            fx_rates=fx,
        )
        assert exposure == Decimal("138000.00")

    def test_usd_asset_unchanged(self):
        fx = FxRateTable.usd_only()
        inst = _usd_inst(_uuid(2), "XAUUSD")
        exposure = _position_exposure_usd(
            quantity=Decimal("2"),
            mark=Decimal("2650.50"),
            instrument=inst,
            fx_rates=fx,
        )
        assert exposure == Decimal("5301.00")

    def test_btcusd_unchanged(self):
        fx = FxRateTable.usd_only()
        inst = _usd_inst(_uuid(3), "BTCUSD")
        exposure = _position_exposure_usd(
            quantity=Decimal("0.5"),
            mark=Decimal("70000"),
            instrument=inst,
            fx_rates=fx,
        )
        assert exposure == Decimal("35000.00")

    def test_missing_jpy_rate_returns_none(self):
        fx = FxRateTable.usd_only()
        inst = _gbpjpy_inst(_uuid(4))
        assert (
            _position_exposure_usd(
                quantity=Decimal("100000"),
                mark=Decimal("207"),
                instrument=inst,
                fx_rates=fx,
            )
            is None
        )


class TestBatchExposureUsdConversion:
    def _run_summary(self, open_rows, instruments, fx_rates):
        store = MagicMock()
        store.session.execute.return_value.all.return_value = open_rows
        store._instrument_to_domain.side_effect = lambda row: instruments[str(row.id)]

        with patch(
            "quantara_engine.persistence.batch_summary.batch_portfolio_equity",
            return_value={_uuid(1): Decimal("320000")},
        ), patch(
            "quantara_engine.persistence.batch_summary.batch_entry_actual_risk_by_position",
            return_value={},
        ), patch(
            "quantara_engine.persistence.batch_summary._batch_instruments_by_id",
            return_value=instruments,
        ), patch(
            "quantara_engine.portfolio.currency.resolve_dashboard_fx_rates",
            return_value=fx_rates,
        ), patch(
            "quantara_engine.competition.asset_equity.portfolio_ids_for_symbol",
            side_effect=lambda sym: [_uuid(1)],
        ), patch(
            "quantara_engine.competition.asset_equity.nominal_asset_allocated_equity",
            return_value=Decimal("320000"),
        ), patch(
            "quantara_engine.market_data.active_universe.ACTIVE_DB_SYMBOLS",
            ("GBPJPY", "XAUUSD", "BTCUSD", "COIN"),
        ):
            return batch_competition_exposure_risk_summary(
                store,
                [_uuid(1)],
                symbol_by_instrument_id={
                    _uuid(100): "GBPJPY",
                    _uuid(101): "XAUUSD",
                    _uuid(102): "BTCUSD",
                    _uuid(103): "COIN",
                },
            )

    def test_mixed_portfolio_reconciles_usd_exposure(self):
        gbp_id = _uuid(100)
        xau_id = _uuid(101)
        coin_id = _uuid(103)
        fx = FxRateTable.with_jpy(Decimal("150"))
        instruments = {
            gbp_id: _gbpjpy_inst(gbp_id),
            xau_id: _usd_inst(xau_id, "XAUUSD"),
            coin_id: _usd_inst(coin_id, "COIN"),
        }
        open_rows = [
            (UUID(_uuid(501)), UUID(gbp_id), Decimal("100000"), Decimal("207"), Decimal("207"), Decimal("200"), OrmDirection.LONG),
            (UUID(_uuid(502)), UUID(xau_id), Decimal("1"), Decimal("2650"), Decimal("2650"), Decimal("2600"), OrmDirection.LONG),
            (UUID(_uuid(503)), UUID(coin_id), Decimal("10"), Decimal("850"), Decimal("850"), Decimal("800"), OrmDirection.LONG),
        ]
        summary, by_inst = self._run_summary(open_rows, instruments, fx)

        assert by_inst[gbp_id].open_exposure == Decimal("138000.00")
        assert by_inst[xau_id].open_exposure == Decimal("2650.00")
        assert by_inst[coin_id].open_exposure == Decimal("8500.00")
        assert summary.total_open_exposure == Decimal("149150.00")
        asset_sum = sum(v.open_exposure or Decimal("0") for v in by_inst.values())
        assert asset_sum == summary.total_open_exposure

    def test_missing_fx_cache_marks_gbpjpy_unavailable_not_fake_usd(self):
        gbp_id = _uuid(100)
        xau_id = _uuid(101)
        fx = FxRateTable.usd_only()
        instruments = {
            gbp_id: _gbpjpy_inst(gbp_id),
            xau_id: _usd_inst(xau_id, "XAUUSD"),
        }
        open_rows = [
            (UUID(_uuid(601)), UUID(gbp_id), Decimal("100000"), Decimal("207"), Decimal("207"), Decimal("200"), OrmDirection.LONG),
            (UUID(_uuid(602)), UUID(xau_id), Decimal("1"), Decimal("2650"), Decimal("2650"), Decimal("2600"), OrmDirection.LONG),
        ]
        summary, by_inst = self._run_summary(open_rows, instruments, fx)

        assert by_inst[gbp_id].open_exposure is None
        assert by_inst[xau_id].open_exposure == Decimal("2650.00")
        assert summary.total_open_exposure is None
        assert summary.exposure_missing_count == 1
        assert summary.exposure_available is False


@patch("quantara_engine.portfolio.fx_rate_cache._fetch_rate_from_provider")
@patch("quantara_engine.portfolio.fx_rate_cache.get_canonical_jpy_per_usd")
@patch("quantara_engine.portfolio.fx_rate_cache._fetch_rate_from_db_candles")
def test_dashboard_fx_read_path_never_calls_provider(db_mock, canonical_mock, provider_mock):
    from quantara_engine.portfolio.fx_rate_cache import build_dashboard_fx_rates

    store = MagicMock()
    store.get_settings_dict.return_value = {
        "fx_rate_cache:jpy_usd": {
            "rate": "150",
            "updated_at": "2026-09-11T12:00:00+00:00",
            "source": "test",
        }
    }
    table = build_dashboard_fx_rates(store, {"JPY"})
    assert table.quote_per_usd["JPY"] == Decimal("150")
    provider_mock.assert_not_called()
    canonical_mock.assert_not_called()
    db_mock.assert_not_called()
