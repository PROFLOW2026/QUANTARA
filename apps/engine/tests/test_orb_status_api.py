"""ORB status API — timestamp field regression."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

from quantara_engine.competition.orb_service import build_orb_status
from quantara_engine.domain.types import DecisionLogEntry, DecisionType


def test_build_orb_status_uses_candle_timestamp():
    store = MagicMock()
    store.is_orb_competition_enabled.return_value = True
    store.list_orb_competition_entries.return_value = []
    store.get_instrument_by_symbol.return_value = MagicMock(id="inst-spy")
    store.list_recent_candles.return_value = []

    ts = datetime(2026, 9, 9, 14, 35, tzinfo=timezone.utc)
    store.latest_decision_for_instrument.return_value = DecisionLogEntry(
        id="d1",
        strategy_instance_id="i1",
        instrument_id="inst-spy",
        candle_timestamp=ts,
        decision_type=DecisionType.BUY_SIGNAL,
        message="breakout_long_confirmed",
    )

    payload = build_orb_status(store, symbol="SPY")
    assert payload["assets"]["SPY"]["latest_signal_at"] == ts.isoformat()
