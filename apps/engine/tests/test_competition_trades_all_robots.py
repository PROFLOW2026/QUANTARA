"""Competition /trades must include all robots — same scope as asset closed_trades cards."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

from quantara_engine.persistence.store import TradingStore


def test_list_competition_trades_all_uses_all_robot_portfolios_not_robot_a_only():
    store = TradingStore.__new__(TradingStore)
    store.session = MagicMock()

    robot_a = [{"portfolio": SimpleNamespace(id="a1")}]
    robot_b = [{"portfolio": SimpleNamespace(id="b1")}]
    cde = [
        {"portfolio": SimpleNamespace(id="c1")},
        {"portfolio": SimpleNamespace(id="d1")},
        {"portfolio": SimpleNamespace(id="e1")},
    ]
    combined = robot_a + robot_b + cde
    store.list_all_competition_entries = MagicMock(return_value=(robot_a, robot_b, combined))
    store.list_competition_portfolios = MagicMock(
        return_value=[e["portfolio"] for e in robot_a]
    )

    seen: list[str] = []

    def fake_list_trades(portfolio_id, limit=100, offset=0, paper_only=False):
        seen.append(portfolio_id)
        return [
            SimpleNamespace(
                id=f"t-{portfolio_id}",
                closed_at=datetime(2026, 9, 14, tzinfo=timezone.utc),
            )
        ]

    store.list_trades = fake_list_trades  # type: ignore[method-assign]

    rows = TradingStore.list_competition_trades_all(store, limit=500)
    assert seen == ["a1", "b1", "c1", "d1", "e1"]
    assert len(rows) == 5
    store.list_competition_portfolios.assert_not_called()
