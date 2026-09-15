"""Research vs Live Sim comparison — scope and metric integrity."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from quantara_engine.live_sim.analytics import build_comparison_summary
from quantara_engine.persistence.batch_summary import CompetitionExposureRiskSummary


def _research_snapshot():
    pos = MagicMock()
    pos.net_quantity = Decimal("-100")
    snap = MagicMock()
    snap.equity = Decimal("325066.29")
    snap.realized_pnl = Decimal("3884.98")
    snap.unrealized_pnl = Decimal("1181.31")
    snap.gross_exposure = Decimal("613000")
    snap.positions = {"NVDA": pos}
    return snap


def _live_sim_summary() -> dict:
    return {
        "available": True,
        "equity": 10063.09,
        "starting_capital": 10000.0,
        "total_return_pct": 0.63,
        "current_drawdown_pct": 0.0,
        "max_drawdown_pct": 0.0,
        "win_rate_pct": 40.35,
        "closed_trades_count": 57,
        "open_sl_risk_pct": 0.62,
        "gross_exposure": 2990.0,
        "realized_pnl": 37.83,
        "unrealized_pnl": 25.26,
        "fees_paid": 1.2,
        "open_positions": [{"id": "p1"}] * 10,
        "broker_positions": [{"symbol": "NVDA"}],
        "candidates": {
            "total": 100,
            "accepted": 20,
            "rejected": 80,
            "acceptance_rate_pct": 20.0,
        },
    }


@patch("quantara_engine.live_sim.analytics.build_live_sim_summary")
@patch("quantara_engine.broker.state_builder.build_competition_broker_account")
@patch("quantara_engine.live_sim.analytics.BrokerExecutionService")
@patch("quantara_engine.live_sim.analytics._research_broker_position_stats")
@patch("quantara_engine.live_sim.analytics._research_strategy_trade_stats")
def test_research_realized_positive_never_shows_zero_closed_trades(
    mock_strategy_stats,
    mock_broker_stats,
    mock_broker_svc,
    mock_research_account,
    mock_live_summary,
):
    mock_research_account.return_value = _research_snapshot()
    mock_live_summary.return_value = _live_sim_summary()
    mock_broker_svc.return_value.get_account_row.return_value = {
        "id": "acc-1",
        "fees_paid": 0,
        "risk_settings": {"high_water_mark": 325000},
        "starting_cash": 320000,
        "equity": 325066.29,
    }
    mock_strategy_stats.return_value = {
        "open_positions": 72,
        "closed_trades_count": 496,
        "wins": 253,
        "losses": 243,
        "win_rate_pct": 51.01,
        "open_sl_risk_pct": 0.53,
        "open_sl_risk_usd": 1723.0,
        "open_sl_risk_unavailable_he": None,
    }
    mock_broker_stats.return_value = {
        "open_positions": 6,
        "unique_symbols_open": 6,
        "closed_trades_count": 120,
        "wins": 55,
        "losses": 65,
        "win_rate_pct": 45.83,
        "open_sl_risk_pct": None,
        "open_sl_risk_usd": None,
        "open_sl_risk_unavailable_he": "נתוני SL פיזי לא שלמים (180 מתוך 380 לוטים)",
    }

    store = MagicMock()
    out = build_comparison_summary(store)

    assert out["available"] is True
    research = out["research"]
    assert research["realized_pnl"] == pytest.approx(3884.98)
    assert research["closed_trades"] == 496
    assert research["win_rate_pct"] == pytest.approx(51.01)
    assert research["open_positions"] == 72
    assert research["sl_risk_pct"] == pytest.approx(0.53)
    assert research["scopes"]["broker"]["open_sl_risk_pct"] is None
    assert "לא שלמים" in research["scopes"]["broker"]["open_sl_risk_unavailable_he"]


@patch("quantara_engine.live_sim.analytics.build_live_sim_summary")
@patch("quantara_engine.broker.state_builder.build_competition_broker_account")
@patch("quantara_engine.live_sim.analytics.BrokerExecutionService")
@patch("quantara_engine.live_sim.analytics._research_broker_position_stats")
@patch("quantara_engine.live_sim.analytics._research_strategy_trade_stats")
def test_research_strategy_sl_risk_not_zero_when_authoritative(
    mock_strategy_stats,
    mock_broker_stats,
    mock_broker_svc,
    mock_research_account,
    mock_live_summary,
):
    mock_research_account.return_value = _research_snapshot()
    mock_live_summary.return_value = _live_sim_summary()
    mock_broker_svc.return_value.get_account_row.return_value = {"id": "acc-1", "fees_paid": 0}
    mock_strategy_stats.return_value = {
        "open_positions": 5,
        "closed_trades_count": 10,
        "wins": 6,
        "losses": 4,
        "win_rate_pct": 60.0,
        "open_sl_risk_pct": 1.25,
        "open_sl_risk_usd": 4000.0,
        "open_sl_risk_unavailable_he": None,
    }
    mock_broker_stats.return_value = {
        "open_positions": 2,
        "unique_symbols_open": 2,
        "closed_trades_count": 10,
        "wins": 6,
        "losses": 4,
        "win_rate_pct": 60.0,
        "open_sl_risk_pct": None,
        "open_sl_risk_usd": None,
        "open_sl_risk_unavailable_he": "נתוני SL פיזי לא זמין",
    }

    out = build_comparison_summary(MagicMock())
    assert out["research"]["sl_risk_pct"] == pytest.approx(1.25)
    assert out["research"]["scopes"]["strategy"]["open_sl_risk_pct"] == pytest.approx(1.25)


@patch("quantara_engine.live_sim.analytics.build_live_sim_summary")
@patch("quantara_engine.broker.state_builder.build_competition_broker_account")
@patch("quantara_engine.live_sim.analytics.BrokerExecutionService")
@patch("quantara_engine.live_sim.analytics._research_broker_position_stats")
@patch("quantara_engine.live_sim.analytics._research_strategy_trade_stats")
def test_scopes_remain_explicitly_separated(
    mock_strategy_stats,
    mock_broker_stats,
    mock_broker_svc,
    mock_research_account,
    mock_live_summary,
):
    mock_research_account.return_value = _research_snapshot()
    mock_live_summary.return_value = _live_sim_summary()
    mock_broker_svc.return_value.get_account_row.return_value = {"id": "acc-1", "fees_paid": 0}
    mock_strategy_stats.return_value = {
        "open_positions": 72,
        "closed_trades_count": 496,
        "wins": 253,
        "losses": 243,
        "win_rate_pct": 51.0,
        "open_sl_risk_pct": 0.53,
        "open_sl_risk_usd": 1700.0,
        "open_sl_risk_unavailable_he": None,
    }
    mock_broker_stats.return_value = {
        "open_positions": 6,
        "unique_symbols_open": 6,
        "closed_trades_count": 120,
        "wins": 55,
        "losses": 65,
        "win_rate_pct": 45.0,
        "open_sl_risk_pct": None,
        "open_sl_risk_usd": None,
        "open_sl_risk_unavailable_he": "לא זמין",
    }

    out = build_comparison_summary(MagicMock())
    assert out["research"]["scopes"]["strategy"]["open_positions"] == 72
    assert out["research"]["scopes"]["broker"]["open_positions"] == 6
    assert out["live_sim"]["closed_trades"] == 57
    assert out["live_sim"]["open_positions"] == 10
    assert out["live_sim"]["realized_pnl"] == pytest.approx(37.83)


@patch("quantara_engine.live_sim.analytics._aggregate_competition_trade_metrics")
def test_aggregate_competition_trade_metrics_win_rate(mock_agg):
    from quantara_engine.live_sim.analytics import _research_strategy_trade_stats

    store = MagicMock()
    store.list_competition_entries.return_value = [{"portfolio": MagicMock(id="p1")}]
    mock_agg.return_value = {
        "closed_trades_count": 4,
        "wins": 3,
        "losses": 1,
        "win_rate_pct": 75.0,
    }
    store.get_instrument_by_symbol.return_value = MagicMock(id="inst-1", symbol="NVDA")
    store.batch_competition_exposure_risk_summary.return_value = (
        CompetitionExposureRiskSummary(
            total_open_exposure=Decimal("1000"),
            total_open_risk_usd=Decimal("500"),
            total_equity=Decimal("320000"),
            open_risk_pct=0.15,
            open_position_count=8,
            total_remaining_sl_risk_usd=Decimal("500"),
            risk_missing_count=0,
        ),
        {},
    )

    stats = _research_strategy_trade_stats(store, Decimal("325000"))
    assert stats["closed_trades_count"] == 4
    assert stats["open_positions"] == 8
    assert stats["open_sl_risk_pct"] == pytest.approx(500 / 325000 * 100, rel=1e-3)
