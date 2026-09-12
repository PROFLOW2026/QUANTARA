"""Replay V5 unit tests — basis carry, mark timing, unknown session."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from quantara_engine.broker.replay_v5 import (
    ReplayV5Event,
    ReplayV5State,
    _event_sort_key,
    replay_v5,
)
from quantara_engine.market_data.polling import bar_close_timestamp


def test_netting_basis_carry_reconciles_total_pnl():
    """Physical vs attributed realized may differ; total P&L must still reconcile."""
    state = ReplayV5State(starting_cash=Decimal("10000"), balance=Decimal("10000"), cash=Decimal("10000"))
    events = [
        ReplayV5Event(
            kind="entry",
            symbol="NVDA",
            asset_class="stock",
            direction="long",
            quantity=Decimal("50"),
            price=Decimal("100"),
            portfolio_id="a",
            strategy_position_id="pos-a",
            market_open=True,
            timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
        ),
        ReplayV5Event(
            kind="entry",
            symbol="NVDA",
            asset_class="stock",
            direction="long",
            quantity=Decimal("50"),
            price=Decimal("110"),
            portfolio_id="c",
            strategy_position_id="pos-c",
            market_open=True,
            timestamp=datetime(2026, 1, 1, 1, tzinfo=timezone.utc),
        ),
        ReplayV5Event(
            kind="exit",
            symbol="NVDA",
            asset_class="stock",
            direction="short",
            quantity=Decimal("50"),
            price=Decimal("120"),
            portfolio_id="c",
            strategy_position_id="pos-c",
            market_open=True,
            order_purpose="close",
            timestamp=datetime(2026, 1, 1, 2, tzinfo=timezone.utc),
        ),
    ]
    result = replay_v5(events, state=state)
    assert result["realized_basis_difference"] == 250.0
    assert result["total_pnl_reconciliation"] == 0.0
    assert result["basis_carry_check"] == 0.0
    assert result["expected_netting_basis_carry"] is True


def test_mark_events_sort_after_trades_at_bar_open():
    bar_open = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    bar_close = bar_close_timestamp(bar_open, "5m")
    trade = ReplayV5Event(
        kind="entry",
        symbol="NVDA",
        asset_class="stock",
        direction="long",
        quantity=Decimal("1"),
        price=Decimal("100"),
        timestamp=bar_open,
    )
    mark = ReplayV5Event(
        kind="mark",
        symbol="NVDA",
        asset_class="stock",
        direction="long",
        quantity=Decimal("0"),
        price=Decimal("105"),
        timestamp=bar_close,
    )
    assert _event_sort_key(trade) < _event_sort_key(mark)
    assert bar_close == bar_open + timedelta(minutes=5)


def test_unknown_market_session_rejected_not_treated_as_open():
    events = [
        ReplayV5Event(
            kind="entry",
            symbol="NVDA",
            asset_class="stock",
            direction="long",
            quantity=Decimal("1"),
            price=Decimal("100"),
            market_open=None,
            timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
        ),
    ]
    result = replay_v5(events)
    assert result["broker_accepted_entries"] == 0
    assert result["unknown_session_count"] == 1
    assert result["rejection_counts"].get("unknown_market_session") == 1


def test_non_crypto_cash_includes_gross_realized_minus_fees():
    events = [
        ReplayV5Event(
            kind="entry",
            symbol="NVDA",
            asset_class="stock",
            direction="long",
            quantity=Decimal("10"),
            price=Decimal("100"),
            market_open=True,
            timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
        ),
        ReplayV5Event(
            kind="exit",
            symbol="NVDA",
            asset_class="stock",
            direction="short",
            quantity=Decimal("10"),
            price=Decimal("110"),
            fees=Decimal("2"),
            market_open=True,
            order_purpose="close",
            timestamp=datetime(2026, 1, 1, 1, tzinfo=timezone.utc),
        ),
    ]
    result = replay_v5(events)
    assert result["ending_cash"] == float(Decimal("320000") + Decimal("100") - Decimal("2"))
    assert result["cash_reconciliation"] == 0.0
