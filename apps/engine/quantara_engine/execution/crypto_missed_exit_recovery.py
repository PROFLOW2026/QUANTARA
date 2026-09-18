"""Deterministic recovery of Research crypto positions that missed SL/TP protection."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from quantara_engine.domain.types import DecisionLogEntry, DecisionType, ExitReason, new_id
from quantara_engine.execution.cost_profile import execution_assumptions_for
from quantara_engine.execution.crypto_fast_protection import (
    FALLBACK_TIMEFRAME,
    FAST_PROTECTION_5M_IDEMPOTENCY_PREFIX,
    FAST_PROTECTION_IDEMPOTENCY_PREFIX,
    FAST_PROTECTION_TIMEFRAME,
)
from quantara_engine.execution.exit_triggers import detect_exit_trigger
from quantara_engine.execution.paper_broker import PaperBrokerAdapter
from quantara_engine.execution.position_management import _management_candles
from quantara_engine.market_data.polling import is_bar_complete

logger = logging.getLogger(__name__)


def _as_utc(ts: datetime) -> datetime:
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc)


def _first_trigger_candle(
    store,
    position,
    instrument,
    timeframe: str,
    *,
    now: datetime,
) -> tuple[Any, ExitReason, Decimal] | None:
    candles = store.list_candles(
        instrument.id,
        timeframe,
        since=position.opened_at,
        limit=5000,
    )
    pending = _management_candles(
        store,
        position,
        instrument,
        timeframe,
        last_managed=None,
        now=now,
        prefetched=candles,
    )
    for candle in pending:
        if not is_bar_complete(_as_utc(candle.timestamp), timeframe, now):
            continue
        trigger = detect_exit_trigger(position, candle)
        if trigger:
            reason, trigger_price = trigger
            return candle, reason, trigger_price
    return None


def recover_missed_research_crypto_exits(
    store,
    *,
    now: datetime | None = None,
    symbols: set[str] | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """
    Close only Research crypto positions that missed SL/TP due to protection failure.

    Uses 1m first; if no 1m trigger exists across available bars, uses 5m fallback.
    Exit pricing uses the normal PaperBrokerAdapter path (not current market).
    """
    from quantara_engine.broker.execution_bridge import execute_through_broker
    from quantara_engine.domain.types import Direction
    from quantara_engine.execution.crypto_mark_valuation import is_fast_protection_crypto
    from quantara_engine.portfolio.currency import build_currency_context

    now = now or datetime.now(timezone.utc)
    _, _, entries = store.list_all_competition_entries()

    recovered: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    seen_position_ids: set[str] = set()

    for entry in entries:
        instance = entry["instance"]
        portfolio = entry["portfolio"]
        state = store.load_portfolio_state(portfolio.id)
        for position in list(state.open_positions()):
            if position.id in seen_position_ids:
                continue
            seen_position_ids.add(position.id)
            owning = instance if position.strategy_instance_id == instance.id else None
            if owning is None:
                continue
            instrument = store.get_instrument_by_id(position.instrument_id)
            if not instrument or not is_fast_protection_crypto(instrument.symbol):
                continue
            sym = instrument.symbol.upper().replace("/", "")
            if symbols is not None and sym not in symbols:
                continue
            if store.trade_exists_for_position(position.id):
                skipped.append({"position_id": position.id, "reason": "trade_exists"})
                continue

            hit = _first_trigger_candle(
                store, position, instrument, FAST_PROTECTION_TIMEFRAME, now=now
            )
            source = FAST_PROTECTION_TIMEFRAME
            if hit is None:
                hit = _first_trigger_candle(
                    store, position, instrument, FALLBACK_TIMEFRAME, now=now
                )
                source = FALLBACK_TIMEFRAME
            if hit is None:
                skipped.append(
                    {
                        "position_id": position.id,
                        "symbol": sym,
                        "reason": "no_proven_trigger",
                    }
                )
                continue

            candle, reason, trigger_price = hit
            if dry_run:
                recovered.append(
                    {
                        "position_id": position.id,
                        "symbol": sym,
                        "dry_run": True,
                        "source_timeframe": source,
                        "trigger_reason": reason.value,
                        "trigger_ts": candle.timestamp.isoformat(),
                        "trigger_price": float(trigger_price),
                    }
                )
                continue

            nested = store.session.begin_nested()
            try:
                state = store.load_portfolio_state(position.portfolio_id)
                open_pos = next(
                    (p for p in state.open_positions() if p.id == position.id), None
                )
                if open_pos is None:
                    nested.rollback()
                    skipped.append(
                        {"position_id": position.id, "reason": "not_open_in_state"}
                    )
                    continue

                assumptions = execution_assumptions_for(instrument, candle.close)
                broker = PaperBrokerAdapter(instrument.id, assumptions)
                gap_exit = trigger_price == candle.open
                order, fill = broker.execute_exit_at_trigger(
                    open_pos.direction,
                    open_pos.quantity,
                    candle,
                    trigger_price,
                    state.portfolio.id,
                    gap_exit=gap_exit,
                )
                close_dir = (
                    Direction.SHORT
                    if open_pos.direction.value == "long"
                    else Direction.LONG
                )
                purpose = "sl" if reason.value == "sl" else "tp"
                prefix = (
                    FAST_PROTECTION_5M_IDEMPOTENCY_PREFIX
                    if source == FALLBACK_TIMEFRAME
                    else FAST_PROTECTION_IDEMPOTENCY_PREFIX
                )
                broker_res = execute_through_broker(
                    store,
                    portfolio_id=state.portfolio.id,
                    instrument=instrument,
                    direction=close_dir,
                    quantity=open_pos.quantity,
                    fill=fill,
                    execution_at=candle.timestamp,
                    timeframe=source,
                    idempotency_key=(
                        f"{prefix}:recovery:{purpose}:{open_pos.id}:"
                        f"{candle.timestamp.isoformat()}"
                    ),
                    is_close=True,
                    strategy_position_id=open_pos.id,
                    order_purpose=purpose,
                )
                if broker_res is not None and not broker_res.accepted:
                    nested.rollback()
                    skipped.append(
                        {
                            "position_id": position.id,
                            "reason": "broker_rejected",
                            "detail": getattr(broker_res, "decision", None),
                        }
                    )
                    continue

                ctx = build_currency_context(store, [instrument])
                if broker_res is not None and broker_res.shadow_only:
                    from quantara_engine.broker.lifecycle import close_shadow_position

                    trade = close_shadow_position(
                        state,
                        open_pos,
                        exit_reason=reason,
                        closed_at=candle.timestamp,
                        currency=ctx,
                        exit_price=trigger_price,
                    )
                else:
                    trade = state.close_position(
                        open_pos, fill, reason, candle.timestamp, ctx
                    )

                decision = DecisionLogEntry(
                    id=new_id(),
                    strategy_instance_id=owning.id,
                    instrument_id=instrument.id,
                    candle_timestamp=candle.timestamp,
                    decision_type=(
                        DecisionType.SL_TRIGGERED
                        if reason == ExitReason.SL
                        else DecisionType.TP_TRIGGERED
                    ),
                    message=(
                        f"RECOVERED missed {reason.value.upper()} at {trigger_price} "
                        f"via {source}"
                    ),
                    signal_id=None,
                    metadata={
                        "position_management": True,
                        "recovered_missed_protection": True,
                        "source_timeframe": source,
                        "recovery_reason": "protection_outage_recovery",
                    },
                )
                exit_snapshot = state.create_snapshot(candle.timestamp)
                store.persist_exit_execution(
                    order=order,
                    fill=fill,
                    position=open_pos,
                    trade=trade,
                    portfolio_state=state,
                    strategy_instance_id=owning.id,
                    filled_at=candle.timestamp,
                )
                store.save_decision(decision)
                store.save_snapshot(exit_snapshot)
                nested.commit()

                risk_tier = None
                try:
                    from quantara_engine.competition.constants import PORTFOLIO_DEF_BY_ID

                    pdef = PORTFOLIO_DEF_BY_ID.get(position.portfolio_id)
                    if pdef is not None:
                        risk_tier = pdef.risk_slug
                except Exception:
                    risk_tier = None

                recovered.append(
                    {
                        "position_id": position.id,
                        "portfolio_id": position.portfolio_id,
                        "symbol": sym,
                        "strategy": owning.strategy_slug,
                        "risk_tier": risk_tier,
                        "timeframe": owning.timeframe,
                        "direction": open_pos.direction.value,
                        "entry": float(open_pos.entry_price),
                        "SL": float(open_pos.stop_loss),
                        "TP": float(open_pos.take_profit)
                        if open_pos.take_profit is not None
                        else None,
                        "first_trigger_timestamp": candle.timestamp.isoformat(),
                        "source_timeframe": source,
                        "trigger_reason": reason.value,
                        "exit_base_price": float(trigger_price),
                        "actual_fill_price": float(fill.fill_price),
                        "fees": float(fill.fees),
                        "slippage": float(fill.slippage),
                        "spread": float(fill.spread_cost),
                        "realized_pnl": float(trade.realized_pnl),
                        "trade_id": trade.id,
                    }
                )
            except Exception as exc:
                nested.rollback()
                logger.exception("Recovery failed for %s", position.id)
                skipped.append(
                    {"position_id": position.id, "reason": "error", "error": str(exc)}
                )

    store.session.flush()
    return {
        "recovered": recovered,
        "skipped": skipped,
        "recovered_count": len(recovered),
        "skipped_count": len(skipped),
    }
