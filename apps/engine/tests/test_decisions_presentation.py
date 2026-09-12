"""Presentation-layer tests for competition decision API enrichment."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

from quantara_engine.api.routes import decisions


def test_competition_decisions_include_portfolio_context():
    store = MagicMock()
    store.resolve_instrument_display_symbols.return_value = {"inst-eth": "ETH/USD"}
    store.list_competition_instance_ids.return_value = ["ia1", "ia2"]
    store.build_instance_strategy_identity_map.return_value = {
        "ia1": {
            "robot_label": "Robot B",
            "strategy_slug": "opening-range-breakout",
            "strategy_name": "Opening Range Breakout",
            "portfolio_id": "pf-1",
            "portfolio_name": "ETH/USD 5 דקות — מאוזן",
            "risk_slug": "balanced",
            "risk_name_he": "מאוזן",
            "timeframe": "5m",
        }
    }

    decision = MagicMock()
    decision.id = "d1"
    decision.strategy_instance_id = "ia1"
    decision.instrument_id = "inst-eth"
    decision.candle_timestamp = datetime(2026, 9, 13, 1, 20, tzinfo=timezone.utc)
    decision.decision_type.value = "no_setup"
    decision.message = "opening_range_not_complete"
    decision.metadata = {}

    store.list_competition_decisions.return_value = [decision]

    rows = decisions(store, limit=50)
    assert len(rows) == 1
    row = rows[0]
    assert row["instrument"] == "ETH/USD"
    assert row["portfolio_name"] == "ETH/USD 5 דקות — מאוזן"
    assert row["risk_slug"] == "balanced"
    assert row["risk_name_he"] == "מאוזן"
    assert row["timeframe"] == "5m"
