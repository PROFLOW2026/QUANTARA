"""Regression tests for competition open-positions API serialization."""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from quantara_engine.domain.types import Direction, Instrument, Position, PositionStatus
from quantara_engine.portfolio.currency import FxRateTable
from quantara_engine.persistence.batch_summary import planned_position_metrics


def _usd_inst(inst_id: str = "inst-eth") -> Instrument:
    return Instrument(
        id=inst_id,
        symbol="ETHUSD",
        name="ETH",
        asset_class="crypto",
        base_currency="ETH",
        quote_currency="USD",
        pip_size=Decimal("0.01"),
        contract_size=Decimal("1"),
        price_tick_size=Decimal("0.01"),
        quantity_step=Decimal("0.0001"),
        min_quantity=Decimal("0.0001"),
    )


def _position(*, tp: Decimal | None = Decimal("2539")) -> Position:
    return Position(
        id="pos-1",
        portfolio_id="pf-1",
        strategy_instance_id="si-1",
        instrument_id="inst-eth",
        direction=Direction.LONG,
        quantity=Decimal("1"),
        entry_price=Decimal("2525"),
        current_price=Decimal("2520"),
        stop_loss=Decimal("2518"),
        take_profit=tp,
        unrealized_pnl=Decimal("-5"),
        status=PositionStatus.OPEN,
    )


class TestPlannedPositionMetrics:
    def test_with_tp_returns_target_profit_and_rr(self):
        pos = _position()
        inst = _usd_inst()
        fx = FxRateTable.usd_only()
        metrics = planned_position_metrics(
            position=pos,
            instrument=inst,
            fx_rates=fx,
            intent_risk=Decimal("10"),
        )
        assert metrics["target_profit_usd"] is not None
        assert metrics["target_profit_usd"] > 0
        assert metrics["risk_reward_ratio"] is not None

    def test_null_tp_returns_none_target_not_crash(self):
        pos = _position(tp=None)
        metrics = planned_position_metrics(
            position=pos,
            instrument=_usd_inst(),
            fx_rates=FxRateTable.usd_only(),
            intent_risk=Decimal("10"),
        )
        assert metrics["target_profit_usd"] is None
        assert metrics["risk_reward_ratio"] is None
        assert metrics["risk_to_sl_usd"] == Decimal("10")


class TestBuildCompetitionOpenPositionsFx:
    """Ensure open-positions detail path passes quote_currencies to FX resolver."""

    def _minimal_store(self, open_positions: list[Position]) -> MagicMock:
        store = MagicMock()
        portfolio = SimpleNamespace(
            id="pf-1",
            name="ETH Test",
            initial_capital=Decimal("2000"),
            equity=Decimal("2000"),
            balance=Decimal("2000"),
            unrealized_pnl=Decimal("0"),
            exposure_notional=Decimal("0"),
            peak_equity=Decimal("2000"),
            status=SimpleNamespace(value="active"),
        )
        risk = SimpleNamespace(slug="balanced", risk_per_trade_pct=Decimal("1"))
        instance = SimpleNamespace(
            id="si-1",
            strategy_slug="momentum-continuation",
            strategy_version="1.0.0",
            timeframe="15m",
        )
        entry = {
            "portfolio": portfolio,
            "instance": instance,
            "risk_profile": risk,
            "sort_order": 1,
        }
        store.list_all_competition_entries.return_value = ([entry], [], [entry])
        store.list_multi_strategy_competition_entries.return_value = []
        store.get_competition_started_at.return_value = None
        store.get_competition_experiment_id.return_value = "exp-1"
        store.get_settings_dict.return_value = {}
        store.is_multi_strategy_competition_enabled.return_value = True
        store.batch_portfolio_dashboard_stats.return_value = {
            "pf-1": {
                "open_positions": open_positions,
                "realized_pnl": Decimal("0"),
                "closed_trades_count": 0,
                "win_rate": None,
            }
        }
        store.list_competition_trades.return_value = []
        store.get_competition_today_stats.return_value = {}
        return store

    @patch("quantara_engine.competition.service._collect_closed_trades", return_value=[])
    @patch("quantara_engine.persistence.batch_summary.batch_entry_actual_risk_by_position", return_value={})
    @patch("quantara_engine.persistence.batch_summary._batch_instruments_by_id")
    @patch("quantara_engine.portfolio.currency.resolve_dashboard_fx_rates")
    def test_open_positions_with_tp_calls_fx_with_quote_currencies(
        self,
        mock_fx,
        mock_instruments,
        _mock_risk,
        _mock_trades,
    ):
        from quantara_engine.competition.service import build_competition_response

        mock_fx.return_value = FxRateTable.usd_only()
        mock_instruments.return_value = {"inst-eth": _usd_inst()}

        store = self._minimal_store([_position()])
        payload = build_competition_response(store)

        mock_fx.assert_called_once()
        call_args = mock_fx.call_args[0]
        assert len(call_args) == 2
        assert isinstance(call_args[1], set)
        assert "USD" in call_args[1]
        assert len(payload["open_positions"]) == 1
        row = payload["open_positions"][0]
        assert row["take_profit"] is not None
        assert row["target_profit_usd"] is not None
        assert row["risk_reward_ratio"] is not None

    @patch("quantara_engine.competition.service._collect_closed_trades", return_value=[])
    @patch("quantara_engine.persistence.batch_summary.batch_entry_actual_risk_by_position", return_value={})
    @patch("quantara_engine.persistence.batch_summary._batch_instruments_by_id", return_value={})
    @patch("quantara_engine.portfolio.currency.resolve_dashboard_fx_rates")
    def test_zero_open_positions_does_not_crash(
        self,
        mock_fx,
        _mock_instruments,
        _mock_risk,
        _mock_trades,
    ):
        from quantara_engine.competition.service import build_competition_response

        mock_fx.return_value = FxRateTable.usd_only()

        store = self._minimal_store([])
        payload = build_competition_response(store)

        mock_fx.assert_called_once()
        assert mock_fx.call_args[0][1] == set()
        assert payload["open_positions"] == []

    @patch("quantara_engine.competition.service._collect_closed_trades", return_value=[])
    @patch("quantara_engine.persistence.batch_summary.batch_entry_actual_risk_by_position", return_value={})
    @patch("quantara_engine.persistence.batch_summary._batch_instruments_by_id")
    @patch("quantara_engine.portfolio.currency.resolve_dashboard_fx_rates")
    def test_null_tp_serializes_without_crash(
        self,
        mock_fx,
        mock_instruments,
        _mock_risk,
        _mock_trades,
    ):
        from quantara_engine.competition.service import build_competition_response

        mock_fx.return_value = FxRateTable.usd_only()
        mock_instruments.return_value = {"inst-eth": _usd_inst()}

        store = self._minimal_store([_position(tp=None)])
        payload = build_competition_response(store)

        row = payload["open_positions"][0]
        assert row["take_profit"] is None
        assert row["target_profit_usd"] is None
        assert row["risk_reward_ratio"] is None


def test_live_build_competition_response_if_db_available():
    """Case D: real DB with mixed assets — must return 200-shaped payload."""
    pytest.importorskip("sqlalchemy")
    try:
        from quantara_engine.db.session import session_scope
        from quantara_engine.persistence.store import TradingStore
        from quantara_engine.competition.service import build_competition_response
    except ImportError:
        pytest.skip("engine deps unavailable")

    try:
        with session_scope() as session:
            store = TradingStore(session)
            payload = build_competition_response(store)
    except Exception as exc:
        if "DATABASE_URL" in str(exc) or "connection" in str(exc).lower():
            pytest.skip(f"DB unavailable: {exc}")
        raise

    assert payload.get("active") is True
    assert isinstance(payload.get("open_positions"), list)
