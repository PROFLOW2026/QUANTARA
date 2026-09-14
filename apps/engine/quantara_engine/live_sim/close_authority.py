"""Canonical Live Sim close-success authority.

Broker fills/orders are financial truth. live_sim_positions is strategy/shadow
attribution and may only become CLOSED after a confirmed physical broker close.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import text

from quantara_engine.broker.execution_service import BrokerExecutionResult, BrokerExecutionService
from quantara_engine.persistence.store import TradingStore


def live_sim_physical_close_succeeded(result: BrokerExecutionResult | None) -> bool:
    """True only when a non-shadow physical close fill is confirmed."""
    if result is None or not result.accepted:
        return False
    if result.shadow_only:
        return False
    closed_qty = Decimal(str(result.physical_closed_qty or 0))
    fill_qty = Decimal(str(result.fill_quantity or 0))
    if closed_qty > 0:
        return True
    # Idempotent retry of a real fill: fill_id present with positive quantity.
    if result.broker_fill_id and fill_qty > 0 and result.broker_order_id:
        return True
    return False


def live_sim_close_is_partial(
    result: BrokerExecutionResult | None,
    *,
    requested_quantity: Decimal,
) -> bool:
    """True when physical close occurred but remaining attributed qty may remain."""
    if not live_sim_physical_close_succeeded(result) or result is None:
        return False
    closed = Decimal(str(result.physical_closed_qty or result.fill_quantity or 0))
    return closed > 0 and closed < requested_quantity


def finalize_live_sim_position_close(
    store: TradingStore,
    *,
    position_id: str,
    closed_at: datetime,
    account_slug: str,
    instrument_symbol: str,
    mark_price: Decimal,
    broker_res: BrokerExecutionResult | None,
    requested_quantity: Decimal | None = None,
) -> bool:
    """
    Mark live_sim_positions CLOSED only after physical broker close success.

    Returns True when the shadow row was closed.
    Partial physical closes update remaining quantity and leave status OPEN.
    """
    if not live_sim_physical_close_succeeded(broker_res) or broker_res is None:
        return False

    closed_qty = Decimal(str(broker_res.physical_closed_qty or broker_res.fill_quantity or 0))
    if requested_quantity is not None and closed_qty > 0 and closed_qty < requested_quantity:
        remaining = requested_quantity - closed_qty
        store.session.execute(
            text(
                """
                UPDATE live_sim_positions
                SET quantity = :qty, updated_at = NOW()
                WHERE id = :id AND status = 'open'
                """
            ),
            {"id": position_id, "qty": remaining},
        )
        svc = BrokerExecutionService(store, account_slug=account_slug)
        svc.mark_to_market({instrument_symbol.upper(): mark_price}, at=closed_at)
        return False

    store.session.execute(
        text(
            """
            UPDATE live_sim_positions
            SET status = 'closed', closed_at = :ts, updated_at = NOW()
            WHERE id = :id AND status = 'open'
            """
        ),
        {"id": position_id, "ts": closed_at},
    )
    svc = BrokerExecutionService(store, account_slug=account_slug)
    svc.mark_to_market({instrument_symbol.upper(): mark_price}, at=closed_at)

    try:
        from quantara_engine.owner_portfolio.asset_ledger import refresh_all_asset_states_from_positions
        from quantara_engine.owner_portfolio.service import LIVE_SIM_OWNER_SLUG

        refresh_all_asset_states_from_positions(store, LIVE_SIM_OWNER_SLUG)
    except Exception:
        pass

    return True
