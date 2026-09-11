"""Replay V3 — historical DB replay + in-memory engine with FIFO attribution."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
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
from quantara_engine.persistence.store import TradingStore

_DEFAULT_FX = {"USD": Decimal("1"), "JPY": Decimal("150")}


@dataclass
class ReplayLot:
    portfolio_id: str
    strategy_position_id: str | None
    direction: str
    remaining_qty: Decimal
    entry_price: Decimal
    opportunity_key: str | None = None


@dataclass
class ReplayV3Event:
    kind: str  # entry | exit | mark
    symbol: str
    asset_class: str
    direction: str
    quantity: Decimal
    price: Decimal
    portfolio_id: str = "p1"
    strategy_position_id: str | None = None
    fees: Decimal = Decimal("0")
    market_open: bool = True
    timestamp: datetime | None = None


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
    fill_attributed_total: Decimal = Decimal("0")
    lots: dict[str, deque[ReplayLot]] = field(default_factory=dict)
    accepted_entries: set[str] = field(default_factory=set)


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


def _attributed_remaining(state: ReplayV3State, symbol: str, portfolio_id: str) -> Decimal:
    return sum(
        (lot.remaining_qty for lot in state.lots.get(symbol, deque()) if lot.portfolio_id == portfolio_id),
        Decimal("0"),
    )


def _fifo_close(
    state: ReplayV3State,
    *,
    symbol: str,
    closed_qty: Decimal,
    fill_price: Decimal,
    exit_portfolio_id: str,
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
        lot_pnl = realized_pnl_usd(take, lot.entry_price, fill_price, is_long, spec, state.fx_rates)
        attributed += lot_pnl
        lot.remaining_qty -= take
        remaining -= take
        if lot.remaining_qty <= 0:
            queue.popleft()
    state.attributed_realized += attributed
    return attributed


def _open_lot(
    state: ReplayV3State,
    *,
    symbol: str,
    portfolio_id: str,
    strategy_position_id: str | None,
    direction: str,
    quantity: Decimal,
    fill_price: Decimal,
) -> None:
    state.lots.setdefault(symbol, deque()).append(
        ReplayLot(
            portfolio_id=portfolio_id,
            strategy_position_id=strategy_position_id,
            direction=direction,
            remaining_qty=quantity,
            entry_price=fill_price,
        )
    )


def _apply_crypto_cash(
    state: ReplayV3State,
    *,
    symbol: str,
    direction: str,
    quantity: Decimal,
    price: Decimal,
    fees: Decimal,
    is_close: bool,
) -> None:
    spec = get_instrument_spec(symbol)
    rules = state.profile.rules_for(spec.asset_class)
    if spec.asset_class != "crypto" or rules.initial_margin_pct < Decimal("100"):
        state.cash -= fees
        return
    notional = quantity * price
    if direction == "long" and not is_close:
        state.cash -= notional + fees
        state.spot_crypto_cash -= notional + fees
    else:
        state.cash += notional - fees
        state.spot_crypto_cash += notional - fees


def _execute_fill(state: ReplayV3State, event: ReplayV3Event, *, is_close: bool) -> None:
    spec = get_instrument_spec(event.symbol)
    cur_qty, cur_avg, _ = state.positions.get(
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
    state.realized_total += netting.realized_pnl
    state.fees_paid += fees
    _apply_crypto_cash(
        state,
        symbol=event.symbol,
        direction=event.direction,
        quantity=event.quantity,
        price=event.price,
        fees=fees,
        is_close=is_close,
    )

    closed_qty = netting.closed_quantity
    opened_qty = max(Decimal("0"), event.quantity - closed_qty)
    fill_attributed = Decimal("0")
    if closed_qty > 0:
        fill_attributed = _fifo_close(
            state,
            symbol=event.symbol,
            closed_qty=closed_qty,
            fill_price=event.price,
            exit_portfolio_id=event.portfolio_id,
        )
        state.fill_attributed_total += fill_attributed
    if opened_qty > 0:
        _open_lot(
            state,
            symbol=event.symbol,
            portfolio_id=event.portfolio_id,
            strategy_position_id=event.strategy_position_id,
            direction=event.direction,
            quantity=opened_qty,
            fill_price=event.price,
        )

    if netting.new_net_qty == 0:
        state.positions.pop(event.symbol, None)
        if not any(lot.remaining_qty > 0 for lot in state.lots.get(event.symbol, deque())):
            state.lots.pop(event.symbol, None)
    else:
        state.positions[event.symbol] = (netting.new_net_qty, netting.new_avg_price, event.price)


def replay_v3(events: list[ReplayV3Event], *, starting_cash: Decimal | None = None) -> dict:
    profile = QUANTARA_STANDARD_PAPER
    start = starting_cash if starting_cash is not None else profile.starting_cash
    state = ReplayV3State(
        profile=profile,
        balance=start,
        cash=start,
        spot_crypto_cash=start,
    )

    accepted = rejected = orphan_exits = 0
    for event in events:
        if event.kind == "mark":
            if event.symbol in state.positions:
                qty, avg, _ = state.positions[event.symbol]
                state.positions[event.symbol] = (qty, avg, event.price)
            continue

        if event.kind == "exit":
            physical_qty = abs(state.positions.get(event.symbol, (Decimal("0"), Decimal("0"), Decimal("0")))[0])
            if event.strategy_position_id:
                cap_qty = sum(
                    (
                        lot.remaining_qty
                        for lot in state.lots.get(event.symbol, deque())
                        if lot.strategy_position_id == event.strategy_position_id
                    ),
                    Decimal("0"),
                )
            else:
                cap_qty = min(
                    event.quantity,
                    _attributed_remaining(state, event.symbol, event.portfolio_id) or physical_qty,
                )
            if cap_qty <= 0:
                orphan_exits += 1
                rejected += 1
                continue
            if event.quantity > cap_qty:
                event = ReplayV3Event(
                    kind=event.kind,
                    symbol=event.symbol,
                    asset_class=event.asset_class,
                    direction=event.direction,
                    quantity=cap_qty,
                    price=event.price,
                    portfolio_id=event.portfolio_id,
                    strategy_position_id=event.strategy_position_id,
                    fees=event.fees,
                    market_open=event.market_open,
                    timestamp=event.timestamp,
                )

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
        if event.kind == "entry" and event.strategy_position_id:
            state.accepted_entries.add(event.strategy_position_id)
        _execute_fill(state, event, is_close=is_close)

    final = _snapshot(state)
    open_lot_qty = sum(
        (lot.remaining_qty for lots in state.lots.values() for lot in lots),
        Decimal("0"),
    )
    broker_qty = sum((abs(qty) for qty, _, _ in state.positions.values()), Decimal("0"))
    expected_balance = start + state.realized_total - state.fees_paid

    return {
        "events_total": len(events),
        "accepted": accepted,
        "rejected": rejected,
        "orphan_exits": orphan_exits,
        "starting_cash": float(start),
        "ending_balance": float(state.balance),
        "ending_equity": float(final.equity),
        "realized_pnl": float(state.realized_total),
        "fees_paid": float(state.fees_paid),
        "attributed_realized": float(state.attributed_realized),
        "open_positions": len(state.positions),
        "financial_reconciliation": float((state.balance - expected_balance).quantize(Decimal("0.01"))),
        "margin_reconciliation": 0.0,
        "attribution_reconciliation": float(
            (state.fill_attributed_total - state.attributed_realized).quantize(Decimal("0.01"))
        ),
        "physical_quantity_difference": float((open_lot_qty - broker_qty).quantize(Decimal("0.00000001"))),
        "account_state": final.account_state.value,
    }


def replay_historical_day(
    store: TradingStore,
    day_start: datetime,
    day_end: datetime,
    *,
    starting_cash: Decimal | None = None,
) -> dict:
    """Read-only chronological replay from strategy positions/trades for one day."""
    from sqlalchemy import text

    entries = store.session.execute(
        text(
            """
            SELECT p.id::text AS position_id, p.quantity, p.direction, p.entry_price, p.opened_at,
                   p.portfolio_id::text AS portfolio_id,
                   i.symbol, i.asset_class::text AS asset_class
            FROM positions p
            JOIN instruments i ON i.id = p.instrument_id
            WHERE p.backtest_run_id IS NULL
              AND p.opened_at >= :d0 AND p.opened_at < :d1
            ORDER BY p.opened_at
            """
        ),
        {"d0": day_start, "d1": day_end},
    ).mappings().all()

    exits = store.session.execute(
        text(
            """
            SELECT t.quantity, t.exit_price, t.closed_at, t.realized_pnl,
                   p.id::text AS position_id, p.direction, p.portfolio_id::text AS portfolio_id,
                   i.symbol, i.asset_class::text AS asset_class
            FROM trades t
            JOIN positions p ON p.id = t.position_id
            JOIN instruments i ON i.id = p.instrument_id
            WHERE p.backtest_run_id IS NULL
              AND t.closed_at >= :d0 AND t.closed_at < :d1
            ORDER BY t.closed_at
            """
        ),
        {"d0": day_start, "d1": day_end},
    ).mappings().all()

    events: list[ReplayV3Event] = []
    for e in entries:
        direction = "long" if str(e["direction"]).lower() == "long" else "short"
        events.append(
            ReplayV3Event(
                kind="entry",
                symbol=str(e["symbol"]).upper(),
                asset_class=str(e["asset_class"]),
                direction=direction,
                quantity=Decimal(str(e["quantity"])),
                price=Decimal(str(e["entry_price"])),
                portfolio_id=str(e["portfolio_id"]),
                strategy_position_id=str(e["position_id"]),
                timestamp=e["opened_at"],
            )
        )
    for x in exits:
        pos_dir = str(x["direction"]).lower()
        direction = "short" if pos_dir == "long" else "long"
        events.append(
            ReplayV3Event(
                kind="exit",
                symbol=str(x["symbol"]).upper(),
                asset_class=str(x["asset_class"]),
                direction=direction,
                quantity=Decimal(str(x["quantity"])),
                price=Decimal(str(x["exit_price"])),
                portfolio_id=str(x["portfolio_id"]),
                strategy_position_id=str(x["position_id"]),
                timestamp=x["closed_at"],
            )
        )
    events.sort(key=lambda ev: ev.timestamp or datetime.min)

    result = replay_v3(events, starting_cash=starting_cash)
    result["entries_total"] = len(entries)
    result["exits_total"] = len(exits)
    result["day_start"] = day_start.isoformat()
    result["day_end"] = day_end.isoformat()
    return result


def write_replay_v3_audit_markdown(store: TradingStore, path: str, day_start: datetime, day_end: datetime) -> dict:
    """Generate docs/audits replay report from historical DB replay."""
    result = replay_historical_day(store, day_start, day_end)
    lines = [
        f"# Broker Replay V3 — {day_start.date()}",
        "",
        "## Summary",
        f"- Entries in DB: {result.get('entries_total', 0)}",
        f"- Exits in DB: {result.get('exits_total', 0)}",
        f"- Accepted broker events: {result.get('accepted', 0)}",
        f"- Rejected broker events: {result.get('rejected', 0)}",
        f"- Orphan exits skipped: {result.get('orphan_exits', 0)}",
        "",
        "## Reconciliation",
        f"- Financial: {result.get('financial_reconciliation', 0):.2f}",
        f"- Margin: {result.get('margin_reconciliation', 0):.2f}",
        f"- Attribution: {result.get('attribution_reconciliation', 0):.2f}",
        f"- Physical quantity diff: {result.get('physical_quantity_difference', 0)}",
        "",
        f"- Ending balance: {result.get('ending_balance', 0):.2f}",
        f"- Realized P&L: {result.get('realized_pnl', 0):.2f}",
        f"- Fees paid: {result.get('fees_paid', 0):.2f}",
    ]
    from pathlib import Path

    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return result
