"""Live Sim physical protection gate helpers (virtual / paper only)."""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Literal

from sqlalchemy import text
from sqlalchemy.orm import Session

ProtectionStatus = Literal["NOT_APPLICABLE", "HEALTHY", "UNHEALTHY"]

_KRAKEN_LIKE_SLUG = "live-sim-kraken-like"
_ETH_SYMBOL = "ETHUSD"


def eth_kraken_protection_status(session: Session) -> dict[str, Any]:
    """
    Kraken-like ETH protection health.

    No open Live Sim ETH position ⇒ NOT_APPLICABLE (healthy for release gates).
    Open position ⇒ require broker qty, attribution, SL/TP, and mark aligned.
    """
    open_count = int(
        session.execute(
            text(
                """
                SELECT COUNT(*) FROM live_sim_positions p
                JOIN instruments i ON i.id = p.instrument_id
                WHERE p.status = 'open' AND i.symbol = :sym
                """
            ),
            {"sym": _ETH_SYMBOL},
        ).scalar()
        or 0
    )
    if open_count == 0:
        return {
            "status": "NOT_APPLICABLE",
            "healthy": True,
            "reason": "no_open_eth_position",
            "open_positions": 0,
        }

    row = (
        session.execute(
            text(
                """
                SELECT p.quantity, p.stop_loss, p.take_profit, bp.net_quantity,
                       COALESCE(SUM(l.remaining_qty), 0) AS attr_qty, bp.mark_price
                FROM live_sim_positions p
                JOIN broker_accounts ba ON ba.id = p.broker_account_id
                JOIN instruments i ON i.id = p.instrument_id
                JOIN broker_positions bp ON bp.broker_account_id = p.broker_account_id
                  AND bp.instrument_id = p.instrument_id
                LEFT JOIN broker_attribution_lots l ON l.strategy_position_id = p.id
                  AND l.remaining_qty > 0
                WHERE p.status = 'open' AND ba.slug = :slug AND i.symbol = :sym
                GROUP BY p.id, p.quantity, p.stop_loss, p.take_profit,
                         bp.net_quantity, bp.mark_price
                """
            ),
            {"slug": _KRAKEN_LIKE_SLUG, "sym": _ETH_SYMBOL},
        )
        .mappings()
        .first()
    )
    if not row:
        return {
            "status": "UNHEALTHY",
            "healthy": False,
            "reason": "open_eth_missing_broker_row",
            "open_positions": open_count,
        }

    qty = Decimal(str(row["quantity"]))
    net = Decimal(str(row["net_quantity"]))
    attr = Decimal(str(row["attr_qty"]))
    aligned = net == attr == qty
    has_levels = bool(row["stop_loss"]) and bool(row["take_profit"])
    has_mark = bool(row["mark_price"])
    healthy = aligned and has_levels and has_mark
    return {
        "status": "HEALTHY" if healthy else "UNHEALTHY",
        "healthy": healthy,
        "reason": "ok" if healthy else "qty_sl_tp_mark_mismatch",
        "open_positions": open_count,
        "row": dict(row),
    }
