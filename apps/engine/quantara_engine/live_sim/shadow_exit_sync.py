"""Close Live Sim shadows when broker exit consumed all strategy attribution."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import text

from quantara_engine.broker.attribution import attributed_remaining_quantity
from quantara_engine.persistence.store import TradingStore

logger = logging.getLogger(__name__)

_EXIT_PURPOSES = frozenset({"sl", "tp", "close", "flatten", "manual_close"})


def _as_utc(ts: datetime) -> datetime:
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc)


def is_exit_order_purpose(order_purpose: str) -> bool:
    return order_purpose in _EXIT_PURPOSES


def _live_sim_position_row(store: TradingStore, strategy_position_id: str) -> dict[str, Any] | None:
    row = store.session.execute(
        text(
            """
            SELECT id::text, status, quantity, opportunity_key, broker_account_id::text
            FROM live_sim_positions
            WHERE id = CAST(:pid AS uuid)
            """
        ),
        {"pid": strategy_position_id},
    ).mappings().first()
    return dict(row) if row else None


def _exit_fill_by_id(store: TradingStore, fill_id: str) -> dict[str, Any] | None:
    row = store.session.execute(
        text(
            """
            SELECT f.id::text AS fill_id, f.filled_at, o.order_purpose::text AS purpose
            FROM broker_fills f
            JOIN broker_orders o ON o.id = f.broker_order_id
            WHERE f.id = CAST(:fid AS uuid)
              AND o.order_purpose IN ('sl', 'tp', 'close', 'flatten', 'manual_close')
            """
        ),
        {"fid": fill_id},
    ).mappings().first()
    return dict(row) if row else None


def _latest_exit_fill_evidence(store: TradingStore, *, strategy_position_id: str) -> dict[str, Any] | None:
    row = store.session.execute(
        text(
            """
            SELECT f.id::text AS fill_id, f.filled_at, o.order_purpose::text AS purpose
            FROM broker_attribution_ledger l
            JOIN broker_fills f ON f.id = l.broker_fill_id
            JOIN broker_orders o ON o.id = f.broker_order_id
            WHERE l.strategy_position_id = CAST(:pid AS uuid)
              AND l.exit_price IS NOT NULL
            ORDER BY f.filled_at DESC, f.fill_sequence DESC
            LIMIT 1
            """
        ),
        {"pid": strategy_position_id},
    ).mappings().first()
    return dict(row) if row else None


def _close_shadow_from_evidence(
    store: TradingStore,
    *,
    strategy_position_id: str,
    closed_at: datetime,
    reason: str,
    exit_fill_id: str | None = None,
) -> bool:
    """Idempotent shadow close — no broker fill, no PnL, no attribution mutation."""
    closed_at = _as_utc(closed_at)
    updated = store.session.execute(
        text(
            """
            UPDATE live_sim_positions
            SET status = 'closed', closed_at = :ts, updated_at = NOW()
            WHERE id = CAST(:pid AS uuid) AND status = 'open'
            RETURNING id::text
            """
        ),
        {"pid": strategy_position_id, "ts": closed_at},
    ).scalar()
    if updated:
        store.update_settings(
            f"live_sim_integrity:shadow_exit_sync:{strategy_position_id}",
            {
                "reason": reason,
                "closed_at": closed_at.isoformat(),
                "exit_fill_id": exit_fill_id,
                "synced_at": datetime.now(timezone.utc).isoformat(),
            },
            flush=False,
        )
        logger.info("Closed live sim shadow %s (%s)", strategy_position_id, reason)
        return True

    status = store.session.execute(
        text("SELECT status FROM live_sim_positions WHERE id = CAST(:pid AS uuid)"),
        {"pid": strategy_position_id},
    ).scalar()
    return str(status or "").lower() == "closed"


def _sync_shadow_quantity(
    store: TradingStore,
    *,
    strategy_position_id: str,
    remaining_qty: Decimal,
) -> bool:
    updated = store.session.execute(
        text(
            """
            UPDATE live_sim_positions
            SET quantity = :qty, updated_at = NOW()
            WHERE id = CAST(:pid AS uuid)
              AND status = 'open'
              AND quantity <> :qty
            RETURNING id::text
            """
        ),
        {"pid": strategy_position_id, "qty": remaining_qty},
    ).scalar()
    return bool(updated)


def maybe_close_live_sim_shadow_on_attribution_exhausted(
    store: TradingStore,
    *,
    strategy_position_id: str,
    broker_account_id: str,
    symbol: str,
    exit_fill_id: str | None = None,
    closed_at: datetime | None = None,
    portfolio_id: str | None = None,
    opportunity_key: str | None = None,
    reason: str = "attribution_exhausted_after_broker_exit",
) -> bool:
    """
    When strategy attribution is fully consumed after a broker exit, close the shadow row.

    Partial exits (remaining attributed qty > 0) keep the shadow OPEN and sync quantity.
    Returns True when the shadow is CLOSED (including idempotent already-closed).
    """
    row = _live_sim_position_row(store, strategy_position_id)
    if not row:
        return False
    if str(row["status"]).lower() != "open":
        return str(row["status"]).lower() == "closed"

    remaining = attributed_remaining_quantity(
        store,
        broker_account_id=broker_account_id,
        symbol=symbol,
        strategy_position_id=strategy_position_id,
        portfolio_id=portfolio_id,
        opportunity_key=opportunity_key or row.get("opportunity_key"),
    )
    if remaining > 0:
        _sync_shadow_quantity(
            store,
            strategy_position_id=strategy_position_id,
            remaining_qty=remaining,
        )
        return False

    evidence = _exit_fill_by_id(store, exit_fill_id) if exit_fill_id else None
    if not evidence:
        evidence = _latest_exit_fill_evidence(store, strategy_position_id=strategy_position_id)
    if not evidence:
        return False

    ts = closed_at or evidence["filled_at"]
    return _close_shadow_from_evidence(
        store,
        strategy_position_id=strategy_position_id,
        closed_at=ts,
        reason=reason,
        exit_fill_id=str(evidence.get("fill_id") or exit_fill_id or ""),
    )


def reconcile_stale_live_sim_shadows(store: TradingStore) -> list[dict[str, Any]]:
    """Maintenance fallback: close open shadows with consumed attribution + exit evidence."""
    rows = store.session.execute(
        text(
            """
            SELECT lsp.id::text AS position_id,
                   lsp.broker_account_id::text AS broker_account_id,
                   i.symbol,
                   lsp.opportunity_key
            FROM live_sim_positions lsp
            JOIN instruments i ON i.id = lsp.instrument_id
            JOIN broker_accounts ba ON ba.id = lsp.broker_account_id
            WHERE lsp.status = 'open'
              AND ba.slug IN ('live-sim-ibkr-like', 'live-sim-kraken-like')
            ORDER BY lsp.opened_at
            """
        )
    ).mappings().all()

    closed: list[dict[str, Any]] = []
    for row in rows:
        if maybe_close_live_sim_shadow_on_attribution_exhausted(
            store,
            strategy_position_id=row["position_id"],
            broker_account_id=row["broker_account_id"],
            symbol=row["symbol"],
            opportunity_key=row.get("opportunity_key"),
            reason="reconcile_stale_shadow_attribution_exhausted",
        ):
            closed.append(
                {
                    "position_id": row["position_id"],
                    "symbol": row["symbol"],
                    "action": "close_shadow_attribution_exhausted",
                }
            )
    return closed
