"""SL/TP management for live-sim positions — exits never blocked by entry gates."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from decimal import Decimal as Dc

from sqlalchemy import text

from quantara_engine.broker.accounts import LIVE_SIM_VIRTUAL_PORTFOLIO_ID
from quantara_engine.broker.execution_bridge import execute_through_broker
from quantara_engine.domain.types import Direction
from quantara_engine.domain.types import Direction as D
from quantara_engine.domain.types import Position as DomainPosition
from quantara_engine.execution.cost_profile import execution_assumptions_for
from quantara_engine.execution.exit_triggers import detect_exit_trigger
from quantara_engine.execution.paper_broker import PaperBrokerAdapter
from quantara_engine.broker.instruments import get_instrument_spec
from quantara_engine.broker.pnl import unrealized_pnl_usd
from quantara_engine.live_sim.close_authority import (
    attributed_lot_remaining,
    finalize_live_sim_position_close,
)
from quantara_engine.live_sim.execution_routing import query_open_live_sim_position_rows
from quantara_engine.live_sim.opportunity import live_sim_execution_idempotency_key
from quantara_engine.persistence.store import TradingStore

logger = logging.getLogger(__name__)
EXIT_RESUME_AFTER_KEY = "live_sim_exit_resume_after"


def _exit_resume_after(store: TradingStore) -> dict[str, datetime]:
    raw = store.get_settings_dict().get(EXIT_RESUME_AFTER_KEY) or {}
    if not isinstance(raw, dict):
        return {}
    out: dict[str, datetime] = {}
    for position_id, stamp in raw.items():
        try:
            parsed = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
        except ValueError:
            continue
        out[str(position_id)] = parsed
    return out


def _as_utc(ts: datetime | None) -> datetime | None:
    if ts is None:
        return None
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc)


def _persist_open_mark(store: TradingStore, row: dict, mark: Dc) -> None:
    qty = Dc(str(row["quantity"]))
    signed = qty if str(row["direction"]).lower() == "long" else -qty
    entry = Dc(str(row["entry_price"]))
    try:
        spec = get_instrument_spec(str(row["symbol"]))
        upnl = unrealized_pnl_usd(signed, entry, mark, spec, {"USD": Dc("1")})
    except Exception:
        upnl = (mark - entry) * signed
    store.session.execute(
        text(
            """
            UPDATE live_sim_positions
            SET current_price = :mark, unrealized_pnl = :upnl, updated_at = NOW()
            WHERE id = :id AND status = 'open'
            """
        ),
        {"id": row["id"], "mark": mark, "upnl": upnl},
    )


def process_live_sim_exits(store: TradingStore, now: datetime) -> dict:
    rows = query_open_live_sim_position_rows(store)
    if not rows:
        return {"closed": 0}

    from quantara_engine.execution.crypto_mark_valuation import (
        should_skip_5m_position_management,
    )

    closed = 0
    resume_after = _exit_resume_after(store)
    for row in rows:
        if should_skip_5m_position_management(store, row["symbol"], now=now):
            continue
        instrument = store.get_instrument_by_id(row["instrument_id"])
        if not instrument:
            continue
        candles = store.list_recent_candles(instrument.id, row["timeframe"], limit=30)
        if not candles:
            continue

        direction = D.LONG if str(row["direction"]).lower() == "long" else D.SHORT
        account_slug = str(row["broker_account_slug"])
        qty = Dc(str(row["quantity"]))
        lot_qty = attributed_lot_remaining(store, row["id"])
        if lot_qty is not None and lot_qty > 0 and lot_qty != qty:
            qty = lot_qty
            row["quantity"] = lot_qty
            store.session.execute(
                text(
                    """
                    UPDATE live_sim_positions
                    SET quantity = :qty, updated_at = NOW()
                    WHERE id = :id AND status = 'open'
                    """
                ),
                {"id": row["id"], "qty": lot_qty},
            )
        opened_at = row.get("opened_at")
        resume_at = resume_after.get(str(row["id"]))
        eligible = []
        opened_utc = _as_utc(opened_at)
        resume_utc = _as_utc(resume_at)
        for candle in candles:
            candle_utc = _as_utc(candle.timestamp)
            if opened_utc is not None and candle_utc is not None and candle_utc < opened_utc:
                continue
            if resume_utc is not None and candle_utc is not None and candle_utc <= resume_utc:
                continue
            eligible.append(candle)
        if eligible:
            _persist_open_mark(store, row, Dc(str(eligible[-1].close)))
        pos = DomainPosition(
            id=row["id"],
            portfolio_id=LIVE_SIM_VIRTUAL_PORTFOLIO_ID,
            strategy_instance_id="",
            instrument_id=row["instrument_id"],
            direction=direction,
            quantity=qty,
            entry_price=Dc(str(row["entry_price"])),
            current_price=Dc(str(row["entry_price"])),
            stop_loss=Dc(str(row["stop_loss"])),
            take_profit=Dc(str(row["take_profit"])) if row["take_profit"] else None,
            opened_at=row.get("opened_at") or now,
        )

        for candle in eligible:
            trigger = detect_exit_trigger(pos, candle)
            if not trigger:
                continue
            reason, trigger_price = trigger
            assumptions = execution_assumptions_for(instrument, candle.close)
            broker = PaperBrokerAdapter(instrument.id, assumptions)
            close_dir = Direction.SHORT if direction == D.LONG else Direction.LONG
            _, fill = broker.execute_exit_at_trigger(
                direction,
                pos.quantity,
                candle,
                trigger_price,
                LIVE_SIM_VIRTUAL_PORTFOLIO_ID,
            )
            purpose = "sl" if reason.value == "sl" else "tp"
            idem = live_sim_execution_idempotency_key(
                account_slug,
                f"exit:{row['id']}:{candle.timestamp.isoformat()}:{purpose}",
            )
            broker_res = execute_through_broker(
                store,
                portfolio_id=LIVE_SIM_VIRTUAL_PORTFOLIO_ID,
                instrument=instrument,
                direction=close_dir,
                quantity=pos.quantity,
                fill=fill,
                execution_at=candle.timestamp,
                timeframe=row["timeframe"],
                idempotency_key=idem,
                is_close=True,
                strategy_position_id=row["id"],
                opportunity_key=row.get("opportunity_key"),
                order_purpose=purpose,
                skip_if_not_competition=False,
                account_slug=account_slug,
            )
            if finalize_live_sim_position_close(
                store,
                position_id=row["id"],
                closed_at=candle.timestamp,
                account_slug=account_slug,
                instrument_symbol=instrument.symbol,
                mark_price=candle.close,
                broker_res=broker_res,
                requested_quantity=qty,
            ):
                closed += 1
                logger.info(
                    "Live-sim closed %s via %s on %s",
                    instrument.symbol,
                    purpose,
                    account_slug,
                )
            break

    return {"closed": closed, "open_checked": len(rows)}
