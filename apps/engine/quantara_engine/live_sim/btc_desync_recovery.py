"""Targeted recovery for Live Sim shadow-closed / broker-open desync.

Uses the first proven SL/TP candle via detect_exit_trigger + PaperBroker,
never current market price. Idempotent.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import text

from quantara_engine.broker.accounts import LIVE_SIM_VIRTUAL_PORTFOLIO_ID
from quantara_engine.broker.attribution import link_strategy_position_to_fill
from quantara_engine.broker.execution_bridge import execute_through_broker
from quantara_engine.domain.types import Direction, Direction as D, Position as DomainPosition
from quantara_engine.execution.cost_profile import execution_assumptions_for
from quantara_engine.execution.crypto_fast_protection import FAST_PROTECTION_TIMEFRAME
from quantara_engine.execution.exit_triggers import detect_exit_trigger
from quantara_engine.execution.paper_broker import PaperBrokerAdapter
from quantara_engine.execution.position_management import _management_candles
from quantara_engine.live_sim.close_authority import (
    finalize_live_sim_position_close,
    live_sim_physical_close_succeeded,
)
from quantara_engine.live_sim.opportunity import live_sim_execution_idempotency_key
from quantara_engine.market_data.polling import is_bar_complete
from quantara_engine.persistence.store import TradingStore

logger = logging.getLogger(__name__)

# Proven BTC Live Sim desync from production-sim audit (2026-09-14).
DEFAULT_BTC_POSITION_ID = "5f30c5ce-d946-4173-8587-af067a7af563"
RECOVERY_IDEMPOTENCY_PREFIX = "btc_shadow_broker_desync_recovery"


def _as_utc(ts: datetime) -> datetime:
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc)


def _broker_net_qty(store: TradingStore, account_id: str, symbol: str) -> Decimal:
    row = store.session.execute(
        text(
            """
            SELECT COALESCE(bp.net_quantity, 0) AS qty
            FROM broker_positions bp
            JOIN instruments i ON i.id = bp.instrument_id
            WHERE bp.broker_account_id = CAST(:aid AS uuid) AND i.symbol = :sym
            """
        ),
        {"aid": account_id, "sym": symbol.upper()},
    ).mappings().first()
    return Decimal(str(row["qty"])) if row else Decimal("0")


def _existing_close_fill_count(
    store: TradingStore,
    *,
    account_id: str,
    symbol: str,
    position_id: str,
) -> int:
    # Recovery idempotency keys are hashed; match by purpose + attribution ledger.
    attributed = store.session.execute(
        text(
            """
            SELECT COUNT(DISTINCT f.id)
            FROM broker_attribution_ledger l
            JOIN broker_fills f ON f.id = l.broker_fill_id
            JOIN broker_orders o ON o.id = f.broker_order_id
            WHERE l.strategy_position_id = CAST(:pid AS uuid)
              AND l.exit_price IS NOT NULL
              AND o.order_purpose IN ('sl', 'tp', 'close')
            """
        ),
        {"pid": position_id},
    ).scalar()
    if attributed:
        return int(attributed)
    return int(
        store.session.execute(
            text(
                """
                SELECT COUNT(*)
                FROM broker_fills f
                JOIN broker_orders o ON o.id = f.broker_order_id
                JOIN instruments i ON i.id = o.instrument_id
                WHERE o.broker_account_id = CAST(:aid AS uuid)
                  AND i.symbol = :sym
                  AND o.order_purpose IN ('sl', 'tp', 'close')
                  AND o.idempotency_key LIKE :pfx || '%'
                """
            ),
            {
                "aid": account_id,
                "sym": symbol.upper(),
                "pfx": RECOVERY_IDEMPOTENCY_PREFIX,
            },
        ).scalar()
        or 0
    )


def _link_attribution(
    store: TradingStore,
    position_id: str,
    account_id: str,
    symbol: str,
    *,
    opportunity_key: str | None = None,
) -> int:
    """Backfill strategy_position_id on open lots for this Live Sim leg only."""
    lots = store.session.execute(
        text(
            """
            SELECT id::text, broker_fill_id::text, opportunity_key
            FROM broker_attribution_lots
            WHERE broker_account_id = CAST(:aid AS uuid)
              AND symbol = :sym
              AND remaining_qty > 0
              AND (
                strategy_position_id = CAST(:pid AS uuid)
                OR (
                  strategy_position_id IS NULL
                  AND (
                    :opp IS NULL
                    OR opportunity_key = :opp
                  )
                )
              )
            """
        ),
        {
            "aid": account_id,
            "sym": symbol.upper(),
            "pid": position_id,
            "opp": opportunity_key,
        },
    ).mappings().all()
    linked = 0
    for lot in lots:
        if opportunity_key and lot.get("opportunity_key") not in (None, opportunity_key):
            if str(lot.get("opportunity_key") or "") != opportunity_key:
                continue
        if lot["broker_fill_id"]:
            link_strategy_position_to_fill(
                store,
                broker_fill_id=lot["broker_fill_id"],
                strategy_position_id=position_id,
            )
            linked += 1
        store.session.execute(
            text(
                """
                UPDATE broker_attribution_lots
                SET strategy_position_id = CAST(:pid AS uuid)
                WHERE id = CAST(:id AS uuid)
                  AND (strategy_position_id IS NULL OR strategy_position_id = CAST(:pid AS uuid))
                """
            ),
            {"pid": position_id, "id": lot["id"]},
        )
    return linked


def _first_trigger(
    store: TradingStore,
    pos: DomainPosition,
    instrument,
    *,
    now: datetime,
):
    candles = store.list_candles(
        instrument.id,
        FAST_PROTECTION_TIMEFRAME,
        since=pos.opened_at,
        limit=5000,
    )
    pending = _management_candles(
        store,
        pos,
        instrument,
        FAST_PROTECTION_TIMEFRAME,
        last_managed=None,
        now=now,
        prefetched=candles,
    )
    for candle in pending:
        if not is_bar_complete(_as_utc(candle.timestamp), FAST_PROTECTION_TIMEFRAME, now):
            continue
        trigger = detect_exit_trigger(pos, candle)
        if trigger:
            return candle, trigger[0], trigger[1]
    return None


def recover_live_sim_btc_shadow_broker_desync(
    store: TradingStore,
    *,
    position_id: str = DEFAULT_BTC_POSITION_ID,
    now: datetime | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """
    Repair one Live Sim BTC case: shadow CLOSED incorrectly while Kraken qty remains.

    Does not create a new entry. Does not close at current market.
    """
    now = now or datetime.now(timezone.utc)
    row = store.session.execute(
        text(
            """
            SELECT p.id::text, p.status, p.instrument_id::text, p.direction::text,
                   p.quantity, p.entry_price, p.stop_loss, p.take_profit,
                   p.timeframe, p.opportunity_key, p.opened_at, p.closed_at,
                   p.broker_account_id::text AS account_id,
                   i.symbol, ba.slug AS broker_account_slug
            FROM live_sim_positions p
            JOIN instruments i ON i.id = p.instrument_id
            JOIN broker_accounts ba ON ba.id = p.broker_account_id
            WHERE p.id = CAST(:pid AS uuid)
            """
        ),
        {"pid": position_id},
    ).mappings().first()
    if not row:
        return {"status": "not_found", "position_id": position_id}

    symbol = str(row["symbol"]).upper()
    account_id = row["account_id"]
    account_slug = row["broker_account_slug"]
    broker_qty = _broker_net_qty(store, account_id, symbol)
    existing_closes = _existing_close_fill_count(
        store, account_id=account_id, symbol=symbol, position_id=position_id
    )

    if abs(broker_qty) == 0 and row["status"] == "closed" and existing_closes > 0:
        return {
            "status": "already_recovered",
            "position_id": position_id,
            "broker_qty": float(broker_qty),
            "close_fills": existing_closes,
            "duplicate_exits": max(0, existing_closes - 1),
        }

    if abs(broker_qty) == 0:
        return {
            "status": "no_broker_qty",
            "position_id": position_id,
            "shadow_status": row["status"],
            "broker_qty": 0.0,
        }

    instrument = store.get_instrument_by_id(row["instrument_id"])
    if not instrument:
        return {"status": "instrument_missing", "position_id": position_id}

    # Re-open shadow for management if incorrectly closed (no physical close yet).
    if row["status"] == "closed" and abs(broker_qty) > 0 and existing_closes == 0:
        if not dry_run:
            store.session.execute(
                text(
                    """
                    UPDATE live_sim_positions
                    SET status = 'open', closed_at = NULL, updated_at = NOW()
                    WHERE id = CAST(:pid AS uuid) AND status = 'closed'
                    """
                ),
                {"pid": position_id},
            )
            logger.info("Reopened incorrectly closed Live Sim shadow %s", position_id)
        reopened = True
    else:
        reopened = False

    linked = _link_attribution(
        store,
        position_id,
        account_id,
        symbol,
        opportunity_key=row.get("opportunity_key"),
    )

    direction = D.LONG if str(row["direction"]).lower() == "long" else D.SHORT
    # Close only remaining physically attributed quantity for this strategy leg.
    from quantara_engine.broker.attribution import attributed_remaining_quantity

    attributed = attributed_remaining_quantity(
        store,
        broker_account_id=account_id,
        symbol=symbol,
        strategy_position_id=position_id,
        portfolio_id=LIVE_SIM_VIRTUAL_PORTFOLIO_ID,
        opportunity_key=row.get("opportunity_key"),
    )
    qty = attributed if attributed > 0 else Decimal(str(row["quantity"]))
    if attributed <= 0 and abs(broker_qty) > 0:
        # Lot still unlinked / mismatched — use remaining broker qty only when this
        # is the sole open lot for the opportunity.
        qty = min(abs(broker_qty), Decimal(str(row["quantity"])))

    pos = DomainPosition(
        id=position_id,
        portfolio_id=LIVE_SIM_VIRTUAL_PORTFOLIO_ID,
        strategy_instance_id="",
        instrument_id=row["instrument_id"],
        direction=direction,
        quantity=qty,
        entry_price=Decimal(str(row["entry_price"])),
        current_price=Decimal(str(row["entry_price"])),
        stop_loss=Decimal(str(row["stop_loss"])),
        take_profit=Decimal(str(row["take_profit"])) if row["take_profit"] else None,
        opened_at=row["opened_at"] or now,
    )

    found = _first_trigger(store, pos, instrument, now=now)
    if not found:
        return {
            "status": "no_trigger_candle",
            "position_id": position_id,
            "broker_qty": float(broker_qty),
            "attributed_qty": float(attributed),
            "linked_lots": linked,
            "reopened": reopened,
        }

    candle, reason, trigger_price = found
    if dry_run:
        return {
            "status": "dry_run",
            "position_id": position_id,
            "trigger": reason.value,
            "trigger_price": float(trigger_price),
            "candle_ts": candle.timestamp.isoformat(),
            "broker_qty": float(broker_qty),
            "close_qty": float(qty),
            "attributed_qty": float(attributed),
            "linked_lots": linked,
            "reopened": reopened,
            "would_reopen": bool(reopened or row["status"] == "closed"),
        }

    assumptions = execution_assumptions_for(instrument, candle.close)
    broker = PaperBrokerAdapter(instrument.id, assumptions)
    close_dir = Direction.SHORT if direction == D.LONG else Direction.LONG
    _, fill = broker.execute_exit_at_trigger(
        direction,
        qty,
        candle,
        trigger_price,
        LIVE_SIM_VIRTUAL_PORTFOLIO_ID,
    )
    purpose = "sl" if reason.value == "sl" else "tp"
    idem = live_sim_execution_idempotency_key(
        account_slug,
        f"{RECOVERY_IDEMPOTENCY_PREFIX}:{position_id}:{candle.timestamp.isoformat()}:{purpose}",
    )
    broker_res = execute_through_broker(
        store,
        portfolio_id=LIVE_SIM_VIRTUAL_PORTFOLIO_ID,
        instrument=instrument,
        direction=close_dir,
        quantity=qty,
        fill=fill,
        execution_at=candle.timestamp,
        timeframe=FAST_PROTECTION_TIMEFRAME,
        idempotency_key=idem,
        is_close=True,
        strategy_position_id=position_id,
        opportunity_key=row.get("opportunity_key"),
        order_purpose=purpose,
        skip_if_not_competition=False,
        account_slug=account_slug,
    )

    closed_shadow = finalize_live_sim_position_close(
        store,
        position_id=position_id,
        closed_at=candle.timestamp,
        account_slug=account_slug,
        instrument_symbol=instrument.symbol,
        mark_price=candle.close,
        broker_res=broker_res,
        requested_quantity=qty,
    )

    # Cancel leftover protective orders for this symbol if flat.
    after_qty = _broker_net_qty(store, account_id, symbol)
    if abs(after_qty) == 0:
        bp_id = store.session.execute(
            text(
                """
                SELECT bp.id::text
                FROM broker_positions bp
                JOIN instruments i ON i.id = bp.instrument_id
                WHERE bp.broker_account_id = CAST(:aid AS uuid) AND i.symbol = :sym
                """
            ),
            {"aid": account_id, "sym": symbol},
        ).scalar()
        if bp_id:
            from quantara_engine.broker.protective_orders import (
                cancel_protective_orders_for_position,
            )

            cancel_protective_orders_for_position(store, broker_position_id=str(bp_id))

    close_fills = _existing_close_fill_count(
        store, account_id=account_id, symbol=symbol, position_id=position_id
    )
    remaining_after = attributed_remaining_quantity(
        store,
        broker_account_id=account_id,
        symbol=symbol,
        strategy_position_id=position_id,
        portfolio_id=LIVE_SIM_VIRTUAL_PORTFOLIO_ID,
        opportunity_key=row.get("opportunity_key"),
    )
    if live_sim_physical_close_succeeded(broker_res) and remaining_after <= 0 and not closed_shadow:
        closed_shadow = finalize_live_sim_position_close(
            store,
            position_id=position_id,
            closed_at=candle.timestamp,
            account_slug=account_slug,
            instrument_symbol=instrument.symbol,
            mark_price=candle.close,
            broker_res=broker_res,
            requested_quantity=qty,
        )

    ok = (
        live_sim_physical_close_succeeded(broker_res)
        and remaining_after <= 0
        and closed_shadow
        and close_fills >= 1
    )
    return {
        "status": "recovered" if ok else "incomplete",
        "position_id": position_id,
        "trigger": purpose,
        "trigger_price": float(trigger_price),
        "fill_price": float(fill.fill_price),
        "candle_ts": candle.timestamp.isoformat(),
        "broker_order_id": broker_res.broker_order_id if broker_res else None,
        "broker_fill_id": broker_res.broker_fill_id if broker_res else None,
        "realized_pnl": float(broker_res.realized_pnl) if broker_res and broker_res.realized_pnl is not None else None,
        "shadow_closed": closed_shadow,
        "broker_qty_after": float(after_qty),
        "attributed_remaining_after": float(remaining_after),
        "close_fills": close_fills,
        "duplicate_exits": max(0, close_fills - 1),
        "physical_succeeded": live_sim_physical_close_succeeded(broker_res),
        "linked_lots": linked,
        "shadow_only": bool(broker_res.shadow_only) if broker_res else None,
    }
