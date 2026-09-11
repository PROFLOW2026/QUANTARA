"""Replay V3 — in-memory broker lifecycle with FIFO attribution reconciliation."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from quantara_engine.broker.account import build_account_snapshot
from quantara_engine.broker.instruments import get_instrument_spec
from quantara_engine.broker.margin import initial_margin_for_notional, maintenance_margin_for_notional
from quantara_engine.broker.netting import apply_fill_with_realized_pnl
from quantara_engine.broker.pnl import realized_pnl_usd
from quantara_engine.broker.pre_trade import evaluate_broker_order
from quantara_engine.broker.profile import QUANTARA_STANDARD_PAPER
from quantara_engine.broker.types import BrokerOrderRequest, PositionMode

_DEFAULT_FX = {"USD": Decimal("1"), "JPY": Decimal("150")}


@dataclass
class ReplayLot:
    portfolio_id: str
    direction: str
    remaining_qty: Decimal
    entry_price: Decimal


@dataclass
class ReplayV3Event:
    kind: str  # entry | exit | mark
    symbol: str
    asset_class: str
    direction: str
    quantity: Decimal
    price: Decimal
    portfolio_id: str = "p1"
    fees: Decimal = Decimal("0")
    market_open: bool = True


@dataclass
class ReplayV3State:
    profile: Any = QUANTARA_STANDARD_PAPER
    fx_rates: dict[str, Decimal] = field(default_factory=lambda: dict(_DEFAULT_FX))
    positions: dict[str, tuple[Decimal, Decimal, Decimal]] = field(default_factory=dict)
    balance: Decimal = Decimal("320000")
    cash: Decimal = Decimal("320000")
    spot_crypto_cash: Decimal = Decimal("320000")
    realized_total: Decimal = Decimal("0")
    fees_paid: Decimal = Decimal("0")
    attributed_realized: Decimal = Decimal("0")
    lots: dict[str, deque[ReplayLot]] = field(default_factory=dict)
    stored_initial_margin: Decimal = Decimal("0")
    stored_maintenance: Decimal = Decimal("0")


def _snapshot(state: ReplayV3State):
    return build_account_snapshot(
        cash=state.cash,
        balance=state.balance,
        realized_pnl=state.realized_total,
        positions=state.positions,
        fx_rates=state.fx_rates,
        profile=state.profile,
        spot_crypto_cash=state.spot_crypto_cash,
    )


def _fifo_attribute(
    state: ReplayV3State,
    *,
    symbol: str,
    closed_qty: Decimal,
    exit_price: Decimal,
    exit_direction: str,
) -> Decimal:
    spec = get_instrument_spec(symbol)
    remaining = closed_qty
    attributed = Decimal("0")
    queue = state.lots.setdefault(symbol, deque())
    while remaining > 0 and queue:
        lot = queue[0]
        take = min(lot.remaining_qty, remaining)
        if take <= 0:
            queue.popleft()
            continue
        is_long = lot.direction == "long"
        lot_pnl = realized_pnl_usd(take, lot.entry_price, exit_price, is_long, spec, state.fx_rates)
        attributed += lot_pnl
        lot.remaining_qty -= take
        remaining -= take
        if lot.remaining_qty <= 0:
            queue.popleft()
    state.attributed_realized += attributed
    return attributed


def _apply_entry_lot(state: ReplayV3State, event: ReplayV3Event) -> None:
    state.lots.setdefault(event.symbol, deque()).append(
        ReplayLot(
            portfolio_id=event.portfolio_id,
            direction=event.direction,
            remaining_qty=event.quantity,
            entry_price=event.price,
        )
    )


def _execute_fill(state: ReplayV3State, event: ReplayV3Event, *, is_close: bool) -> None:
    spec = get_instrument_spec(event.symbol)
    cur_qty, cur_avg, cur_mark = state.positions.get(
        event.symbol, (Decimal("0"), Decimal("0"), event.price)
    )
    netting = apply_fill_with_realized_pnl(
        cur_qty,
        cur_avg,
        event.quantity,
        event.price,
        event.direction,
        mode=PositionMode.NETTING,
        spec=spec,
        fx_rates=state.fx_rates,
    )
    fees = event.fees.quantize(Decimal("0.0001"))
    state.balance += netting.realized_pnl - fees
    state.cash -= fees
    state.realized_total += netting.realized_pnl
    state.fees_paid += fees

    if netting.closed_quantity > 0:
        _fifo_attribute(
            state,
            symbol=event.symbol,
            closed_qty=netting.closed_quantity,
            exit_price=event.price,
            exit_direction=event.direction,
        )

    if not is_close and netting.closed_quantity == 0:
        _apply_entry_lot(state, event)

    if netting.new_net_qty == 0:
        state.positions.pop(event.symbol, None)
        state.lots.pop(event.symbol, None)
    else:
        state.positions[event.symbol] = (netting.new_net_qty, netting.new_avg_price, event.price)


def replay_v3(events: list[ReplayV3Event], *, starting_cash: Decimal | None = None) -> dict:
    """
    Simulate accepted broker fills with physical FIFO attribution.

    Returns reconciliation metrics — all differences should be 0.00 when correct.
    """
    profile = QUANTARA_STANDARD_PAPER
    start = starting_cash if starting_cash is not None else profile.starting_cash
    state = ReplayV3State(
        profile=profile,
        balance=start,
        cash=start,
        spot_crypto_cash=start,
    )

    accepted = rejected = 0
    for event in events:
        if event.kind == "mark":
            if event.symbol in state.positions:
                qty, avg, _ = state.positions[event.symbol]
                state.positions[event.symbol] = (qty, avg, event.price)
            continue

        snap = _snapshot(state)
        is_close = event.kind == "exit"
        req = BrokerOrderRequest(
            symbol=event.symbol,
            asset_class=event.asset_class,
            direction=event.direction,
            quantity=event.quantity,
            mark_price=event.price,
            is_close=is_close,
            market_open=event.market_open,
            data_fresh=True,
        )
        decision = evaluate_broker_order(snap, profile, req, state.fx_rates)
        if not decision.accepted:
            rejected += 1
            continue
        accepted += 1
        _execute_fill(state, event, is_close=is_close)

    final = _snapshot(state)
    stored_im = Decimal("0")
    stored_mm = Decimal("0")
    open_lot_qty = Decimal("0")
    for symbol, (qty, avg, mark) in state.positions.items():
        spec = get_instrument_spec(symbol)
        rules = profile.rules_for(spec.asset_class)
        from quantara_engine.broker.margin import quote_notional_usd

        notional = quote_notional_usd(qty, mark, spec, state.fx_rates)
        stored_im += initial_margin_for_notional(notional, rules)
        stored_mm += maintenance_margin_for_notional(notional, rules)
    for lots in state.lots.values():
        for lot in lots:
            open_lot_qty += lot.remaining_qty

    broker_qty = sum(abs(qty) for qty, _, _ in state.positions.values())
    expected_balance = start + state.realized_total - state.fees_paid
    financial_diff = (state.balance - expected_balance).quantize(Decimal("0.01"))
    margin_diff = (
        final.initial_margin_used - stored_im + (final.maintenance_margin_required - stored_mm)
    ).quantize(Decimal("0.01"))
    attr_diff = (state.realized_total - state.attributed_realized).quantize(Decimal("0.01"))
    qty_diff = (open_lot_qty - broker_qty).quantize(Decimal("0.00000001"))

    return {
        "events_total": len(events),
        "accepted": accepted,
        "rejected": rejected,
        "starting_cash": float(start),
        "ending_balance": float(state.balance),
        "ending_equity": float(final.equity),
        "realized_pnl": float(state.realized_total),
        "fees_paid": float(state.fees_paid),
        "attributed_realized": float(state.attributed_realized),
        "open_positions": len(state.positions),
        "financial_reconciliation": float(financial_diff),
        "margin_reconciliation": float(margin_diff),
        "attribution_reconciliation": float(attr_diff),
        "physical_quantity_difference": float(qty_diff),
        "account_state": final.account_state.value,
        "gross_leverage": float(final.gross_leverage),
    }
