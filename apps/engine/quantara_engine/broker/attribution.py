"""FIFO strategy attribution against broker fills — physical lot tracking."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from quantara_engine.broker.instruments import get_instrument_spec
from quantara_engine.broker.pnl import realized_pnl_usd
from quantara_engine.persistence.store import TradingStore


@dataclass
class AttributionLot:
    id: str
    strategy_position_id: str | None
    portfolio_id: str
    symbol: str
    direction: str
    remaining_qty: Decimal
    entry_price: Decimal
    opportunity_key: str | None = None
    broker_fill_id: str | None = None


def _load_open_lots(store: TradingStore, account_id: str, symbol: str) -> list[AttributionLot]:
    rows = store.session.execute(
        text(
            """
            SELECT id::text, strategy_position_id::text, strategy_portfolio_id::text,
                   symbol, direction::text, remaining_qty, entry_price, opportunity_key,
                   broker_fill_id::text
            FROM broker_attribution_lots
            WHERE broker_account_id = :aid AND symbol = :sym AND remaining_qty > 0
            ORDER BY opened_at ASC, id ASC
            """
        ),
        {"aid": account_id, "sym": symbol.upper()},
    ).mappings().all()
    return [
        AttributionLot(
            id=r["id"],
            strategy_position_id=r.get("strategy_position_id"),
            portfolio_id=str(r["strategy_portfolio_id"]),
            symbol=str(r["symbol"]),
            direction=str(r["direction"]).lower(),
            remaining_qty=Decimal(str(r["remaining_qty"])),
            entry_price=Decimal(str(r["entry_price"])),
            opportunity_key=r.get("opportunity_key"),
            broker_fill_id=r.get("broker_fill_id"),
        )
        for r in rows
    ]


def _insert_entry_lot(
    store: TradingStore,
    *,
    lot_id: str,
    broker_account_id: str,
    broker_fill_id: str,
    strategy_position_id: str | None,
    portfolio_id: str,
    symbol: str,
    direction: str,
    quantity: Decimal,
    fill_price: Decimal,
    opportunity_key: str | None,
) -> None:
    store.session.execute(
        text(
            """
            INSERT INTO broker_attribution_lots (
              id, broker_account_id, broker_fill_id, strategy_position_id,
              strategy_portfolio_id, symbol, direction, remaining_qty,
              entry_price, opportunity_key
            ) VALUES (
              :id, :aid, :fid, :spid, :pid, :sym, :dir, :qty, :entry, :opp
            )
            """
        ),
        {
            "id": lot_id,
            "aid": broker_account_id,
            "fid": broker_fill_id,
            "spid": strategy_position_id,
            "pid": portfolio_id,
            "sym": symbol.upper(),
            "dir": direction,
            "qty": quantity,
            "entry": fill_price,
            "opp": opportunity_key,
        },
    )
    store.session.execute(
        text(
            """
            INSERT INTO broker_attribution_ledger (
              broker_fill_id, strategy_position_id, strategy_portfolio_id,
              opportunity_key, quantity, entry_price, realized_pnl, direction
            ) VALUES (:fid, :spid, :pid, :opp, :qty, :entry, 0, :dir)
            """
        ),
        {
            "fid": broker_fill_id,
            "spid": strategy_position_id,
            "pid": portfolio_id,
            "opp": opportunity_key,
            "qty": quantity,
            "entry": fill_price,
            "dir": direction,
        },
    )


def allocate_fill_to_strategy_legs(
    store: TradingStore,
    *,
    broker_account_id: str,
    broker_fill_id: str,
    symbol: str,
    strategy_position_id: str | None,
    portfolio_id: str,
    direction: str,
    quantity: Decimal,
    fill_price: Decimal,
    closed_quantity: Decimal,
    fx_rates: dict[str, Decimal],
    opportunity_key: str | None = None,
) -> Decimal:
    """
    Decompose fill into closed + opened portions with FIFO attribution.

    closed_quantity: physical quantity closed against prior opposite lots.
    opened_quantity: remainder opened in fill direction (add or flip).
    """
    spec = get_instrument_spec(symbol)
    attributed_realized = Decimal("0")
    closed_qty = max(Decimal("0"), closed_quantity)
    opened_qty = max(Decimal("0"), quantity - closed_qty)

    remaining = closed_qty
    lots = _load_open_lots(store, broker_account_id, symbol)
    for lot in lots:
        if remaining <= 0:
            break
        take = min(lot.remaining_qty, remaining)
        if take <= 0:
            continue
        is_long = lot.direction == "long"
        lot_pnl = realized_pnl_usd(take, lot.entry_price, fill_price, is_long, spec, fx_rates)
        attributed_realized += lot_pnl
        new_rem = lot.remaining_qty - take
        store.session.execute(
            text(
                """
                UPDATE broker_attribution_lots SET remaining_qty = :rem
                WHERE id = :id
                """
            ),
            {"rem": new_rem, "id": lot.id},
        )
        store.session.execute(
            text(
                """
                INSERT INTO broker_attribution_ledger (
                  broker_fill_id, strategy_position_id, strategy_portfolio_id,
                  opportunity_key, quantity, entry_price, exit_price,
                  realized_pnl, direction
                ) VALUES (:fid, :spid, :pid, :opp, :qty, :entry, :exit, :pnl, :dir)
                """
            ),
            {
                "fid": broker_fill_id,
                "spid": lot.strategy_position_id,
                "pid": lot.portfolio_id,
                "opp": lot.opportunity_key or opportunity_key,
                "qty": take,
                "entry": lot.entry_price,
                "exit": fill_price,
                "pnl": lot_pnl,
                "dir": lot.direction,
            },
        )
        remaining -= take

    if opened_qty > 0:
        lot_id = str(uuid.uuid4())
        try:
            _insert_entry_lot(
                store,
                lot_id=lot_id,
                broker_account_id=broker_account_id,
                broker_fill_id=broker_fill_id,
                strategy_position_id=strategy_position_id,
                portfolio_id=portfolio_id,
                symbol=symbol,
                direction=direction,
                quantity=opened_qty,
                fill_price=fill_price,
                opportunity_key=opportunity_key,
            )
        except IntegrityError as exc:
            raise RuntimeError(
                f"duplicate attribution entry for fill {broker_fill_id}"
            ) from exc

    return attributed_realized


def link_strategy_position_to_fill(
    store: TradingStore,
    *,
    broker_fill_id: str,
    strategy_position_id: str,
) -> None:
    """Attach strategy position id to provisional attribution rows after leg is persisted."""
    store.session.execute(
        text(
            """
            UPDATE broker_attribution_lots
            SET strategy_position_id = :spid
            WHERE broker_fill_id = :fid AND strategy_position_id IS NULL
            """
        ),
        {"spid": strategy_position_id, "fid": broker_fill_id},
    )
    store.session.execute(
        text(
            """
            UPDATE broker_attribution_ledger
            SET strategy_position_id = :spid
            WHERE broker_fill_id = :fid AND strategy_position_id IS NULL
            """
        ),
        {"spid": strategy_position_id, "fid": broker_fill_id},
    )


def attributed_remaining_quantity(
    store: TradingStore,
    *,
    broker_account_id: str,
    symbol: str,
    strategy_position_id: str | None = None,
    portfolio_id: str | None = None,
    opportunity_key: str | None = None,
) -> Decimal:
    """Remaining physically attributed quantity for a strategy leg."""
    if strategy_position_id:
        row = store.session.execute(
            text(
                """
                SELECT COALESCE(SUM(remaining_qty), 0) AS qty
                FROM broker_attribution_lots
                WHERE broker_account_id = :aid AND symbol = :sym
                  AND strategy_position_id = :spid AND remaining_qty > 0
                """
            ),
            {"aid": broker_account_id, "sym": symbol.upper(), "spid": strategy_position_id},
        ).mappings().first()
        return Decimal(str(row["qty"])) if row else Decimal("0")

    if portfolio_id and opportunity_key:
        row = store.session.execute(
            text(
                """
                SELECT COALESCE(SUM(remaining_qty), 0) AS qty
                FROM broker_attribution_lots
                WHERE broker_account_id = :aid AND symbol = :sym
                  AND strategy_portfolio_id = :pid AND opportunity_key = :opp
                  AND remaining_qty > 0
                """
            ),
            {
                "aid": broker_account_id,
                "sym": symbol.upper(),
                "pid": portfolio_id,
                "opp": opportunity_key,
            },
        ).mappings().first()
        return Decimal(str(row["qty"])) if row else Decimal("0")

    return Decimal("0")


def resolve_physical_exit_quantity(
    store: TradingStore,
    *,
    broker_account_id: str,
    symbol: str,
    requested_quantity: Decimal,
    strategy_position_id: str | None = None,
    portfolio_id: str | None = None,
    opportunity_key: str | None = None,
) -> Decimal:
    """Cap strategy exit to currently attributed physical quantity."""
    attributed = attributed_remaining_quantity(
        store,
        broker_account_id=broker_account_id,
        symbol=symbol,
        strategy_position_id=strategy_position_id,
        portfolio_id=portfolio_id,
        opportunity_key=opportunity_key,
    )
    if attributed <= 0:
        return Decimal("0")
    return min(requested_quantity, attributed)


def attribution_reconciliation(store: TradingStore, account_id: str) -> dict:
    """Compare broker fill realized vs attributed ledger sum."""
    broker_row = store.session.execute(
        text(
            """
            SELECT COALESCE(SUM(realized_pnl), 0) AS total
            FROM broker_fills f
            JOIN broker_orders o ON o.id = f.broker_order_id
            WHERE o.broker_account_id = :aid
            """
        ),
        {"aid": account_id},
    ).mappings().first()
    attr_row = store.session.execute(
        text(
            """
            SELECT COALESCE(SUM(l.realized_pnl), 0) AS total
            FROM broker_attribution_ledger l
            JOIN broker_fills f ON f.id = l.broker_fill_id
            JOIN broker_orders o ON o.id = f.broker_order_id
            WHERE o.broker_account_id = :aid
            """
        ),
        {"aid": account_id},
    ).mappings().first()
    open_lots = store.session.execute(
        text(
            """
            SELECT COALESCE(SUM(remaining_qty), 0) AS qty
            FROM broker_attribution_lots
            WHERE broker_account_id = :aid AND remaining_qty > 0
            """
        ),
        {"aid": account_id},
    ).mappings().first()
    broker_qty = store.session.execute(
        text(
            """
            SELECT COALESCE(SUM(ABS(net_quantity)), 0) AS qty
            FROM broker_positions WHERE broker_account_id = :aid
            """
        ),
        {"aid": account_id},
    ).mappings().first()

    b = Decimal(str(broker_row["total"]))
    a = Decimal(str(attr_row["total"]))
    lot_qty = Decimal(str(open_lots["qty"]))
    pos_qty = Decimal(str(broker_qty["qty"]))
    return {
        "broker_realized": float(b),
        "attributed_realized": float(a),
        "realized_difference": float((b - a).quantize(Decimal("0.01"))),
        "open_lot_qty": float(lot_qty),
        "broker_position_qty": float(pos_qty),
        "quantity_difference": float((lot_qty - pos_qty).quantize(Decimal("0.00000001"))),
    }
