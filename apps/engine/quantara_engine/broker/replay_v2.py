"""Full-day broker lifecycle replay (entries + exits) — read-only simulation."""

from __future__ import annotations

from collections import Counter
from datetime import datetime
from decimal import Decimal

from quantara_engine.broker.account import build_account_snapshot
from quantara_engine.broker.netting import apply_fill_with_realized_pnl
from quantara_engine.broker.pre_trade import evaluate_broker_order
from quantara_engine.broker.profile import QUANTARA_STANDARD_PAPER
from quantara_engine.broker.types import BrokerOrderRequest, PositionMode
from quantara_engine.persistence.store import TradingStore

_REPLAY_FX = {"USD": Decimal("1"), "JPY": Decimal("150")}


def replay_full_day(
    store: TradingStore,
    day_start: datetime,
    day_end: datetime,
) -> dict:
    from sqlalchemy import text

    entries = store.session.execute(
        text(
            """
            SELECT p.quantity, p.direction, p.entry_price, p.opened_at,
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
            SELECT t.quantity, p.direction, t.exit_price, t.closed_at,
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

    profile = QUANTARA_STANDARD_PAPER
    positions: dict[str, tuple[Decimal, Decimal, Decimal]] = {}
    balance = profile.starting_cash
    cash = profile.starting_cash
    spot = profile.starting_cash
    realized_total = Decimal("0")
    entry_accepted = entry_rejected = 0
    exit_replayed = 0
    reasons: Counter = Counter()
    max_gross = Decimal("0")
    max_margin = Decimal("0")

    events = []
    for e in entries:
        events.append(("entry", e))
    for x in exits:
        events.append(("exit", x))
    events.sort(key=lambda ev: ev[1].get("opened_at") or ev[1].get("closed_at"))

    for kind, row in events:
        symbol = str(row["symbol"]).upper()
        ts = row.get("opened_at") or row.get("closed_at")
        if kind == "entry":
            direction = "long" if str(row["direction"]).lower() == "long" else "short"
            qty = Decimal(str(row["quantity"]))
            price = Decimal(str(row["entry_price"]))
        else:
            pos_dir = str(row["direction"]).lower()
            direction = "short" if pos_dir == "long" else "long"
            qty = Decimal(str(row["quantity"]))
            price = Decimal(str(row["exit_price"]))
            exit_replayed += 1

        snap = build_account_snapshot(
            cash=cash,
            balance=balance,
            realized_pnl=realized_total,
            positions=positions,
            fx_rates=_REPLAY_FX,
            profile=profile,
            spot_crypto_cash=spot,
        )
        req = BrokerOrderRequest(
            symbol=symbol,
            asset_class=str(row["asset_class"]),
            direction=direction,
            quantity=qty,
            mark_price=price,
            is_close=(kind == "exit"),
            market_open=True,
            data_fresh=True,
        )
        decision = evaluate_broker_order(snap, profile, req, _REPLAY_FX)
        if kind == "entry":
            if decision.accepted:
                entry_accepted += 1
            else:
                entry_rejected += 1
                reasons[decision.rejection_reason.value if decision.rejection_reason else "unknown"] += 1
                continue

        cur_q, cur_a, _ = positions.get(symbol, (Decimal("0"), Decimal("0"), price))
        fill = apply_fill_with_realized_pnl(
            cur_q, cur_a, decision.accepted_quantity if kind == "entry" else qty,
            price, direction, mode=PositionMode.NETTING,
        )
        balance += fill.realized_pnl
        realized_total += fill.realized_pnl
        if fill.new_net_qty == 0:
            positions.pop(symbol, None)
        else:
            positions[symbol] = (fill.new_net_qty, fill.new_avg_price, price)

        snap2 = build_account_snapshot(
            cash=cash,
            balance=balance,
            realized_pnl=realized_total,
            positions=positions,
            fx_rates=_REPLAY_FX,
            profile=profile,
            spot_crypto_cash=spot,
        )
        max_gross = max(max_gross, snap2.gross_exposure)
        max_margin = max(max_margin, snap2.initial_margin_used)

    final = build_account_snapshot(
        cash=cash,
        balance=balance,
        realized_pnl=realized_total,
        positions=positions,
        fx_rates=_REPLAY_FX,
        profile=profile,
        spot_crypto_cash=spot,
    )

    return {
        "entries_total": len(entries),
        "entries_accepted": entry_accepted,
        "entries_rejected": entry_rejected,
        "exits_replayed": exit_replayed,
        "by_reason": dict(reasons),
        "ending_balance": float(balance),
        "ending_equity": float(final.equity),
        "ending_positions": len(positions),
        "max_gross_exposure": float(max_gross),
        "max_gross_leverage": float(final.gross_leverage),
        "max_initial_margin_used": float(max_margin),
        "realized_pnl": float(realized_total),
    }
