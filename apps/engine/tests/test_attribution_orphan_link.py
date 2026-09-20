"""Orphan attribution lot linking via intent chain."""

from __future__ import annotations

from unittest.mock import MagicMock, call, patch

from quantara_engine.broker.attribution import link_orphan_lots_via_intent_chain


def test_link_orphan_lots_via_intent_chain_calls_link_for_each_row():
    session = MagicMock()
    row1 = {"lot_id": "lot-1", "fill_id": "fill-1", "strategy_position_id": "pos-1"}
    row2 = {"lot_id": "lot-2", "fill_id": "fill-2", "strategy_position_id": "pos-2"}
    session.execute.return_value.mappings.return_value.all.return_value = [row1, row2]
    store = MagicMock(session=session)

    with patch(
        "quantara_engine.broker.attribution.link_strategy_position_to_fill"
    ) as link_mock:
        linked = link_orphan_lots_via_intent_chain(store)

    assert linked == [row1, row2]
    assert link_mock.call_count == 2
    link_mock.assert_has_calls(
        [
            call(store, broker_fill_id="fill-1", strategy_position_id="pos-1"),
            call(store, broker_fill_id="fill-2", strategy_position_id="pos-2"),
        ]
    )


def test_link_orphan_lots_empty_when_no_rows():
    session = MagicMock()
    session.execute.return_value.mappings.return_value.all.return_value = []
    store = MagicMock(session=session)

    with patch(
        "quantara_engine.broker.attribution.link_strategy_position_to_fill"
    ) as link_mock:
        linked = link_orphan_lots_via_intent_chain(store)

    assert linked == []
    link_mock.assert_not_called()
