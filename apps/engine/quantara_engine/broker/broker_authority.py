"""Durable simulated broker authoritative order state — survives process restart."""

from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal

from sqlalchemy import text


def persist_authority_order(
    session,
    *,
    broker_account_id: str,
    broker_order_id: str,
    client_order_id: str,
    status: str,
    filled_quantity: Decimal = Decimal("0"),
    remaining_quantity: Decimal | None = None,
    submission_unknown: bool = False,
    accepted_at: datetime | None = None,
    first_fill_at: datetime | None = None,
    last_fill_at: datetime | None = None,
    replace_of_order_id: str | None = None,
    payload: dict | None = None,
) -> None:
    try:
        session.execute(
            text(
                """
                INSERT INTO sim_broker_authority_orders (
                  broker_account_id, broker_order_id, client_order_id,
                  authoritative_status, filled_quantity, remaining_quantity,
                  submission_unknown, accepted_at, first_fill_at, last_fill_at,
                  replace_of_order_id, payload
                ) VALUES (
                  :aid, :oid, :cid, :st, :fq, :rq, :su, :acc, :ff, :lf, :rep,
                  CAST(:payload AS jsonb)
                )
                ON CONFLICT (broker_order_id) DO UPDATE SET
                  authoritative_status = EXCLUDED.authoritative_status,
                  filled_quantity = EXCLUDED.filled_quantity,
                  remaining_quantity = EXCLUDED.remaining_quantity,
                  submission_unknown = EXCLUDED.submission_unknown,
                  accepted_at = COALESCE(EXCLUDED.accepted_at, sim_broker_authority_orders.accepted_at),
                  first_fill_at = COALESCE(EXCLUDED.first_fill_at, sim_broker_authority_orders.first_fill_at),
                  last_fill_at = COALESCE(EXCLUDED.last_fill_at, sim_broker_authority_orders.last_fill_at),
                  payload = COALESCE(EXCLUDED.payload, sim_broker_authority_orders.payload),
                  updated_at = NOW()
                """
            ),
            {
                "aid": broker_account_id,
                "oid": broker_order_id,
                "cid": client_order_id,
                "st": status,
                "fq": filled_quantity,
                "rq": remaining_quantity,
                "su": submission_unknown,
                "acc": accepted_at,
                "ff": first_fill_at,
                "lf": last_fill_at,
                "rep": replace_of_order_id,
                "payload": json.dumps(payload or {}),
            },
        )
    except Exception:
        session.rollback()


def load_authority_order_by_client_id(session, broker_account_id: str, client_order_id: str) -> dict | None:
    try:
        row = session.execute(
            text(
                """
                SELECT broker_order_id::text, authoritative_status, filled_quantity,
                       remaining_quantity, submission_unknown, accepted_at, payload
                FROM sim_broker_authority_orders
                WHERE broker_account_id = :aid AND client_order_id = :cid
                LIMIT 1
                """
            ),
            {"aid": broker_account_id, "cid": client_order_id},
        ).mappings().first()
        return dict(row) if row else None
    except Exception:
        session.rollback()
        return None


def load_open_authority_orders(session, broker_account_id: str) -> list[dict]:
    try:
        rows = session.execute(
            text(
                """
                SELECT broker_order_id::text, client_order_id, authoritative_status,
                       filled_quantity, remaining_quantity, submission_unknown, payload
                FROM sim_broker_authority_orders
                WHERE broker_account_id = :aid
                  AND authoritative_status IN (
                    'validating', 'accepted', 'submitted', 'partially_filled', 'submission_unknown'
                  )
                """
            ),
            {"aid": broker_account_id},
        ).mappings().all()
        return [dict(r) for r in rows]
    except Exception:
        session.rollback()
        return []
