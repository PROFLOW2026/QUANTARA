"""Live Sim shadow auto-close when broker exit consumed attribution."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from quantara_engine.live_sim.shadow_exit_sync import (
    maybe_close_live_sim_shadow_on_attribution_exhausted,
    reconcile_stale_live_sim_shadows,
)


def _ts() -> datetime:
    return datetime(2026, 9, 24, 21, 10, tzinfo=timezone.utc)


@pytest.fixture
def store():
    return MagicMock(session=MagicMock())


@patch("quantara_engine.live_sim.shadow_exit_sync._exit_fill_by_id")
@patch("quantara_engine.live_sim.shadow_exit_sync.attributed_remaining_quantity")
@patch("quantara_engine.live_sim.shadow_exit_sync._live_sim_position_row")
@patch("quantara_engine.live_sim.shadow_exit_sync._close_shadow_from_evidence")
def test_full_sl_fill_closes_shadow(close_mock, row_mock, attr_mock, fill_mock, store):
    fill_mock.return_value = {"fill_id": "fill-sl", "filled_at": _ts(), "purpose": "sl"}
    row_mock.return_value = {"status": "open", "opportunity_key": "opp"}
    attr_mock.return_value = Decimal("0")
    close_mock.return_value = True

    ok = maybe_close_live_sim_shadow_on_attribution_exhausted(
        store,
        strategy_position_id="pos-1",
        broker_account_id="acct",
        symbol="ETHUSD",
        exit_fill_id="fill-sl",
        closed_at=_ts(),
    )

    assert ok is True
    close_mock.assert_called_once()
    kwargs = close_mock.call_args.kwargs
    assert kwargs["strategy_position_id"] == "pos-1"
    assert kwargs["exit_fill_id"] == "fill-sl"


@patch("quantara_engine.live_sim.shadow_exit_sync._exit_fill_by_id")
@patch("quantara_engine.live_sim.shadow_exit_sync.attributed_remaining_quantity")
@patch("quantara_engine.live_sim.shadow_exit_sync._live_sim_position_row")
@patch("quantara_engine.live_sim.shadow_exit_sync._close_shadow_from_evidence")
def test_full_tp_fill_closes_shadow(close_mock, row_mock, attr_mock, fill_mock, store):
    fill_mock.return_value = {"fill_id": "fill-tp", "filled_at": _ts(), "purpose": "tp"}
    row_mock.return_value = {"status": "open", "opportunity_key": "opp"}
    attr_mock.return_value = Decimal("0")
    close_mock.return_value = True

    ok = maybe_close_live_sim_shadow_on_attribution_exhausted(
        store,
        strategy_position_id="pos-2",
        broker_account_id="acct",
        symbol="BTCUSD",
        exit_fill_id="fill-tp",
        closed_at=_ts(),
    )

    assert ok is True
    close_mock.assert_called_once()


@patch("quantara_engine.live_sim.shadow_exit_sync._sync_shadow_quantity")
@patch("quantara_engine.live_sim.shadow_exit_sync.attributed_remaining_quantity")
@patch("quantara_engine.live_sim.shadow_exit_sync._live_sim_position_row")
@patch("quantara_engine.live_sim.shadow_exit_sync._close_shadow_from_evidence")
def test_partial_tp_keeps_shadow_open(close_mock, row_mock, attr_mock, sync_mock, store):
    row_mock.return_value = {"status": "open", "opportunity_key": "opp"}
    attr_mock.return_value = Decimal("0.02")
    sync_mock.return_value = True

    ok = maybe_close_live_sim_shadow_on_attribution_exhausted(
        store,
        strategy_position_id="pos-3",
        broker_account_id="acct",
        symbol="NVDA",
        exit_fill_id="fill-partial-tp",
        closed_at=_ts(),
    )

    assert ok is False
    close_mock.assert_not_called()
    sync_mock.assert_called_once_with(
        store,
        strategy_position_id="pos-3",
        remaining_qty=Decimal("0.02"),
    )


@patch("quantara_engine.live_sim.shadow_exit_sync._exit_fill_by_id")
@patch("quantara_engine.live_sim.shadow_exit_sync._sync_shadow_quantity")
@patch("quantara_engine.live_sim.shadow_exit_sync.attributed_remaining_quantity")
@patch("quantara_engine.live_sim.shadow_exit_sync._live_sim_position_row")
@patch("quantara_engine.live_sim.shadow_exit_sync._close_shadow_from_evidence")
def test_final_partial_slice_closes_shadow(
    close_mock, row_mock, attr_mock, sync_mock, fill_mock, store
):
    fill_mock.return_value = {"fill_id": "fill-final", "filled_at": _ts(), "purpose": "tp"}
    row_mock.return_value = {"status": "open", "opportunity_key": "opp"}
    attr_mock.side_effect = [Decimal("0.01"), Decimal("0")]
    sync_mock.return_value = True
    close_mock.return_value = True

    ok_partial = maybe_close_live_sim_shadow_on_attribution_exhausted(
        store,
        strategy_position_id="pos-4",
        broker_account_id="acct",
        symbol="NVDA",
        exit_fill_id="fill-partial",
        closed_at=_ts(),
    )
    ok_final = maybe_close_live_sim_shadow_on_attribution_exhausted(
        store,
        strategy_position_id="pos-4",
        broker_account_id="acct",
        symbol="NVDA",
        exit_fill_id="fill-final",
        closed_at=_ts(),
    )

    assert ok_partial is False
    assert ok_final is True
    close_mock.assert_called_once()


@patch("quantara_engine.live_sim.shadow_exit_sync.maybe_close_live_sim_shadow_on_attribution_exhausted")
def test_reconcile_closes_stale_shadow(close_mock, store):
    store.session.execute.return_value.mappings.return_value.all.return_value = [
        {
            "position_id": "pos-5",
            "broker_account_id": "acct",
            "symbol": "COIN",
            "opportunity_key": "opp",
        }
    ]
    close_mock.return_value = True

    closed = reconcile_stale_live_sim_shadows(store)

    assert len(closed) == 1
    assert closed[0]["position_id"] == "pos-5"
    close_mock.assert_called_once()


@patch("quantara_engine.live_sim.shadow_exit_sync._close_shadow_from_evidence")
@patch("quantara_engine.live_sim.shadow_exit_sync.attributed_remaining_quantity")
@patch("quantara_engine.live_sim.shadow_exit_sync._live_sim_position_row")
def test_already_closed_shadow_is_idempotent(row_mock, attr_mock, close_mock, store):
    row_mock.return_value = {"status": "closed", "opportunity_key": "opp"}

    ok = maybe_close_live_sim_shadow_on_attribution_exhausted(
        store,
        strategy_position_id="pos-6",
        broker_account_id="acct",
        symbol="GBPJPY",
    )

    assert ok is True
    attr_mock.assert_not_called()
    close_mock.assert_not_called()


def test_close_shadow_from_evidence_no_fill_insert():
    from quantara_engine.live_sim.shadow_exit_sync import _close_shadow_from_evidence

    store = MagicMock(session=MagicMock())
    store.session.execute.return_value.scalar.side_effect = ["pos-7"]

    ok = _close_shadow_from_evidence(
        store,
        strategy_position_id="pos-7",
        closed_at=_ts(),
        reason="test",
        exit_fill_id="fill-evidence",
    )

    assert ok is True
    sql_calls = [str(c.args[0]) for c in store.session.execute.call_args_list]
    assert not any("INSERT INTO broker_fills" in s for s in sql_calls)
    assert not any("realized_pnl" in s and "INSERT" in s for s in sql_calls)
    store.update_settings.assert_called_once()


@patch("quantara_engine.live_sim.shadow_exit_sync._exit_fill_by_id")
@patch("quantara_engine.live_sim.shadow_exit_sync._close_shadow_from_evidence")
@patch("quantara_engine.live_sim.shadow_exit_sync.attributed_remaining_quantity")
@patch("quantara_engine.live_sim.shadow_exit_sync._live_sim_position_row")
def test_reconcile_quantities_after_close(row_mock, attr_mock, close_mock, fill_mock, store):
    fill_mock.return_value = {"fill_id": "fill-full", "filled_at": _ts(), "purpose": "sl"}
    row_mock.return_value = {"status": "open", "opportunity_key": "opp"}
    attr_mock.return_value = Decimal("0")
    close_mock.return_value = True

    ok = maybe_close_live_sim_shadow_on_attribution_exhausted(
        store,
        strategy_position_id="pos-8",
        broker_account_id="acct",
        symbol="AMD",
        exit_fill_id="fill-full",
        closed_at=_ts(),
    )

    assert ok is True
    close_mock.assert_called_once_with(
        store,
        strategy_position_id="pos-8",
        closed_at=_ts(),
        reason="attribution_exhausted_after_broker_exit",
        exit_fill_id="fill-full",
    )
