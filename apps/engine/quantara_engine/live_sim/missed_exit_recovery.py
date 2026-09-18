"""Deterministic Live Sim missed-exit recovery at first provable SL/TP trigger."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import text

from quantara_engine.broker.accounts import LIVE_SIM_VIRTUAL_PORTFOLIO_ID
from quantara_engine.broker.attribution import attributed_remaining_quantity
from quantara_engine.broker.execution_bridge import execute_through_broker
from quantara_engine.domain.types import Direction, Direction as D, Position as DomainPosition
from quantara_engine.execution.cost_profile import execution_assumptions_for
from quantara_engine.execution.crypto_fast_protection import (
    FALLBACK_TIMEFRAME,
    FAST_PROTECTION_TIMEFRAME,
)
from quantara_engine.execution.exit_triggers import detect_exit_trigger
from quantara_engine.execution.paper_broker import PaperBrokerAdapter
from quantara_engine.execution.position_management import _management_candles
from quantara_engine.live_sim.btc_desync_recovery import (
    _broker_net_qty,
    _existing_close_fill_count,
    _link_attribution,
)
from quantara_engine.live_sim.close_authority import (
    finalize_live_sim_position_close,
    live_sim_physical_close_succeeded,
)
from quantara_engine.live_sim.entry_authority import position_has_physical_entry_evidence
from quantara_engine.live_sim.opportunity import live_sim_execution_idempotency_key
from quantara_engine.market_data.polling import is_bar_complete
from quantara_engine.persistence.store import TradingStore

logger = logging.getLogger(__name__)

RECOVERY_IDEMPOTENCY_PREFIX = "protection_outage_recovery"


def _as_utc(ts: datetime) -> datetime:
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc)


def _first_trigger(
    store: TradingStore,
    pos: DomainPosition,
    instrument,
    *,
    now: datetime,
) -> tuple[Any, Any, Decimal, str] | None:
    for timeframe in (FAST_PROTECTION_TIMEFRAME, FALLBACK_TIMEFRAME):
        candles = store.list_candles(
            instrument.id,
            timeframe,
            since=pos.opened_at,
            limit=5000,
        )
        pending = _management_candles(
            store,
            pos,
            instrument,
            timeframe,
            last_managed=None,
            now=now,
            prefetched=candles,
        )
        for candle in pending:
            if not is_bar_complete(_as_utc(candle.timestamp), timeframe, now):
                continue
            trigger = detect_exit_trigger(pos, candle)
            if trigger:
                reason, trigger_price = trigger
                return candle, reason, trigger_price, timeframe
    return None


def _close_shadow_only(
    store: TradingStore,
    *,
    position_id: str,
    closed_at: datetime,
    reason: str,
    trigger_ts: datetime,
) -> None:
    store.session.execute(
        text(
            """
            UPDATE live_sim_positions
            SET status = 'closed', closed_at = :ts, updated_at = NOW()
            WHERE id = CAST(:pid AS uuid) AND status = 'open'
            """
        ),
        {"pid": position_id, "ts": closed_at},
    )
    store.session.execute(
        text(
            """
            UPDATE broker_attribution_lots
            SET remaining_qty = 0
            WHERE strategy_position_id = CAST(:pid AS uuid) AND remaining_qty > 0
            """
        ),
        {"pid": position_id},
    )
    store.update_settings(
        f"live_sim_integrity:protection_recovery:{position_id}",
        {
            "reason": reason,
            "trigger_ts": trigger_ts.isoformat(),
            "recovered_at": datetime.now(timezone.utc).isoformat(),
            "broker_flat": True,
        },
        flush=False,
    )


def recover_missed_live_sim_exits(
    store: TradingStore,
    *,
    now: datetime | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Recover open Live Sim positions with provable historical SL/TP triggers."""
    now = now or datetime.now(timezone.utc)
    rows = store.session.execute(
        text(
            """
            SELECT p.id::text, p.status, p.instrument_id::text, p.direction::text,
                   p.quantity, p.entry_price, p.stop_loss, p.take_profit,
                   p.timeframe, p.opportunity_key, p.opened_at,
                   p.broker_account_id::text AS account_id,
                   i.symbol, ba.slug AS broker_account_slug
            FROM live_sim_positions p
            JOIN instruments i ON i.id = p.instrument_id
            JOIN broker_accounts ba ON ba.id = p.broker_account_id
            WHERE p.status = 'open'
            ORDER BY p.opened_at
            """
        )
    ).mappings().all()

    recovered: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []

    for row in rows:
        position_id = row["id"]
        symbol = str(row["symbol"]).upper()
        account_id = row["account_id"]
        account_slug = row["broker_account_slug"]
        broker_qty = _broker_net_qty(store, account_id, symbol)
        has_entry = position_has_physical_entry_evidence(
            store,
            position_id=position_id,
            broker_account_id=account_id,
            symbol=symbol,
            opportunity_key=row.get("opportunity_key"),
        )

        direction = D.LONG if str(row["direction"]).lower() == "long" else D.SHORT
        pos = DomainPosition(
            id=position_id,
            portfolio_id=LIVE_SIM_VIRTUAL_PORTFOLIO_ID,
            strategy_instance_id="",
            instrument_id=row["instrument_id"],
            direction=direction,
            quantity=Decimal(str(row["quantity"])),
            entry_price=Decimal(str(row["entry_price"])),
            current_price=Decimal(str(row["entry_price"])),
            stop_loss=Decimal(str(row["stop_loss"])),
            take_profit=Decimal(str(row["take_profit"])) if row["take_profit"] else None,
            opened_at=row["opened_at"] or now,
        )
        instrument = store.get_instrument_by_id(row["instrument_id"])
        if not instrument:
            skipped.append({"position_id": position_id, "reason": "no_instrument"})
            continue

        found = _first_trigger(store, pos, instrument, now=now)
        if not found:
            skipped.append({"position_id": position_id, "symbol": symbol, "reason": "no_proven_trigger"})
            continue

        candle, reason, trigger_price, source_tf = found
        purpose = "sl" if reason.value == "sl" else "tp"

        if abs(broker_qty) == 0:
            if not has_entry:
                action = "close_orphan_shadow_no_physical_entry"
            else:
                existing_closes = _existing_close_fill_count(
                    store, account_id=account_id, symbol=symbol, position_id=position_id
                )
                if existing_closes > 0:
                    action = "close_shadow_broker_already_flat"
                else:
                    action = "close_shadow_broker_flat_no_exit_fill"
            if dry_run:
                recovered.append(
                    {
                        "position_id": position_id,
                        "symbol": symbol,
                        "dry_run": True,
                        "action": action,
                        "trigger": purpose,
                        "trigger_ts": candle.timestamp.isoformat(),
                        "broker_qty_before": 0.0,
                    }
                )
                continue
            _close_shadow_only(
                store,
                position_id=position_id,
                closed_at=candle.timestamp,
                reason=action,
                trigger_ts=candle.timestamp,
            )
            recovered.append(
                {
                    "position_id": position_id,
                    "symbol": symbol,
                    "action": action,
                    "trigger": purpose,
                    "trigger_ts": candle.timestamp.isoformat(),
                    "broker_qty_before": 0.0,
                    "broker_qty_after": 0.0,
                    "physical_fill": False,
                }
            )
            continue

        linked = _link_attribution(
            store,
            position_id,
            account_id,
            symbol,
            opportunity_key=row.get("opportunity_key"),
        )
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
            qty = min(abs(broker_qty), Decimal(str(row["quantity"])))
        pos.quantity = qty

        if dry_run:
            recovered.append(
                {
                    "position_id": position_id,
                    "symbol": symbol,
                    "dry_run": True,
                    "action": "physical_recovery",
                    "trigger": purpose,
                    "trigger_ts": candle.timestamp.isoformat(),
                    "broker_qty_before": float(broker_qty),
                    "close_qty": float(qty),
                }
            )
            continue

        assumptions = execution_assumptions_for(instrument, candle.close)
        paper = PaperBrokerAdapter(instrument.id, assumptions)
        close_dir = Direction.SHORT if direction == D.LONG else Direction.LONG
        _, fill = paper.execute_exit_at_trigger(
            direction,
            qty,
            candle,
            trigger_price,
            LIVE_SIM_VIRTUAL_PORTFOLIO_ID,
        )
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
            timeframe=source_tf,
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
        after_qty = _broker_net_qty(store, account_id, symbol)
        recovered.append(
            {
                "position_id": position_id,
                "symbol": symbol,
                "action": "physical_recovery",
                "trigger": purpose,
                "trigger_ts": candle.timestamp.isoformat(),
                "fill_price": float(fill.fill_price),
                "realized_pnl": float(broker_res.realized_pnl)
                if broker_res and broker_res.realized_pnl is not None
                else None,
                "broker_qty_before": float(broker_qty),
                "broker_qty_after": float(after_qty),
                "shadow_closed": closed_shadow,
                "physical_succeeded": live_sim_physical_close_succeeded(broker_res),
                "linked_lots": linked,
            }
        )

    store.session.flush()
    return {
        "recovered": recovered,
        "skipped": skipped,
        "recovered_count": len(recovered),
        "skipped_count": len(skipped),
    }
