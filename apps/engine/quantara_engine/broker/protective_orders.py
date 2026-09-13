"""Broker-native simulated protective orders (SL/TP/OCO)."""

from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import text

from quantara_engine.broker.client_order_id import derive_client_order_id
from quantara_engine.persistence.store import TradingStore


def create_protective_orders_for_position(
    store: TradingStore,
    *,
    broker_account_id: str,
    broker_position_id: str,
    symbol: str,
    direction: str,
    quantity: Decimal,
    stop_loss: Decimal | None,
    take_profit: Decimal | None,
    account_slug: str,
    opportunity_key: str | None = None,
) -> list[str]:
    """Create broker-side SL/TP with OCO linkage; quantity matches filled position."""
    if stop_loss is None and take_profit is None:
        return []
    if quantity <= 0:
        return []

    created: list[str] = []
    sl_id: str | None = None
    tp_id: str | None = None
    dir_norm = direction.lower()

    if stop_loss is not None and stop_loss > 0:
        sl_id = str(uuid.uuid4())
        client_id = derive_client_order_id(
            account_slug=account_slug,
            idempotency_key=f"prot-sl:{broker_position_id}:{opportunity_key or sl_id}",
        )
        store.session.execute(
            text(
                """
                INSERT INTO broker_protective_orders (
                  id, broker_account_id, broker_position_id, protective_type,
                  trigger_price, status, reduce_only, client_order_id, quantity
                ) VALUES (
                  :id, :aid, :pid, 'stop_loss', :price, 'active', TRUE, :cid, :qty
                )
                """
            ),
            {
                "id": sl_id,
                "aid": broker_account_id,
                "pid": broker_position_id,
                "price": stop_loss,
                "cid": client_id,
                "qty": quantity,
            },
        )
        created.append(sl_id)

    if take_profit is not None and take_profit > 0:
        tp_id = str(uuid.uuid4())
        client_id = derive_client_order_id(
            account_slug=account_slug,
            idempotency_key=f"prot-tp:{broker_position_id}:{opportunity_key or tp_id}",
        )
        store.session.execute(
            text(
                """
                INSERT INTO broker_protective_orders (
                  id, broker_account_id, broker_position_id, protective_type,
                  trigger_price, status, reduce_only, client_order_id, quantity
                ) VALUES (
                  :id, :aid, :pid, 'take_profit', :price, 'active', TRUE, :cid, :qty
                )
                """
            ),
            {
                "id": tp_id,
                "aid": broker_account_id,
                "pid": broker_position_id,
                "price": take_profit,
                "cid": client_id,
                "qty": quantity,
            },
        )
        created.append(tp_id)

    if sl_id and tp_id:
        store.session.execute(
            text("UPDATE broker_protective_orders SET oco_sibling_id = :sib WHERE id = :id"),
            {"sib": tp_id, "id": sl_id},
        )
        store.session.execute(
            text("UPDATE broker_protective_orders SET oco_sibling_id = :sib WHERE id = :id"),
            {"sib": sl_id, "id": tp_id},
        )

    return created


def sync_protective_quantity(
    store: TradingStore,
    *,
    broker_position_id: str,
    quantity: Decimal,
) -> None:
    """Adjust active protective order quantity after partial/additional fills."""
    if quantity <= 0:
        cancel_protective_orders_for_position(store, broker_position_id=broker_position_id)
        return
    try:
        store.session.execute(
            text(
                """
                UPDATE broker_protective_orders
                SET quantity = :qty, updated_at = NOW()
                WHERE broker_position_id = :pid AND status = 'active'
                """
            ),
            {"pid": broker_position_id, "qty": quantity},
        )
    except Exception:
        store.session.rollback()


def cancel_protective_orders_for_position(
    store: TradingStore,
    *,
    broker_position_id: str,
    exclude_id: str | None = None,
) -> int:
    """Cancel active protective orders — OCO-safe when one leg fills."""
    try:
        result = store.session.execute(
            text(
                """
                UPDATE broker_protective_orders
                SET status = 'cancelled', cancelled_at = NOW(), updated_at = NOW()
                WHERE broker_position_id = :pid
                  AND status = 'active'
                  AND (:exclude IS NULL OR id::text != :exclude)
                """
            ),
            {"pid": broker_position_id, "exclude": exclude_id},
        )
        return result.rowcount or 0
    except Exception:
        store.session.rollback()
        return 0


def mark_protective_triggered(
    store: TradingStore,
    *,
    protective_id: str,
    filled: bool = False,
) -> None:
    status = "filled" if filled else "triggered"
    store.session.execute(
        text(
            """
            UPDATE broker_protective_orders
            SET status = :st, triggered_at = NOW(), updated_at = NOW()
            WHERE id = :id
            """
        ),
        {"id": protective_id, "st": status},
    )
