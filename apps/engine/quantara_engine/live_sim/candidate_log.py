"""Persist live-sim allocation decisions for audit and Home UI."""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from quantara_engine.live_sim.constants import REJECTION_HE
from quantara_engine.persistence.store import TradingStore


def rejection_label_he(reason: str | None) -> str:
    if not reason:
        return "לא ידוע"
    return REJECTION_HE.get(reason, reason)


def log_allocation(
    store: TradingStore,
    *,
    account_id: str,
    canonical_key: str,
    opportunity_key: str | None,
    strategy_slug: str,
    strategy_version: str,
    robot_label: str | None,
    symbol: str,
    timeframe: str,
    direction: str,
    signal_candle_timestamp: datetime,
    proposed_entry: Decimal | None,
    stop_loss: Decimal | None,
    take_profit: Decimal | None,
    calculated_risk_usd: Decimal | None,
    calculated_quantity: Decimal | None,
    accepted: bool,
    rejection_reason: str | None = None,
    rejection_detail: str | None = None,
    resulting_open_sl_risk_usd: Decimal | None = None,
    symbol_sl_risk_pct: float | None = None,
    group_sl_risk_pct: float | None = None,
    group_name: str | None = None,
    broker_order_id: str | None = None,
    live_sim_position_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> str | None:
    log_id = str(uuid.uuid4())
    try:
        store.session.execute(
            text(
                """
                INSERT INTO live_sim_allocation_log (
                  id, broker_account_id, canonical_opportunity_key, opportunity_key,
                  strategy_slug, strategy_version, robot_label, symbol, timeframe,
                  direction, signal_candle_timestamp, proposed_entry, stop_loss, take_profit,
                  calculated_risk_usd, calculated_quantity, accepted,
                  rejection_reason, rejection_detail, resulting_open_sl_risk_usd,
                  symbol_sl_risk_pct, group_sl_risk_pct, group_name,
                  broker_order_id, live_sim_position_id, metadata
                ) VALUES (
                  :id, :aid, :canonical, :opp, :slug, :ver, :robot, :sym, :tf,
                  CAST(:dir AS direction), :sig_ts, :entry, :sl, :tp,
                  :risk, :qty, :accepted, :reason, :detail, :open_risk,
                  :sym_pct, :grp_pct, :grp, :order_id, :pos_id, CAST(:meta AS jsonb)
                )
                """
            ),
            {
                "id": log_id,
                "aid": account_id,
                "canonical": canonical_key,
                "opp": opportunity_key,
                "slug": strategy_slug,
                "ver": strategy_version,
                "robot": robot_label,
                "sym": symbol,
                "tf": timeframe,
                "dir": direction,
                "sig_ts": signal_candle_timestamp,
                "entry": proposed_entry,
                "sl": stop_loss,
                "tp": take_profit,
                "risk": calculated_risk_usd,
                "qty": calculated_quantity,
                "accepted": accepted,
                "reason": rejection_reason,
                "detail": rejection_detail,
                "open_risk": resulting_open_sl_risk_usd,
                "sym_pct": symbol_sl_risk_pct,
                "grp_pct": group_sl_risk_pct,
                "grp": group_name,
                "order_id": broker_order_id,
                "pos_id": live_sim_position_id,
                "meta": json.dumps(metadata or {}),
            },
        )
        return log_id
    except IntegrityError:
        store.session.rollback()
        return None


def update_allocation_execution(
    store: TradingStore,
    log_id: str,
    *,
    broker_order_id: str,
    live_sim_position_id: str,
    metadata_patch: dict[str, Any] | None = None,
) -> None:
    if metadata_patch:
        store.session.execute(
            text(
                """
                UPDATE live_sim_allocation_log
                SET broker_order_id = :oid,
                    live_sim_position_id = :pid,
                    metadata = metadata || CAST(:meta AS jsonb)
                WHERE id = :id
                """
            ),
            {
                "id": log_id,
                "oid": broker_order_id,
                "pid": live_sim_position_id,
                "meta": json.dumps(metadata_patch),
            },
        )
    else:
        store.session.execute(
            text(
                """
                UPDATE live_sim_allocation_log
                SET broker_order_id = :oid, live_sim_position_id = :pid
                WHERE id = :id
                """
            ),
            {"id": log_id, "oid": broker_order_id, "pid": live_sim_position_id},
        )


def find_allocation_by_canonical(
    store: TradingStore,
    account_id: str,
    canonical_key: str,
) -> dict | None:
    row = store.session.execute(
        text(
            """
            SELECT id::text, accepted, broker_order_id::text, live_sim_position_id::text,
                   metadata, signal_candle_timestamp, symbol, timeframe, direction::text,
                   proposed_entry, stop_loss, take_profit, calculated_risk_usd,
                   calculated_quantity, strategy_slug, strategy_version, robot_label,
                   opportunity_key, canonical_opportunity_key, created_at
            FROM live_sim_allocation_log
            WHERE broker_account_id = :aid AND canonical_opportunity_key = :key
            LIMIT 1
            """
        ),
        {"aid": account_id, "key": canonical_key},
    ).mappings().first()
    return dict(row) if row else None


def allocation_lifecycle_state(row: dict) -> str:
    if row.get("live_sim_position_id") or row.get("broker_order_id"):
        return "filled"
    meta = row.get("metadata") or {}
    if meta.get("expired"):
        return "expired"
    if not row.get("accepted"):
        return "rejected"
    if meta.get("pending_execution"):
        return "pending_execution"
    return "accepted"


def update_allocation_metadata(
    store: TradingStore,
    log_id: str,
    metadata_patch: dict[str, Any],
) -> None:
    store.session.execute(
        text(
            """
            UPDATE live_sim_allocation_log
            SET metadata = metadata || CAST(:meta AS jsonb)
            WHERE id = :id
            """
        ),
        {"id": log_id, "meta": json.dumps(metadata_patch)},
    )


def mark_allocation_expired(store: TradingStore, log_id: str, *, reason: str) -> None:
    update_allocation_metadata(
        store,
        log_id,
        {"pending_execution": False, "expired": True, "expiry_reason": reason},
    )


def expire_stale_live_sim_allocations(store: TradingStore, now: datetime) -> int:
    from quantara_engine.execution.timing import intent_past_execution_window

    rows = store.session.execute(
        text(
            """
            SELECT id::text, signal_candle_timestamp, timeframe, created_at, metadata
            FROM live_sim_allocation_log
            WHERE accepted = TRUE
              AND broker_order_id IS NULL
              AND live_sim_position_id IS NULL
              AND (metadata->>'pending_execution')::boolean IS TRUE
              AND COALESCE((metadata->>'expired')::boolean, FALSE) = FALSE
            """
        )
    ).mappings().all()
    expired = 0
    for row in rows:
        exec_ts = row["signal_candle_timestamp"]
        tf = str(row["timeframe"])
        from quantara_engine.execution.timing import next_execution_timestamp

        execution_ts = next_execution_timestamp(exec_ts, tf)
        if intent_past_execution_window(
            signal_candle_timestamp=exec_ts,
            execution_candle_timestamp=execution_ts,
            intent_created_at=row["created_at"],
            timeframe=tf,
            now=now,
        ):
            mark_allocation_expired(store, row["id"], reason="execution_window_passed")
            expired += 1
    return expired


def list_recent_allocations(
    store: TradingStore,
    account_id: str,
    *,
    limit: int = 50,
) -> list[dict]:
    rows = store.session.execute(
        text(
            """
            SELECT id::text, strategy_slug, strategy_version, robot_label, symbol, timeframe,
                   direction::text, signal_candle_timestamp, proposed_entry, stop_loss, take_profit,
                   calculated_risk_usd, calculated_quantity, accepted,
                   rejection_reason, rejection_detail, resulting_open_sl_risk_usd,
                   symbol_sl_risk_pct, group_sl_risk_pct, group_name,
                   broker_order_id::text, live_sim_position_id::text, metadata, created_at
            FROM live_sim_allocation_log
            WHERE broker_account_id = :aid
            ORDER BY created_at DESC
            LIMIT :lim
            """
        ),
        {"aid": account_id, "lim": limit},
    ).mappings().all()
    out = []
    for r in rows:
        reason = r.get("rejection_reason")
        meta = r.get("metadata") or {}
        lifecycle = allocation_lifecycle_state(dict(r))
        out.append(
            {
                **dict(r),
                "rejection_reason_he": rejection_label_he(reason),
                "direction": str(r["direction"]),
                "lifecycle_state": lifecycle,
                "metadata": meta,
            }
        )
    return out
