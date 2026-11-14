"""Live Sim decision-time fills, executable-session exits and netting policy."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from quantara_engine.broker.market_gate import candle_executable_for_instrument
from quantara_engine.domain.types import Instrument
from quantara_engine.execution.timing import (
    decision_time_fill_allowed,
    live_sim_fill_model,
)
from quantara_engine.live_sim.allocator import entry_fill_guard_reason
from quantara_engine.live_sim.decision_quote import resolve_decision_time_candle
from quantara_engine.live_sim.position_policy import evaluate_symbol_position_policy


def _utc(*a) -> datetime:
    return datetime(*a, tzinfo=timezone.utc)


def _instrument(symbol: str, asset_class: str = "stock") -> Instrument:
    return Instrument(
        id=f"{symbol}-id",
        symbol=symbol,
        name=symbol,
        asset_class=asset_class,
        quote_currency="USD",
        quantity_step=Decimal("1"),
        min_quantity=Decimal("1"),
    )


def _c(ts: datetime, close: str):
    p = Decimal(close)
    return SimpleNamespace(timestamp=ts, open=p, high=p, low=p, close=p)


# --- decision-time model ---------------------------------------------------------------


def test_default_fill_model_is_decision_time(monkeypatch):
    monkeypatch.delenv("LIVE_SIM_FILL_MODEL", raising=False)
    assert live_sim_fill_model() == "decision_time"
    monkeypatch.setenv("LIVE_SIM_FILL_MODEL", "next_open")
    assert live_sim_fill_model() == "next_open"


def test_decision_gate_requires_closed_signal_bar():
    sig = _utc(2026, 11, 10, 15, 0)
    ok, reason = decision_time_fill_allowed(
        signal_candle_timestamp=sig,
        timeframe="5m",
        now=sig + timedelta(minutes=3),
        quote_timestamp=sig + timedelta(minutes=2),
    )
    assert not ok and reason == "signal_bar_not_complete"


def test_decision_gate_rejects_stale_signal_and_stale_quote():
    sig = _utc(2026, 11, 10, 15, 0)
    ok, reason = decision_time_fill_allowed(
        signal_candle_timestamp=sig,
        timeframe="5m",
        now=sig + timedelta(minutes=20),
        quote_timestamp=sig + timedelta(minutes=19),
    )
    assert not ok and "stale_signal_age" in reason
    ok, reason = decision_time_fill_allowed(
        signal_candle_timestamp=sig,
        timeframe="5m",
        now=sig + timedelta(minutes=12),
        quote_timestamp=sig + timedelta(minutes=5),
    )
    assert not ok and reason == "decision_quote_stale"


def test_fill_price_is_latest_completed_1m_close_not_signal_open():
    """Entry is priced from data available at decision time, never a stale/future bar."""
    sig = _utc(2026, 11, 10, 15, 0)
    now = sig + timedelta(minutes=5, seconds=40)  # 15:05:40, signal bar closed at 15:05
    one_m = [
        _c(sig + timedelta(minutes=3), "100.10"),
        _c(sig + timedelta(minutes=4), "100.30"),  # closes 15:05 -> available
        _c(sig + timedelta(minutes=5), "100.55"),  # closes 15:06 -> NOT yet available
    ]
    store = MagicMock()
    store.list_recent_candles.return_value = one_m
    candle, reason = resolve_decision_time_candle(
        store,
        _instrument("NVDA"),
        signal_candle_timestamp=sig,
        signal_candle_close=Decimal("100.20"),
        timeframe="5m",
        now=now,
    )
    assert reason is None
    assert candle.open == Decimal("100.30")  # latest *completed* 1m close
    assert candle.timestamp == now  # fill time is decision time, not a bar-start time
    assert candle.quote_timestamp <= now
    assert candle.quote_source == "1m_close"


def test_signal_close_used_when_1m_feed_lags():
    sig = _utc(2026, 11, 10, 15, 0)
    now = sig + timedelta(minutes=5, seconds=20)
    store = MagicMock()
    store.list_recent_candles.return_value = [_c(sig + timedelta(minutes=1), "99.00")]
    candle, reason = resolve_decision_time_candle(
        store,
        _instrument("NVDA"),
        signal_candle_timestamp=sig,
        signal_candle_close=Decimal("100.20"),
        timeframe="5m",
        now=now,
    )
    assert reason is None
    assert candle.open == Decimal("100.20")
    assert candle.quote_source == "signal_5m_close"


def test_no_quote_when_signal_bar_open():
    sig = _utc(2026, 11, 10, 15, 0)
    store = MagicMock()
    store.list_recent_candles.return_value = []
    candle, reason = resolve_decision_time_candle(
        store,
        _instrument("NVDA"),
        signal_candle_timestamp=sig,
        signal_candle_close=Decimal("100.20"),
        timeframe="5m",
        now=sig + timedelta(minutes=2),
    )
    assert candle is None and reason == "signal_bar_not_complete"


# --- fill guard ------------------------------------------------------------------------


def test_fill_guard_blocks_stop_breach_and_drift():
    assert (
        entry_fill_guard_reason(
            direction="long",
            fill_price=Decimal("99.0"),
            entry_ref=Decimal("100"),
            stop_loss=Decimal("99.5"),
        )
        == "ENTRY_STOP_BREACHED"
    )
    assert (
        entry_fill_guard_reason(
            direction="short",
            fill_price=Decimal("100.6"),
            entry_ref=Decimal("100"),
            stop_loss=Decimal("100.5"),
        )
        == "ENTRY_STOP_BREACHED"
    )
    # stop distance 1.0 -> 0.25R drift cap = 0.25
    assert (
        entry_fill_guard_reason(
            direction="long",
            fill_price=Decimal("100.4"),
            entry_ref=Decimal("100"),
            stop_loss=Decimal("99"),
            quote_price=Decimal("100.4"),
        )
        == "ENTRY_PRICE_DRIFT"
    )
    # favourable move and cost-only differences are fine
    assert (
        entry_fill_guard_reason(
            direction="long",
            fill_price=Decimal("100.1"),
            entry_ref=Decimal("100"),
            stop_loss=Decimal("99"),
            quote_price=Decimal("99.9"),
        )
        is None
    )


# --- executable-session exit candles (protective-order market_closed fix) -----------------


def test_premarket_equity_candle_not_executable_but_rth_is():
    amd = _instrument("AMD")
    # 2026-11-10 is a Tuesday. 12:00Z = 07:00 ET (premarket); 15:00Z = 10:00 ET (RTH).
    assert not candle_executable_for_instrument(amd, _utc(2026, 11, 10, 12, 0))
    assert candle_executable_for_instrument(amd, _utc(2026, 11, 10, 15, 0))
    assert not candle_executable_for_instrument(amd, _utc(2026, 11, 10, 22, 0))  # after hours


def test_crypto_candles_always_executable():
    btc = _instrument("BTCUSD", "crypto")
    assert candle_executable_for_instrument(btc, _utc(2026, 11, 14, 3, 0))  # Saturday night


def test_management_candles_skip_premarket():
    from quantara_engine.execution.position_management import _management_candles

    amd = _instrument("AMD")
    opened = _utc(2026, 11, 9, 20, 0)
    pre = SimpleNamespace(timestamp=_utc(2026, 11, 10, 12, 0), open=1, high=1, low=1, close=1)
    rth = SimpleNamespace(timestamp=_utc(2026, 11, 10, 15, 0), open=1, high=1, low=1, close=1)
    position = SimpleNamespace(opened_at=opened)
    out = _management_candles(
        MagicMock(),
        position,
        amd,
        "5m",
        last_managed=None,
        now=_utc(2026, 11, 10, 16, 0),
        prefetched=[pre, rth],
    )
    assert out == [rth]


# --- netting / robot-conflict policy ----------------------------------------------------


def _store_with(open_row=None, pending_row=None):
    store = MagicMock()
    results = [open_row, pending_row]

    def _exec(*_a, **_k):
        res = MagicMock()
        res.mappings.return_value.first.return_value = results.pop(0)
        return res

    store.session.execute.side_effect = _exec
    return store


def test_policy_allows_when_flat():
    v = evaluate_symbol_position_policy(
        _store_with(None, None), symbol="BTCUSD", direction="long"
    )
    assert v.allowed


def test_policy_blocks_same_and_opposite_direction():
    row = {"strategy_slug": "btc-rsi-mean-reversion", "direction": "long"}
    same = evaluate_symbol_position_policy(_store_with(row), symbol="BTCUSD", direction="long")
    assert not same.allowed and same.reason == "SYMBOL_POSITION_OPEN"
    assert same.owner_strategy_slug == "btc-rsi-mean-reversion"
    opp = evaluate_symbol_position_policy(_store_with(row), symbol="BTCUSD", direction="short")
    assert not opp.allowed and opp.reason == "NETTING_CONFLICT_OPPOSITE_SIGNAL"


def test_policy_blocks_pending_accepted_entry():
    pend = {"strategy_slug": "x", "direction": "short"}
    v = evaluate_symbol_position_policy(_store_with(None, pend), symbol="ETHUSD", direction="short")
    assert not v.allowed and v.reason == "SYMBOL_POSITION_OPEN"


# --- order creation never later than its fill (real DB) ---------------------------------


def test_order_created_at_equals_execution_time(broker_test_store):
    """created_at == submitted_at == decision time, so fill (>= +150ms) can never precede it."""
    import uuid

    from sqlalchemy import text

    from quantara_engine.broker.execution_timing import lifecycle_timestamps
    from quantara_engine.broker.realistic_execution import insert_broker_order

    session = broker_test_store.session
    acct = session.execute(text("SELECT id::text FROM broker_accounts LIMIT 1")).scalar()
    inst = session.execute(text("SELECT id::text FROM instruments LIMIT 1")).scalar()
    if not acct or not inst:
        pytest.skip("no seeded broker account / instrument in test DB")
    at = _utc(2026, 11, 10, 15, 5, 40)
    oid = str(uuid.uuid4())
    insert_broker_order(
        session,
        order_id=oid,
        account_id=acct,
        instrument_id=inst,
        direction="long",
        quantity=Decimal("1"),
        idempotency_key=f"t-{oid}",
        client_order_id=f"c-{oid}",
        execution_product="equity_cash",
        execution_model="realistic_broker_v1",
        order_purpose="entry",
        execution_at=at,
        db_strategy_intent_id=None,
        db_strategy_portfolio_id=None,
        reduce_only=False,
    )
    row = session.execute(
        text("SELECT created_at, submitted_at FROM broker_orders WHERE id = CAST(:i AS uuid)"),
        {"i": oid},
    ).mappings().first()
    _, first_fill = lifecycle_timestamps(at)
    assert row["created_at"] == row["submitted_at"] == at
    assert first_fill > row["created_at"]
    session.rollback()
