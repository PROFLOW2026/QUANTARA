"""SL/TP management for live-sim positions — exits never blocked by entry gates."""

from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy import text

from quantara_engine.broker.accounts import LIVE_SIM_VIRTUAL_PORTFOLIO_ID
from quantara_engine.broker.execution_bridge import execute_through_broker
from quantara_engine.broker.execution_service import BrokerExecutionService
from quantara_engine.domain.types import Direction
from quantara_engine.execution.cost_profile import execution_assumptions_for
from quantara_engine.execution.paper_broker import PaperBrokerAdapter
from quantara_engine.execution.exit_triggers import detect_exit_trigger
from quantara_engine.live_sim.execution_routing import query_open_live_sim_position_rows
from quantara_engine.live_sim.opportunity import live_sim_execution_idempotency_key
from quantara_engine.persistence.store import TradingStore

logger = logging.getLogger(__name__)


def process_live_sim_exits(store: TradingStore, now: datetime) -> dict:
    rows = query_open_live_sim_position_rows(store)
    if not rows:
        return {"closed": 0}

    from quantara_engine.execution.crypto_mark_valuation import (
        should_skip_5m_position_management,
    )

    closed = 0
    for row in rows:
        if should_skip_5m_position_management(store, row["symbol"], now=now):
            continue
        instrument = store.get_instrument_by_id(row["instrument_id"])
        if not instrument:
            continue
        candles = store.list_recent_candles(instrument.id, row["timeframe"], limit=30)
        if not candles:
            continue

        from decimal import Decimal as Dc

        from quantara_engine.domain.types import Direction as D
        from quantara_engine.domain.types import Position as DomainPosition

        direction = D.LONG if str(row["direction"]).lower() == "long" else D.SHORT
        account_slug = str(row["broker_account_slug"])
        pos = DomainPosition(
            id=row["id"],
            portfolio_id=LIVE_SIM_VIRTUAL_PORTFOLIO_ID,
            strategy_instance_id="",
            instrument_id=row["instrument_id"],
            direction=direction,
            quantity=Dc(str(row["quantity"])),
            entry_price=Dc(str(row["entry_price"])),
            current_price=Dc(str(row["entry_price"])),
            stop_loss=Dc(str(row["stop_loss"])),
            take_profit=Dc(str(row["take_profit"])) if row["take_profit"] else None,
            opened_at=now,
        )

        for candle in candles:
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
                order_purpose=purpose,
                skip_if_not_competition=False,
                account_slug=account_slug,
            )
            if broker_res and broker_res.accepted:
                store.session.execute(
                    text(
                        """
                        UPDATE live_sim_positions
                        SET status = 'closed', closed_at = :ts, updated_at = NOW()
                        WHERE id = :id
                        """
                    ),
                    {"id": row["id"], "ts": candle.timestamp},
                )
                svc = BrokerExecutionService(store, account_slug=account_slug)
                svc.mark_to_market({instrument.symbol.upper(): candle.close}, at=candle.timestamp)
                closed += 1
                logger.info("Live-sim closed %s via %s on %s", instrument.symbol, purpose, account_slug)
            break

    return {"closed": closed, "open_checked": len(rows)}
