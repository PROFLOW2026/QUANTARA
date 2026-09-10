"""Multi-robot competition aggregation tests."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock

from quantara_engine.competition.constants import COMPETITION_TOTAL_INITIAL
from quantara_engine.competition.orb_constants import ORB_COMPETITION_TOTAL_INITIAL
from quantara_engine.competition.service import build_competition_response
from quantara_engine.domain.types import Portfolio, PortfolioStatus
from quantara_engine.persistence.store import TradingStore


def _entry(portfolio_id: str, instance_id: str, *, slug: str = "gold-trend-pullback", tf: str = "5m"):
    portfolio = Portfolio(
        id=portfolio_id,
        name=f"Portfolio {portfolio_id[-3:]}",
        mode="paper",
        initial_capital=Decimal("2000"),
        balance=Decimal("2000"),
        equity=Decimal("2000"),
        peak_equity=Decimal("2000"),
        status=PortfolioStatus.ACTIVE,
    )
    risk = MagicMock()
    risk.slug = "balanced"
    risk.risk_per_trade_pct = Decimal("1")
    instance = MagicMock()
    instance.id = instance_id
    instance.timeframe = tf
    return {
        "portfolio": portfolio,
        "instance": instance,
        "risk_profile": risk,
        "sort_order": 1,
        "_slug": slug,
    }


def _mock_store(*, orb_enabled: bool, robot_a_count: int = 120, robot_b_count: int = 40):
    robot_a = [
        _entry(f"a{i:03d}", f"ia{i:03d}", tf=["5m", "15m", "1h"][i % 3])
        for i in range(robot_a_count)
    ]
    robot_b = [
        _entry(f"b{i:03d}", f"ib{i:03d}", slug="opening-range-breakout")
        for i in range(robot_b_count if orb_enabled else 0)
    ]
    store = MagicMock(spec=TradingStore)
    store.list_competition_entries.return_value = robot_a
    store.list_orb_competition_entries.return_value = robot_b
    store.is_orb_competition_enabled.return_value = orb_enabled
    store.list_all_competition_entries.return_value = (robot_a, robot_b, robot_a + robot_b)
    store.get_competition_started_at.return_value = None
    store.get_competition_experiment_id.return_value = "00000000-0000-0000-0000-000000000400"
    store.sum_realized_pnl.return_value = Decimal("0")
    store.count_trades_for_portfolio.return_value = 0
    store.portfolio_win_rate.return_value = None
    store.list_positions.return_value = []
    store.load_portfolio_state.return_value = MagicMock(snapshots=[])
    store.list_competition_trades.return_value = []
    store.get_competition_today_stats.return_value = {
        "market_checks_today": 0,
        "entry_signals_today": 0,
        "strategy_signals_today": 0,
        "trades_opened_today": 0,
        "trades_closed_today": 0,
    }
    return store, robot_a, robot_b


def test_robot_a_only_dashboard_count_120():
    store, robot_a, robot_b = _mock_store(orb_enabled=False)
    payload = build_competition_response(store)
    assert payload["experiment"]["portfolio_count"] == 120
    assert payload["experiment"]["robot_a_portfolio_count"] == 120
    assert payload["experiment"]["robot_b_portfolio_count"] == 0
    assert payload["combined"]["initial_equity"] == float(COMPETITION_TOTAL_INITIAL)
    assert len(payload["portfolios"]) == 120
    assert len(robot_b) == 0


def test_robot_a_and_orb_dashboard_count_160():
    store, _, _ = _mock_store(orb_enabled=True)
    payload = build_competition_response(store)
    assert payload["experiment"]["portfolio_count"] == 160
    assert payload["experiment"]["robot_a_portfolio_count"] == 120
    assert payload["experiment"]["robot_b_portfolio_count"] == 40
    assert len(payload["portfolios"]) == 160


def test_combined_initial_capital_320000_when_orb_enabled():
    store, _, _ = _mock_store(orb_enabled=True)
    payload = build_competition_response(store)
    expected = float(COMPETITION_TOTAL_INITIAL + ORB_COMPETITION_TOTAL_INITIAL)
    assert payload["combined"]["initial_equity"] == expected
    assert payload["experiment"]["total_initial_capital"] == expected
    assert expected == 320000.0


def test_robot_a_totals_unchanged_when_orb_enabled():
    store_a, _, _ = _mock_store(orb_enabled=False)
    store_both, _, _ = _mock_store(orb_enabled=True)
    payload_a = build_competition_response(store_a)
    payload_both = build_competition_response(store_both)
    group_a = payload_both["robot_groups"][0]
    assert group_a["portfolio_count"] == payload_a["experiment"]["portfolio_count"]
    assert group_a["initial_capital"] == payload_a["combined"]["initial_equity"]


def test_robot_b_totals_isolated():
    store, _, _ = _mock_store(orb_enabled=True)
    payload = build_competition_response(store)
    group_b = payload["robot_groups"][1]
    assert group_b["robot_label"] == "Robot B"
    assert group_b["strategy_slug"] == "opening-range-breakout"
    assert group_b["portfolio_count"] == 40
    assert group_b["initial_capital"] == float(ORB_COMPETITION_TOTAL_INITIAL)


def test_strategy_identity_preserved_on_portfolios():
    store, _, _ = _mock_store(orb_enabled=True)
    payload = build_competition_response(store)
    robot_a = [p for p in payload["portfolios"] if p["robot_label"] == "Robot A"]
    robot_b = [p for p in payload["portfolios"] if p["robot_label"] == "Robot B"]
    assert len(robot_a) == 120
    assert len(robot_b) == 40
    assert all(p["strategy_slug"] == "gold-trend-pullback" for p in robot_a)
    assert all(p["strategy_slug"] == "opening-range-breakout" for p in robot_b)


def test_no_double_counting_portfolio_ids():
    store, _, _ = _mock_store(orb_enabled=True)
    payload = build_competition_response(store)
    ids = [p["id"] for p in payload["portfolios"]]
    assert len(ids) == len(set(ids))
