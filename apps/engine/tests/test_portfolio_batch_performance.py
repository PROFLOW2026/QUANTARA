"""Regression tests for batched 160-portfolio dashboard queries."""

from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import event, select

from quantara_engine.api.routes import analytics_assets, portfolios_list, positions
from quantara_engine.competition.service import build_competition_response
from quantara_engine.domain.types import Direction, Mode, Portfolio, PortfolioStatus
from quantara_engine.models.trading import Position as OrmPosition
from quantara_engine.models.trading import Trade as OrmTrade
from quantara_engine.persistence.batch_summary import (
    batch_asset_metrics,
    batch_trade_metrics,
)
from quantara_engine.persistence.store import TradingStore


def _count_queries(session) -> tuple[int, float]:
    stats = {"count": 0, "ms": 0.0}

    def before(conn, cursor, statement, parameters, context, executemany):
        conn.info.setdefault("starts", []).append(time.perf_counter())

    def after(conn, cursor, statement, parameters, context, executemany):
        start = conn.info["starts"].pop()
        stats["count"] += 1
        stats["ms"] += (time.perf_counter() - start) * 1000

    event.listen(session.bind, "before_cursor_execute", before)
    event.listen(session.bind, "after_cursor_execute", after)
    return stats  # type: ignore[return-value]


def test_batch_trade_metrics_empty():
    store = MagicMock()
    store.session.execute.return_value.all.return_value = []
    assert batch_trade_metrics(store, []) == {}


def test_portfolios_list_uses_batch_not_per_portfolio_loop():
    from quantara_engine.api import routes

    store = MagicMock()
    portfolio = Portfolio(
        id="p1",
        name="Test",
        mode=Mode.PAPER,
        initial_capital=Decimal("2000"),
        balance=Decimal("2000"),
        unrealized_pnl=Decimal("0"),
        equity=Decimal("2000"),
        exposure_notional=Decimal("0"),
        reserved_capital=Decimal("0"),
        peak_equity=Decimal("2000"),
        status=PortfolioStatus.ACTIVE,
    )
    entry = {
        "portfolio": portfolio,
        "instance": MagicMock(timeframe="5m"),
        "risk_profile": MagicMock(slug="balanced", risk_per_trade_pct=Decimal("1")),
        "sort_order": 1,
    }
    store.list_all_competition_entries.return_value = ([entry], [], [entry])
    store.batch_portfolio_dashboard_stats.return_value = {
        "p1": {
            "closed_trades_count": 2,
            "realized_pnl": Decimal("10"),
            "win_rate": 50.0,
            "open_positions": [],
            "open_positions_count": 0,
            "open_direction": None,
        }
    }

    result = routes.portfolios_list(store)

    assert len(result) == 1
    assert result[0]["closed_trades_count"] == 2
    store.batch_portfolio_dashboard_stats.assert_called_once()
    store.list_positions.assert_not_called()
    store.sum_realized_pnl.assert_not_called()
    store.count_trades_for_portfolio.assert_not_called()


def test_settings_dict_cached_per_store_instance():
    session = MagicMock()
    row = MagicMock(key="k", value="v")
    session.scalars.return_value.all.return_value = [row]
    store = TradingStore(session)
    first = store.get_settings_dict()
    second = store.get_settings_dict()
    assert first == second == {"k": "v"}
    assert session.scalars.call_count == 1


def test_competition_response_skips_load_portfolio_state():
    store = MagicMock()
    portfolio = Portfolio(
        id="p1",
        name="Test",
        mode=Mode.PAPER,
        initial_capital=Decimal("2000"),
        balance=Decimal("2000"),
        unrealized_pnl=Decimal("0"),
        equity=Decimal("2000"),
        exposure_notional=Decimal("0"),
        reserved_capital=Decimal("0"),
        peak_equity=Decimal("2000"),
        status=PortfolioStatus.ACTIVE,
    )
    entry = {
        "portfolio": portfolio,
        "instance": MagicMock(id="i1", timeframe="5m"),
        "risk_profile": MagicMock(slug="balanced", risk_per_trade_pct=Decimal("1")),
        "sort_order": 1,
    }
    store.list_all_competition_entries.return_value = ([entry], [], [entry])
    store.get_competition_started_at.return_value = None
    store.get_competition_experiment_id.return_value = "00000000-0000-0000-0000-000000000400"
    store.batch_portfolio_dashboard_stats.return_value = {
        "p1": {
            "closed_trades_count": 0,
            "realized_pnl": Decimal("0"),
            "win_rate": None,
            "open_positions": [],
            "open_positions_count": 0,
            "open_direction": None,
        }
    }
    store.list_competition_decisions.return_value = []
    store.list_competition_trades.return_value = []
    store.get_competition_today_stats.return_value = {
        "market_checks_today": 0,
        "entry_signals_today": 0,
        "strategy_signals_today": 0,
        "trades_opened_today": 0,
        "trades_closed_today": 0,
    }

    payload = build_competition_response(store)

    assert payload["active"] is True
    assert payload["equity_curves"] == {}
    assert payload["experiment"]["portfolio_count"] == 1
    store.load_portfolio_state.assert_not_called()


@pytest.mark.integration
def test_live_portfolios_query_budget_under_twenty():
    """Live DB: /portfolios must not explode query count with 160 portfolios."""
    from quantara_engine.db.session import session_scope

    with session_scope() as session:
        stats = {"count": 0}

        def after(conn, cursor, statement, parameters, context, executemany):
            stats["count"] += 1

        event.listen(session.bind, "after_cursor_execute", after)
        store = TradingStore(session)
        t0 = time.perf_counter()
        rows = portfolios_list(store)
        elapsed_ms = (time.perf_counter() - t0) * 1000

    assert len(rows) >= 120
    assert stats["count"] <= 20, f"too many queries: {stats['count']}"
    assert elapsed_ms < 15_000, f"too slow: {elapsed_ms:.0f}ms"
