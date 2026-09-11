"""Replay V4 — competition-scoped historical replay with marks, FX truth, independent reconciliation."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import text

from quantara_engine.broker.account import build_account_snapshot
from quantara_engine.broker.instruments import get_instrument_spec
from quantara_engine.broker.margin import initial_margin_for_notional, maintenance_margin_for_notional
from quantara_engine.broker.netting import apply_fill_with_realized_pnl
from quantara_engine.broker.pnl import realized_pnl_usd, unrealized_pnl_usd
from quantara_engine.broker.pre_trade import evaluate_broker_order
from quantara_engine.broker.profile import QUANTARA_STANDARD_PAPER
from quantara_engine.broker.types import BrokerOrderRequest, PositionMode
from quantara_engine.competition.constants import ACTIVE_COMPETITION_EXPERIMENT_ID
from quantara_engine.competition.orb_constants import ORB_COMPETITION_EXPERIMENT_ID
from quantara_engine.persistence.store import TradingStore

_DEFAULT_FX = {"USD": Decimal("1"), "JPY": Decimal("150")}
_COMPETITION_EXPERIMENTS = (ACTIVE_COMPETITION_EXPERIMENT_ID, ORB_COMPETITION_EXPERIMENT_ID)


@dataclass
class ReplayLot:
    portfolio_id: str
    strategy_position_id: str | None
    direction: str
    remaining_qty: Decimal
    entry_price: Decimal
    opportunity_key: str | None = None


@dataclass
class ReplayV4Event:
    kind: str  # entry | exit | mark
    symbol: str
    asset_class: str
    direction: str
    quantity: Decimal
    price: Decimal
    portfolio_id: str = ""
    strategy_position_id: str | None = None
    fees: Decimal = Decimal("0")
    market_open: bool = True
    data_fresh: bool = True
    timestamp: datetime | None = None
    order_purpose: str = "entry"
    rejection_reason: str | None = None


@dataclass
class ReplayV4State:
    profile: Any = QUANTARA_STANDARD_PAPER
    fx_rates: dict[str, Decimal] = field(default_factory=lambda: dict(_DEFAULT_FX))
    fx_methodology: str = "dashboard_resolve_default"
    positions: dict[str, tuple[Decimal, Decimal, Decimal]] = field(default_factory=dict)
    balance: Decimal = Decimal("320000")
    cash: Decimal = Decimal("320000")
    spot_crypto_cash: Decimal = Decimal("320000")
    gross_realized: Decimal = Decimal("0")
    fees_paid: Decimal = Decimal("0")
    net_realized: Decimal = Decimal("0")
    physical_fill_realized: Decimal = Decimal("0")
    attributed_realized: Decimal = Decimal("0")
    lots: dict[str, deque[ReplayLot]] = field(default_factory=dict)
    max_gross_leverage: Decimal = Decimal("0")
    max_initial_margin: Decimal = Decimal("0")
    min_margin_level: Decimal | None = None
    historical_entries: int = 0
    broker_accepted_entries: int = 0
    broker_rejected_entries: int = 0
    historical_exits: int = 0
    valid_physical_exits: int = 0
    shadow_only_exits: int = 0
    orphan_exits: int = 0
    rejection_counts: dict[str, int] = field(default_factory=dict)
    cash_from_fills: Decimal = Decimal("0")
    starting_cash: Decimal = Decimal("320000")


def _snapshot(state: ReplayV4State):
    return build_account_snapshot(
        cash=state.cash,
        balance=state.balance,
        realized_pnl=state.net_realized,
        positions=state.positions,
        fx_rates=state.fx_rates,
        profile=state.profile,
        spot_crypto_cash=state.spot_crypto_cash,
    )


def _track_peaks(state: ReplayV4State) -> None:
    snap = _snapshot(state)
    if snap.gross_leverage > state.max_gross_leverage:
        state.max_gross_leverage = snap.gross_leverage
    if snap.initial_margin_used > state.max_initial_margin:
        state.max_initial_margin = snap.initial_margin_used
    if snap.margin_level_pct is not None:
        if state.min_margin_level is None or snap.margin_level_pct < state.min_margin_level:
            state.min_margin_level = snap.margin_level_pct


def _attributed_for_strategy(state: ReplayV4State, symbol: str, strategy_position_id: str) -> Decimal:
    return sum(
        (
            lot.remaining_qty
            for lot in state.lots.get(symbol, deque())
            if lot.strategy_position_id == strategy_position_id
        ),
        Decimal("0"),
    )


def _close_lots(
    state: ReplayV4State,
    *,
    symbol: str,
    closed_qty: Decimal,
    fill_price: Decimal,
    strategy_position_id: str | None,
    order_purpose: str,
) -> Decimal:
    spec = get_instrument_spec(symbol)
    remaining = closed_qty
    attributed = Decimal("0")
    queue = state.lots.setdefault(symbol, deque())
    if order_purpose in ("sl", "tp", "close", "flatten") and strategy_position_id:
        candidates = [lot for lot in queue if lot.strategy_position_id == strategy_position_id]
    else:
        candidates = list(queue)

    for lot in candidates:
        if remaining <= 0:
            break
        take = min(lot.remaining_qty, remaining)
        if take <= 0:
            continue
        is_long = lot.direction == "long"
        lot_pnl = realized_pnl_usd(take, lot.entry_price, fill_price, is_long, spec, state.fx_rates)
        attributed += lot_pnl
        lot.remaining_qty -= take
        remaining -= take

    if remaining > 0:
        for lot in queue:
            if remaining <= 0:
                break
            if lot.remaining_qty <= 0:
                continue
            take = min(lot.remaining_qty, remaining)
            is_long = lot.direction == "long"
            lot_pnl = realized_pnl_usd(take, lot.entry_price, fill_price, is_long, spec, state.fx_rates)
            attributed += lot_pnl
            lot.remaining_qty -= take
            remaining -= take

    state.lots[symbol] = deque(lot for lot in queue if lot.remaining_qty > 0)
    state.attributed_realized += attributed
    return attributed


def _open_lot(state: ReplayV4State, *, symbol: str, portfolio_id: str, strategy_position_id: str | None,
              direction: str, quantity: Decimal, fill_price: Decimal) -> None:
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


def _apply_crypto_cash(state: ReplayV4State, *, symbol: str, direction: str, quantity: Decimal,
                       price: Decimal, fees: Decimal, is_close: bool) -> None:
    spec = get_instrument_spec(symbol)
    rules = state.profile.rules_for(spec.asset_class)
    if spec.asset_class != "crypto" or rules.initial_margin_pct < Decimal("100"):
        state.cash -= fees
        state.cash_from_fills -= fees
        return
    notional = quantity * price
    if direction == "long" and not is_close:
        state.cash -= notional + fees
        state.spot_crypto_cash -= notional + fees
        state.cash_from_fills -= notional + fees
    else:
        state.cash += notional - fees
        state.spot_crypto_cash += notional - fees
        state.cash_from_fills += notional - fees


def _apply_mark(state: ReplayV4State, symbol: str, mark: Decimal) -> None:
    if symbol not in state.positions:
        return
    qty, avg, _ = state.positions[symbol]
    state.positions[symbol] = (qty, avg, mark)
    _track_peaks(state)


def _execute_fill(state: ReplayV4State, event: ReplayV4Event, *, is_close: bool) -> None:
    spec = get_instrument_spec(event.symbol)
    cur_qty, cur_avg, _ = state.positions.get(event.symbol, (Decimal("0"), Decimal("0"), event.price))
    netting = apply_fill_with_realized_pnl(
        cur_qty, cur_avg, event.quantity, event.price, event.direction,
        mode=PositionMode.NETTING, spec=spec, fx_rates=state.fx_rates,
    )
    fees = event.fees.quantize(Decimal("0.0001"))
    gross_pnl = netting.realized_pnl
    net_pnl = gross_pnl - fees
    state.balance += net_pnl
    state.gross_realized += gross_pnl
    state.physical_fill_realized += gross_pnl
    state.fees_paid += fees
    state.net_realized += net_pnl
    _apply_crypto_cash(
        state, symbol=event.symbol, direction=event.direction, quantity=event.quantity,
        price=event.price, fees=fees, is_close=is_close,
    )

    closed_qty = netting.closed_quantity
    opened_qty = max(Decimal("0"), event.quantity - closed_qty)
    if closed_qty > 0:
        _close_lots(
            state,
            symbol=event.symbol,
            closed_qty=closed_qty,
            fill_price=event.price,
            strategy_position_id=event.strategy_position_id,
            order_purpose=event.order_purpose,
        )
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
    else:
        state.positions[event.symbol] = (netting.new_net_qty, netting.new_avg_price, event.price)
    _track_peaks(state)


def replay_v4(events: list[ReplayV4Event], *, state: ReplayV4State | None = None) -> dict:
    state = state or ReplayV4State(profile=QUANTARA_STANDARD_PAPER)
    accepted = rejected = 0

    for event in events:
        if event.kind == "mark":
            _apply_mark(state, event.symbol, event.price)
            continue

        if event.kind == "entry":
            state.historical_entries += 1
        elif event.kind == "exit":
            state.historical_exits += 1

        if event.kind == "exit":
            cap_qty = event.quantity
            if event.strategy_position_id:
                cap_qty = min(event.quantity, _attributed_for_strategy(
                    state, event.symbol, event.strategy_position_id
                ))
            if cap_qty <= 0:
                state.shadow_only_exits += 1
                state.orphan_exits += 1
                rejected += 1
                continue
            if cap_qty < event.quantity:
                event = ReplayV4Event(
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
                    data_fresh=event.data_fresh,
                    timestamp=event.timestamp,
                    order_purpose=event.order_purpose,
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
            data_fresh=event.data_fresh,
        )
        decision = evaluate_broker_order(snap, state.profile, req, state.fx_rates)
        if not decision.accepted:
            rejected += 1
            reason = decision.rejection_reason.value if decision.rejection_reason else "unknown"
            state.rejection_counts[reason] = state.rejection_counts.get(reason, 0) + 1
            if event.kind == "entry":
                state.broker_rejected_entries += 1
            continue

        accepted += 1
        if event.kind == "entry":
            state.broker_accepted_entries += 1
        _execute_fill(state, event, is_close=is_close)

    final = _snapshot(state)
    per_asset_qty: dict[str, float] = {}
    for symbol in set(state.positions.keys()) | set(state.lots.keys()):
        broker_qty = abs(state.positions.get(symbol, (Decimal("0"), Decimal("0"), Decimal("0")))[0])
        lot_qty = sum((lot.remaining_qty for lot in state.lots.get(symbol, deque())), Decimal("0"))
        per_asset_qty[symbol] = float((lot_qty - broker_qty).quantize(Decimal("0.00000001")))

    expected_balance = state.starting_cash + state.net_realized
    financial_reconciliation = (state.balance - expected_balance).quantize(Decimal("0.01"))

    independent_im = Decimal("0")
    independent_mm = Decimal("0")
    for symbol, (qty, avg, mark) in state.positions.items():
        spec = get_instrument_spec(symbol)
        rules = state.profile.rules_for(spec.asset_class)
        from quantara_engine.broker.margin import quote_notional_usd

        notional = quote_notional_usd(qty, mark, spec, state.fx_rates)
        independent_im += initial_margin_for_notional(notional, rules)
        independent_mm += maintenance_margin_for_notional(notional, rules)

    margin_reconciliation = (
        independent_im - final.initial_margin_used
    ).quantize(Decimal("0.01"))
    maintenance_reconciliation = (
        independent_mm - final.maintenance_margin_required
    ).quantize(Decimal("0.01"))
    attribution_reconciliation = (
        state.physical_fill_realized - state.attributed_realized
    ).quantize(Decimal("0.01"))

    return {
        "events_total": len(events),
        "accepted": accepted,
        "rejected": rejected,
        "orphan_exits": state.orphan_exits,
        "shadow_only_exits": state.shadow_only_exits,
        "historical_entries": state.historical_entries,
        "broker_accepted_entries": state.broker_accepted_entries,
        "broker_rejected_entries": state.broker_rejected_entries,
        "historical_exits": state.historical_exits,
        "valid_physical_exits": state.valid_physical_exits,
        "rejection_counts": dict(state.rejection_counts),
        "starting_cash": float(state.starting_cash),
        "ending_balance": float(state.balance),
        "ending_equity": float(final.equity),
        "gross_realized_pnl": float(state.gross_realized),
        "net_realized_pnl": float(state.net_realized),
        "fees_paid": float(state.fees_paid),
        "unrealized_pnl": float(final.unrealized_pnl),
        "open_positions": len(state.positions),
        "gross_exposure": float(final.gross_exposure),
        "net_exposure": float(final.net_exposure),
        "max_gross_leverage": float(state.max_gross_leverage),
        "max_initial_margin": float(state.max_initial_margin),
        "min_margin_level": float(state.min_margin_level) if state.min_margin_level is not None else None,
        "financial_reconciliation": float(financial_reconciliation),
        "margin_reconciliation": float(margin_reconciliation),
        "maintenance_reconciliation": float(maintenance_reconciliation),
        "attribution_reconciliation": float(attribution_reconciliation),
        "per_asset_quantity_reconciliation": per_asset_qty,
        "fx_rates_used": {k: float(v) for k, v in state.fx_rates.items()},
        "fx_methodology": state.fx_methodology,
        "account_state": final.account_state.value,
        "open_physical_positions": {
            sym: {
                "net_quantity": float(qty),
                "average_price": float(avg),
                "mark_price": float(mark),
            }
            for sym, (qty, avg, mark) in state.positions.items()
        },
    }


def _resolve_fx_rates(store: TradingStore, at: datetime) -> tuple[dict[str, Decimal], str]:
    from quantara_engine.portfolio.currency import resolve_dashboard_fx_rates

    rates = dict(_DEFAULT_FX)
    try:
        fx = resolve_dashboard_fx_rates(store, {"USD", "JPY"})
        rates.update({k: v for k, v in fx.quote_per_usd.items()})
        return rates, f"resolve_dashboard_fx_rates at {at.isoformat()}"
    except Exception as exc:
        return rates, f"fallback_default_fx ({exc})"


def _market_open_for_row(store: TradingStore, symbol: str, asset_class: str, at: datetime) -> bool | None:
    from quantara_engine.broker.market_gate import market_open_for_instrument
    from quantara_engine.domain.types import Instrument

    try:
        instrument = store.get_instrument_by_symbol(symbol) or Instrument(
            id="replay", symbol=symbol, name=symbol, asset_class=asset_class,
        )
        return market_open_for_instrument(instrument, at)
    except Exception:
        return None


def _data_fresh_for_row(store: TradingStore, symbol: str, asset_class: str, at: datetime) -> tuple[bool, str]:
    from quantara_engine.broker.market_gate import data_fresh_for_instrument
    from quantara_engine.domain.types import Instrument

    try:
        instrument = store.get_instrument_by_symbol(symbol) or Instrument(
            id="replay", symbol=symbol, name=symbol, asset_class=asset_class,
        )
        fresh, detail = data_fresh_for_instrument(store, instrument, "5m", at)
        return fresh, detail or "persisted_candle_gate"
    except Exception as exc:
        return False, f"unknown_freshness_error:{exc}"


def _load_mark_events(
    store: TradingStore,
    day_start: datetime,
    day_end: datetime,
    symbols: set[str],
) -> list[ReplayV4Event]:
    if not symbols:
        return []
    rows = store.session.execute(
        text(
            """
            SELECT c.timestamp, i.symbol, i.asset_class::text AS asset_class, c.close
            FROM candles c
            JOIN instruments i ON i.id = c.instrument_id
            WHERE c.timestamp >= :d0 AND c.timestamp < :d1
              AND c.timeframe = '5m'
              AND i.symbol = ANY(CAST(:symbols AS text[]))
            ORDER BY c.timestamp, i.symbol
            """
        ),
        {"d0": day_start, "d1": day_end, "symbols": sorted(symbols)},
    ).mappings().all()
    return [
        ReplayV4Event(
            kind="mark",
            symbol=str(r["symbol"]).upper(),
            asset_class=str(r["asset_class"]),
            direction="long",
            quantity=Decimal("0"),
            price=Decimal(str(r["close"])),
            timestamp=r["timestamp"],
        )
        for r in rows
    ]


def replay_historical_day(
    store: TradingStore,
    day_start: datetime,
    day_end: datetime,
    *,
    starting_cash: Decimal | None = None,
) -> dict:
    """Read-only competition-scoped replay for one calendar day."""
    fx_rates, fx_method = _resolve_fx_rates(store, day_start)
    profile = QUANTARA_STANDARD_PAPER
    start = starting_cash if starting_cash is not None else profile.starting_cash
    state = ReplayV4State(
        profile=profile,
        fx_rates=fx_rates,
        fx_methodology=fx_method,
        balance=start,
        cash=start,
        spot_crypto_cash=start,
        starting_cash=start,
    )

    entries = store.session.execute(
        text(
            """
            SELECT p.id::text AS position_id, p.quantity, p.direction, p.entry_price, p.opened_at,
                   p.portfolio_id::text AS portfolio_id,
                   i.symbol, i.asset_class::text AS asset_class
            FROM positions p
            JOIN instruments i ON i.id = p.instrument_id
            JOIN strategy_instances si ON si.id = p.strategy_instance_id
            WHERE p.backtest_run_id IS NULL
              AND si.experiment_id = ANY(CAST(:exp_ids AS uuid[]))
              AND p.opened_at >= :d0 AND p.opened_at < :d1
            ORDER BY p.opened_at
            """
        ),
        {"d0": day_start, "d1": day_end, "exp_ids": list(_COMPETITION_EXPERIMENTS)},
    ).mappings().all()

    exits = store.session.execute(
        text(
            """
            SELECT t.quantity, t.exit_price, t.closed_at, t.fees_total,
                   p.id::text AS position_id, p.direction, p.portfolio_id::text AS portfolio_id,
                   i.symbol, i.asset_class::text AS asset_class
            FROM trades t
            JOIN positions p ON p.id = t.position_id
            JOIN instruments i ON i.id = p.instrument_id
            JOIN strategy_instances si ON si.id = p.strategy_instance_id
            WHERE p.backtest_run_id IS NULL
              AND si.experiment_id = ANY(CAST(:exp_ids AS uuid[]))
              AND t.closed_at >= :d0 AND t.closed_at < :d1
            ORDER BY t.closed_at
            """
        ),
        {"d0": day_start, "d1": day_end, "exp_ids": list(_COMPETITION_EXPERIMENTS)},
    ).mappings().all()

    events: list[ReplayV4Event] = []
    for e in entries:
        ts = e["opened_at"]
        sym = str(e["symbol"]).upper()
        ac = str(e["asset_class"])
        market_open = _market_open_for_row(store, sym, ac, ts)
        fresh, _ = _data_fresh_for_row(store, sym, ac, ts)
        events.append(
            ReplayV4Event(
                kind="entry",
                symbol=sym,
                asset_class=ac,
                direction="long" if str(e["direction"]).lower() == "long" else "short",
                quantity=Decimal(str(e["quantity"])),
                price=Decimal(str(e["entry_price"])),
                portfolio_id=str(e["portfolio_id"]),
                strategy_position_id=str(e["position_id"]),
                timestamp=ts,
                market_open=True if market_open is None else market_open,
                data_fresh=fresh,
                order_purpose="entry",
            )
        )
    for x in exits:
        ts = x["closed_at"]
        sym = str(x["symbol"]).upper()
        ac = str(x["asset_class"])
        pos_dir = str(x["direction"]).lower()
        market_open = _market_open_for_row(store, sym, ac, ts)
        fresh, _ = _data_fresh_for_row(store, sym, ac, ts)
        events.append(
            ReplayV4Event(
                kind="exit",
                symbol=sym,
                asset_class=ac,
                direction="short" if pos_dir == "long" else "long",
                quantity=Decimal(str(x["quantity"])),
                price=Decimal(str(x["exit_price"])),
                portfolio_id=str(x["portfolio_id"]),
                strategy_position_id=str(x["position_id"]),
                fees=Decimal(str(x.get("fees_total") or "0")),
                timestamp=ts,
                market_open=True if market_open is None else market_open,
                data_fresh=fresh,
                order_purpose="close",
            )
        )

    replay_symbols = {ev.symbol for ev in events}
    events.extend(_load_mark_events(store, day_start, day_end, replay_symbols))
    events.sort(key=lambda ev: (ev.timestamp or datetime.min, 0 if ev.kind == "mark" else 1, ev.symbol))

    result = replay_v4(events, state=state)
    result["entries_total"] = len(entries)
    result["exits_total"] = len(exits)
    result["day_start"] = day_start.isoformat()
    result["day_end"] = day_end.isoformat()
    return result


def write_replay_v4_audit_markdown(store: TradingStore, path: str, day_start: datetime, day_end: datetime) -> dict:
    result = replay_historical_day(store, day_start, day_end)
    lines = [
        f"# Broker Replay V4 — {day_start.date()}",
        "",
        "## Summary",
        f"- Historical entries in DB: {result.get('entries_total', 0)}",
        f"- Historical exits in DB: {result.get('exits_total', 0)}",
        f"- Broker accepted entries: {result.get('broker_accepted_entries', 0)}",
        f"- Broker rejected entries: {result.get('broker_rejected_entries', 0)}",
        f"- Valid physical exits: {result.get('valid_physical_exits', 0)}",
        f"- Shadow-only exits: {result.get('shadow_only_exits', 0)}",
        f"- Orphan/unexecuted exits skipped: {result.get('orphan_exits', 0)}",
        "",
        "## Rejection reasons",
    ]
    for reason, count in sorted((result.get("rejection_counts") or {}).items()):
        lines.append(f"- {reason}: {count}")
    lines.extend([
        "",
        "## Ending state",
        f"- Ending cash/balance: ${result.get('ending_balance', 0):,.2f}",
        f"- Ending equity: ${result.get('ending_equity', 0):,.2f}",
        f"- Gross realized P&L: ${result.get('gross_realized_pnl', 0):,.2f}",
        f"- Fees paid: ${result.get('fees_paid', 0):,.2f}",
        f"- Net realized P&L: ${result.get('net_realized_pnl', 0):,.2f}",
        f"- Unrealized P&L: ${result.get('unrealized_pnl', 0):,.2f}",
        f"- Open broker positions: {result.get('open_positions', 0)}",
        f"- Gross exposure: ${result.get('gross_exposure', 0):,.2f}",
        f"- Net exposure: ${result.get('net_exposure', 0):,.2f}",
        f"- Max gross leverage: {result.get('max_gross_leverage', 0):.4f}",
        f"- Max initial margin: ${result.get('max_initial_margin', 0):,.2f}",
        f"- Minimum margin level: {result.get('min_margin_level')}",
        "",
        "## FX methodology",
        f"- {result.get('fx_methodology', 'unknown')}",
    ])
    for cur, rate in sorted((result.get("fx_rates_used") or {}).items()):
        lines.append(f"- {cur}: {rate}")
    lines.extend([
        "",
        "## Reconciliation",
        f"- Financial: {result.get('financial_reconciliation', 0):.2f}",
        f"- Initial margin: {result.get('margin_reconciliation', 0):.2f}",
        f"- Maintenance margin: {result.get('maintenance_reconciliation', 0):.2f}",
        f"- Attribution (physical fill vs attributed): {result.get('attribution_reconciliation', 0):.2f}",
        "",
        "## Per-asset quantity reconciliation (attribution - broker)",
    ])
    for sym, diff in sorted((result.get("per_asset_quantity_reconciliation") or {}).items()):
        lines.append(f"- {sym}: {diff}")
    lines.append("")
    from pathlib import Path

    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return result
