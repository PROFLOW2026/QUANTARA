"""Today stats: actionable entry signals vs raw strategy opinions."""

from unittest.mock import MagicMock, patch

from quantara_engine.persistence.store import TradingStore


def test_entry_signals_uses_intents_strategy_signals_uses_decisions():
    store = TradingStore(MagicMock())
    responses = iter([15, 10, 0, 10, 0, 0, 0.0, 0.0])

    def fake_scalar(_stmt):
        return next(responses)

    with patch.object(
        store,
        "list_competition_instance_ids",
        return_value=["00000000-0000-0000-0000-000000000001"],
    ):
        with patch.object(
            store,
            "list_competition_entries",
            return_value=[{"portfolio": MagicMock(id="00000000-0000-0000-0000-000000000002")}],
        ):
            store.session.scalar = fake_scalar
            stats = store.get_competition_today_stats()

    assert stats["market_checks_today"] == 15
    assert stats["strategy_signals_today"] == 10
    assert stats["entry_signals_today"] == 0
