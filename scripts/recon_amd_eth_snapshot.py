"""Export read-only snapshot for recon_amd_eth_2026_11_07_v1."""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import sqlalchemy as sa
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")

AMD_PID = "11154b64-ee49-402f-9bbc-31885631fec2"
ETH_PID = "2ce6cba9-d2b4-4052-80a3-882689621a55"
IDEMPOTENCY_KEY = "recon_amd_eth_2026_11_07_v1"


def ser(v):
    if isinstance(v, Decimal):
        return str(v)
    if hasattr(v, "isoformat"):
        return v.isoformat()
    if v.__class__.__name__ == "UUID":
        return str(v)
    if isinstance(v, (bytes, memoryview)):
        return bytes(v).hex()
    return v


def rows_to_dicts(rows):
    return [{k: ser(v) for k, v in dict(r).items()} for r in rows]


def main() -> int:
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("DATABASE_URL missing", file=sys.stderr)
        return 2

    label = sys.argv[1] if len(sys.argv) > 1 else "pre"
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = ROOT / "scripts" / "snapshots"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"recon_amd_eth_{label}_{ts}.json"

    eng = sa.create_engine(url, pool_pre_ping=True)
    snap = {
        "exported_at": ts,
        "label": label,
        "idempotency_key": IDEMPOTENCY_KEY,
        "tables": {},
    }
    queries = {
        "amd_shadow": (
            "SELECT * FROM live_sim_positions WHERE id = :pid",
            {"pid": AMD_PID},
        ),
        "amd_lots": (
            "SELECT * FROM broker_attribution_lots WHERE strategy_position_id = :pid",
            {"pid": AMD_PID},
        ),
        "eth_position": (
            "SELECT * FROM positions WHERE id = :pid",
            {"pid": ETH_PID},
        ),
        "eth_lots": (
            "SELECT * FROM broker_attribution_lots WHERE strategy_position_id = :pid",
            {"pid": ETH_PID},
        ),
        "amd_broker": (
            """
            SELECT ba.slug, bp.*
            FROM broker_positions bp
            JOIN broker_accounts ba ON ba.id = bp.broker_account_id
            JOIN instruments i ON i.id = bp.instrument_id
            WHERE ba.slug = :s AND i.symbol = :sym
            """,
            {"s": "live-sim-ibkr-like", "sym": "AMD"},
        ),
        "eth_broker": (
            """
            SELECT ba.slug, bp.*
            FROM broker_positions bp
            JOIN broker_accounts ba ON ba.id = bp.broker_account_id
            JOIN instruments i ON i.id = bp.instrument_id
            WHERE ba.slug = :s AND i.symbol = :sym
            """,
            {"s": "quantara_paper_competition", "sym": "ETHUSD"},
        ),
        "amd_account": (
            """
            SELECT ba.slug, ba.id::text, ba.*
            FROM broker_accounts ba WHERE ba.slug = :s
            """,
            {"s": "live-sim-ibkr-like"},
        ),
        "eth_account": (
            """
            SELECT ba.slug, ba.id::text, ba.*
            FROM broker_accounts ba WHERE ba.slug = :s
            """,
            {"s": "quantara_paper_competition"},
        ),
        "pending_counts": (
            """
            SELECT status::text AS status, count(*)::int AS n
            FROM order_intents
            WHERE status::text IN ('pending_execution','pending','submitted','open','expired','cancelled')
            GROUP BY 1 ORDER BY 1
            """,
            {},
        ),
        "pending_execution_total": (
            "SELECT count(*)::int AS n FROM order_intents WHERE status = 'pending_execution'",
            {},
        ),
        "pending_execution_old_1h": (
            """
            SELECT count(*)::int AS n
            FROM order_intents
            WHERE status = 'pending_execution'
              AND created_at < NOW() - INTERVAL '1 hour'
            """,
            {},
        ),
        "fills_orders_baseline": (
            """
            SELECT
              (SELECT count(*)::bigint FROM broker_fills) AS broker_fills,
              (SELECT count(*)::bigint FROM broker_orders) AS broker_orders
            """,
            {},
        ),
        "idempotency_existing": (
            "SELECT key, value, updated_at FROM settings WHERE key = :k",
            {"k": IDEMPOTENCY_KEY},
        ),
        "recent_rejects_eth": (
            """
            SELECT rejection_reason, count(*)::int AS n
            FROM broker_orders o
            JOIN instruments i ON i.id = o.instrument_id
            WHERE i.symbol = 'ETHUSD'
              AND o.submitted_at > NOW() - INTERVAL '24 hours'
              AND o.rejection_reason IS NOT NULL
            GROUP BY 1 ORDER BY 2 DESC
            """,
            {},
        ),
    }

    with eng.connect() as c:
        for name, (sql, params) in queries.items():
            snap["tables"][name] = rows_to_dicts(c.execute(sa.text(sql), params).mappings().all())
        try:
            hb = c.execute(
                sa.text(
                    """
                    SELECT key, value, updated_at
                    FROM settings
                    WHERE key ILIKE '%heartbeat%'
                       OR key ILIKE '%worker%'
                       OR key ILIKE '%runner%'
                       OR key ILIKE '%live_sim%'
                    ORDER BY key
                    """
                )
            ).mappings().all()
            snap["tables"]["settings_heartbeats"] = rows_to_dicts(hb)
        except Exception as exc:  # noqa: BLE001
            snap["tables"]["settings_heartbeats_error"] = str(exc)

    out.write_text(json.dumps(snap, indent=2), encoding="utf-8")
    print(json.dumps({"snapshot_path": str(out), "summary": {
        "amd_shadow": snap["tables"]["amd_shadow"],
        "amd_broker_net": snap["tables"]["amd_broker"][0]["net_quantity"] if snap["tables"]["amd_broker"] else None,
        "eth_qty": snap["tables"]["eth_position"][0]["quantity"] if snap["tables"]["eth_position"] else None,
        "pending_execution_total": snap["tables"]["pending_execution_total"],
        "fills_orders_baseline": snap["tables"]["fills_orders_baseline"],
        "idempotency_existing": snap["tables"]["idempotency_existing"],
        "amd_account": snap["tables"]["amd_account"],
    }}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
