#!/usr/bin/env python3
"""Close stale ETH Live Sim shadow after broker-flat TP — no fabricated fills."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "engine"))

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from quantara_engine.live_sim.missed_exit_recovery import _close_shadow_only
from quantara_engine.persistence.store import TradingStore

POSITION_ID = "620951a3-8335-4ca0-bd9f-243d83dfeedb"


def db_url() -> str:
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if line.startswith("DATABASE_URL="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise RuntimeError("DATABASE_URL missing")


def main() -> int:
    session = sessionmaker(bind=create_engine(db_url()))()
    store = TradingStore(session)
    row = session.execute(
        text(
            """
            SELECT lsp.id::text, i.symbol, lsp.status, lsp.quantity,
                   COALESCE(bp.net_quantity, 0) AS broker_qty
            FROM live_sim_positions lsp
            JOIN instruments i ON i.id = lsp.instrument_id
            LEFT JOIN broker_positions bp ON bp.broker_account_id = lsp.broker_account_id
              AND bp.instrument_id = lsp.instrument_id
            WHERE lsp.id = CAST(:pid AS uuid)
            """
        ),
        {"pid": POSITION_ID},
    ).mappings().first()
    if not row:
        print(json.dumps({"status": "not_found"}))
        return 1
    if row["status"] != "open" or Decimal(str(row["broker_qty"])) != 0:
        print(json.dumps({"status": "skip", "row": dict(row)}, default=str))
        return 0

    exit_fill = session.execute(
        text(
            """
            SELECT f.filled_at
            FROM broker_fills f
            JOIN broker_orders o ON o.id = f.broker_order_id
            JOIN broker_attribution_ledger bl ON bl.broker_fill_id = f.id
            WHERE bl.strategy_position_id = CAST(:pid AS uuid)
              AND o.order_purpose IN ('tp', 'sl')
            ORDER BY f.filled_at DESC
            LIMIT 1
            """
        ),
        {"pid": POSITION_ID},
    ).mappings().first()
    closed_at = exit_fill["filled_at"] if exit_fill else None
    if closed_at is None:
        closed_at = datetime.now(timezone.utc)

    _close_shadow_only(
        store,
        position_id=POSITION_ID,
        closed_at=closed_at,
        reason="close_shadow_broker_already_flat",
        trigger_ts=closed_at,
    )
    session.commit()
    print(
        json.dumps(
            {
                "status": "closed",
                "position_id": POSITION_ID,
                "closed_at": closed_at.isoformat(),
            },
            default=str,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
