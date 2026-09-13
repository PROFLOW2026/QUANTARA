"""Read-time overlay: in-memory hub marks for API/SSE (no DB wait)."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from quantara_engine.market_data.streaming.hub import LiveMarkEntry, get_live_mark_hub
from quantara_engine.market_data.symbols import normalize_db_symbol
from quantara_engine.persistence.store import TradingStore

# Hub marks older than this are ignored for REST valuation overlay.
MAX_HUB_MARK_AGE_SECONDS = 120


def hub_mark_fresh(entry: LiveMarkEntry, *, now: datetime) -> bool:
    if entry.at.tzinfo is None:
        mark_at = entry.at.replace(tzinfo=now.tzinfo)
    else:
        mark_at = entry.at.astimezone(now.tzinfo)
    return (now - mark_at).total_seconds() <= MAX_HUB_MARK_AGE_SECONDS


def hub_mark_for_symbol(db_symbol: str) -> LiveMarkEntry | None:
    return get_live_mark_hub().get_entry(normalize_db_symbol(db_symbol))


def hub_mark_price(db_symbol: str) -> tuple[Decimal, datetime, str] | None:
    entry = hub_mark_for_symbol(db_symbol)
    if not entry:
        return None
    return entry.price, entry.at, entry.source


def recompute_unrealized_pnl_at_mark(
    store: TradingStore,
    instrument_id: str,
    mark: Decimal,
) -> float:
    """Read-only unrealized PnL at ``mark`` for open research positions on one instrument."""
    from quantara_engine.broker.instruments import get_instrument_spec
    from quantara_engine.broker.pnl import unrealized_pnl_usd
    from quantara_engine.market_data.symbols import normalize_db_symbol
    from sqlalchemy import text

    inst = store.session.execute(
        text("SELECT symbol FROM instruments WHERE id = :id"),
        {"id": instrument_id},
    ).scalar_one_or_none()
    if not inst:
        return 0.0
    spec = get_instrument_spec(normalize_db_symbol(inst))
    fx = {"USD": Decimal("1")}

    rows = store.session.execute(
        text(
            """
            SELECT p.direction, p.quantity, p.entry_price
            FROM positions p
            JOIN strategy_instances si ON si.id = p.strategy_instance_id
            WHERE si.instrument_id = :iid AND p.status = 'open'
            """
        ),
        {"iid": instrument_id},
    ).mappings().all()

    total = Decimal("0")
    for row in rows:
        qty = Decimal(str(row["quantity"]))
        entry = Decimal(str(row["entry_price"]))
        signed = qty if str(row["direction"]).lower() == "long" else -qty
        total += unrealized_pnl_usd(signed, entry, mark, spec, fx)
    return float(total)
