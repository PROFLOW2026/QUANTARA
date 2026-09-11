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
    gross_realized: Decimal = Decimal("0")
    fees_paid: Decimal = Decimal("0")
    net_realized: Decimal = Decimal("0")
    attributed_realized: Decimal = Decimal("0")
    fill_attributed_total: Decimal = Decimal("0")
    lots: dict[str, deque[ReplayLot]] = field(default_factory=dict)
    accepted_entries: set[str] = field(default_factory=set)
    max_gross_leverage: Decimal = Decimal("0")
    max_margin_used: Decimal = Decimal("0")
    historical_entries: int = 0
    broker_accepted_entries: int = 0
    broker_rejected_entries: int = 0
    historical_exits: int = 0
    valid_physical_exits: int = 0


def _snapshot(state: ReplayV3State):
    return build_account_snapshot(
        cash=state.cash,
        balance=state.balance,
        realized_pnl=state.net_realized,
        positions=state.positions,
        fx_rates=state.fx_rates,
        profile=state.profile,
        spot_crypto_cash=state.spot_crypto_cash,
    )


def _track_margin_peaks(state: ReplayV3State) -> None:
    snap = _snapshot(state)
    if snap.gross_leverage > state.max_gross_leverage:
        state.max_gross_leverage = snap.gross_leverage
    if snap.initial_margin_used > state.max_margin_used:
        state.max_margin_used = snap.initial_margin_used


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
    if quantity <= 0:
        return
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
    gross_pnl = netting.realized_pnl
    net_pnl = gross_pnl - fees
    state.balance += net_pnl
    state.gross_realized += gross_pnl
    state.fees_paid += fees
    state.net_realized += net_pnl
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
    _track_margin_peaks(state)


def replay_v3(
    events: list[ReplayV3Event],
    *,
    starting_cash: Decimal | None = None,
    initial_state: ReplayV3State | None = None,
) -> dict:
    profile = QUANTARA_STANDARD_PAPER
    start = starting_cash if starting_cash is not None else profile.starting_cash
    state = initial_state or ReplayV3State(
        profile=profile,
        balance=start,
        cash=start,
        spot_crypto_cash=start,
    )
    if initial_state is not None:
        start = state.balance

    accepted = rejected = orphan_exits = 0
    for event in events:
        if event.kind == "mark":
            if event.symbol in state.positions:
                qty, avg, _ = state.positions[event.symbol]
                state.positions[event.symbol] = (qty, avg, event.price)
            _track_margin_peaks(state)
            continue

        if event.kind == "entry":
            state.historical_entries += 1
        elif event.kind == "exit":
            state.historical_exits += 1

        if event.kind == "exit":
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
                    _attributed_remaining(state, event.symbol, event.portfolio_id),
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
            state.valid_physical_exits += 1

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
            if event.kind == "entry":
                state.broker_rejected_entries += 1
            continue
        accepted += 1
        if event.kind == "entry":
            state.broker_accepted_entries += 1
            if event.strategy_position_id:
                state.accepted_entries.add(event.strategy_position_id)
        _execute_fill(state, event, is_close=is_close)

    final = _snapshot(state)
    open_lot_qty = sum(
        (lot.remaining_qty for lots in state.lots.values() for lot in lots),
        Decimal("0"),
    )
    broker_qty = sum((abs(qty) for qty, _, _ in state.positions.values()), Decimal("0"))
    expected_balance = start + state.net_realized
    margin_reconciliation = (final.equity - final.balance - final.unrealized_pnl).quantize(Decimal("0.01"))

    return {
        "events_total": len(events),
        "accepted": accepted,
        "rejected": rejected,
        "orphan_exits": orphan_exits,
        "historical_entries": state.historical_entries,
        "broker_accepted_entries": state.broker_accepted_entries,
        "broker_rejected_entries": state.broker_rejected_entries,
        "historical_exits": state.historical_exits,
        "valid_physical_exits": state.valid_physical_exits,
        "starting_cash": float(start),
        "ending_balance": float(state.balance),
        "ending_equity": float(final.equity),
        "gross_realized_pnl": float(state.gross_realized),
        "net_realized_pnl": float(state.net_realized),
        "realized_pnl": float(state.net_realized),
        "fees_paid": float(state.fees_paid),
        "attributed_realized": float(state.attributed_realized),
        "open_positions": len(state.positions),
        "gross_exposure": float(final.gross_exposure),
        "net_exposure": float(final.net_exposure),
        "max_gross_leverage": float(state.max_gross_leverage),
        "max_margin_used": float(state.max_margin_used),
        "financial_reconciliation": float((state.balance - expected_balance).quantize(Decimal("0.01"))),
        "margin_reconciliation": float(margin_reconciliation),
        "attribution_reconciliation": float(
            (state.fill_attributed_total - state.attributed_realized).quantize(Decimal("0.01"))
        ),
        "physical_quantity_difference": float((open_lot_qty - broker_qty).quantize(Decimal("0.00000001"))),
        "account_state": final.account_state.value,
    }


def _resolve_fx_rates(store: TradingStore) -> dict[str, Decimal]:
    from quantara_engine.portfolio.currency import resolve_dashboard_fx_rates

    try:
        fx = resolve_dashboard_fx_rates(store, {"USD", "JPY"})
        rates = dict(_DEFAULT_FX)
        rates.update({k: v for k, v in fx.quote_per_usd.items()})
        return rates
    except Exception:
        return dict(_DEFAULT_FX)


def _market_open_for_row(store: TradingStore, symbol: str, asset_class: str, at: datetime) -> bool:
    from quantara_engine.broker.market_gate import market_open_for_instrument
    from quantara_engine.domain.types import Instrument

    try:
        instrument = store.get_instrument_by_symbol(symbol) or Instrument(
            id="replay",
            symbol=symbol,
            name=symbol,
            asset_class=asset_class,
        )
        return market_open_for_instrument(instrument, at)
    except Exception:
        return True


def replay_historical_day(
    store: TradingStore,
    day_start: datetime,
    day_end: datetime,
    *,
    starting_cash: Decimal | None = None,
) -> dict:
    """Read-only chronological replay from strategy positions/trades for one day."""
    from sqlalchemy import text

    fx_rates = _resolve_fx_rates(store)

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
            SELECT t.quantity, t.exit_price, t.closed_at, t.realized_pnl, t.fees_total,
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
        ts = e["opened_at"]
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
                timestamp=ts,
                market_open=_market_open_for_row(store, str(e["symbol"]), str(e["asset_class"]), ts),
            )
        )
    for x in exits:
        pos_dir = str(x["direction"]).lower()
        direction = "short" if pos_dir == "long" else "long"
        ts = x["closed_at"]
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
                fees=Decimal(str(x.get("fees_total") or "0")),
                timestamp=ts,
                market_open=_market_open_for_row(store, str(x["symbol"]), str(x["asset_class"]), ts),
            )
        )
    events.sort(key=lambda ev: ev.timestamp or datetime.min)

    profile = QUANTARA_STANDARD_PAPER
    start = starting_cash if starting_cash is not None else profile.starting_cash
    state = ReplayV3State(
        profile=profile,
        fx_rates=fx_rates,
        balance=start,
        cash=start,
        spot_crypto_cash=start,
    )
    result = replay_v3(events, starting_cash=start, initial_state=state)
    result["entries_total"] = len(entries)
    result["exits_total"] = len(exits)
    result["day_start"] = day_start.isoformat()
    result["day_end"] = day_end.isoformat()
    result["fx_rates_used"] = {k: float(v) for k, v in fx_rates.items()}
    return result


def write_replay_v3_audit_markdown(store: TradingStore, path: str, day_start: datetime, day_end: datetime) -> dict:
    """Generate docs/audits replay report from historical DB replay."""
    result = replay_historical_day(store, day_start, day_end)
    lines = [
        f"# Broker Replay V3 — {day_start.date()}",
        "",
        "## Summary",
        f"- Historical entries in DB: {result.get('entries_total', 0)}",
        f"- Historical exits in DB: {result.get('exits_total', 0)}",
        f"- Broker accepted entries: {result.get('broker_accepted_entries', 0)}",
        f"- Broker rejected entries: {result.get('broker_rejected_entries', 0)}",
        f"- Valid physical exits: {result.get('valid_physical_exits', 0)}",
        f"- Orphan/unexecuted exits skipped: {result.get('orphan_exits', 0)}",
        f"- Total accepted broker events: {result.get('accepted', 0)}",
        f"- Total rejected broker events: {result.get('rejected', 0)}",
        "",
        "## Ending state",
        f"- Ending balance: ${result.get('ending_balance', 0):,.2f}",
        f"- Ending equity: ${result.get('ending_equity', 0):,.2f}",
        f"- Gross realized P&L: ${result.get('gross_realized_pnl', 0):,.2f}",
        f"- Fees paid: ${result.get('fees_paid', 0):,.2f}",
        f"- Net realized P&L: ${result.get('net_realized_pnl', 0):,.2f}",
        f"- Open broker positions: {result.get('open_positions', 0)}",
        f"- Gross exposure: ${result.get('gross_exposure', 0):,.2f}",
        f"- Net exposure: ${result.get('net_exposure', 0):,.2f}",
        f"- Max gross leverage: {result.get('max_gross_leverage', 0):.4f}",
        f"- Max margin used: ${result.get('max_margin_used', 0):,.2f}",
        "",
        "## Reconciliation",
        f"- Financial: {result.get('financial_reconciliation', 0):.2f}",
        f"- Margin: {result.get('margin_reconciliation', 0):.2f}",
        f"- Attribution: {result.get('attribution_reconciliation', 0):.2f}",
        f"- Physical quantity diff: {result.get('physical_quantity_difference', 0)}",
        "",
        "## FX rates used",
    ]
    for cur, rate in sorted((result.get("fx_rates_used") or {}).items()):
        lines.append(f"- {cur}: {rate}")
    lines.append("")
    from pathlib import Path

    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return result
