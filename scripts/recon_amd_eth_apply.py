"""Apply recon_amd_eth_2026_11_07_v1 with locks, prechecks, and postchecks.

Does NOT create broker orders/fills. Does NOT flatten AMD broker short -24.
Does NOT delete orders/fills/lots/ledger or rewrite historical realized P&L.
"""

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
LOCK_KEY = 202611071


def D(v) -> Decimal:
    return Decimal(str(v or 0))


def main() -> int:
    apply = "--apply" in sys.argv
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("DATABASE_URL missing", file=sys.stderr)
        return 2

    eng = sa.create_engine(url, pool_pre_ping=True)
    report: dict = {
        "idempotency_key": IDEMPOTENCY_KEY,
        "mode": "apply" if apply else "precheck",
        "applied_at": None,
        "mutations": {},
        "stopped": False,
    }

    with eng.connect() as c:
        # Serialize against concurrent workers mutating the same rows.
        got_lock = c.execute(
            sa.text("SELECT pg_try_advisory_xact_lock(:k)"),
            {"k": LOCK_KEY},
        ).scalar()
        if not got_lock:
            report["stopped"] = True
            report["stop_reason"] = "advisory_lock_busy_worker_or_other_recon"
            print(json.dumps(report, indent=2))
            return 3

        existing = c.execute(
            sa.text("SELECT key, value, updated_at FROM settings WHERE key = :k"),
            {"k": IDEMPOTENCY_KEY},
        ).mappings().first()
        if existing and apply:
            report["stopped"] = True
            report["stop_reason"] = "idempotency_key_already_applied"
            report["existing"] = {
                "value": existing["value"],
                "updated_at": existing["updated_at"].isoformat()
                if hasattr(existing["updated_at"], "isoformat")
                else str(existing["updated_at"]),
            }
            print(json.dumps(report, indent=2))
            return 4

        amd_shadow = c.execute(
            sa.text(
                """
                SELECT id::text, broker_account_id::text, direction::text, quantity, status::text,
                       stop_loss, entry_price
                FROM live_sim_positions WHERE id = CAST(:pid AS uuid)
                FOR UPDATE
                """
            ),
            {"pid": AMD_PID},
        ).mappings().first()
        eth_pos = c.execute(
            sa.text(
                """
                SELECT id::text, portfolio_id::text, direction::text, quantity, status::text
                FROM positions WHERE id = CAST(:pid AS uuid)
                FOR UPDATE
                """
            ),
            {"pid": ETH_PID},
        ).mappings().first()

        amd_broker = c.execute(
            sa.text(
                """
                SELECT ba.id::text AS broker_account_id, bp.net_quantity, bp.unrealized_pnl
                FROM broker_positions bp
                JOIN broker_accounts ba ON ba.id = bp.broker_account_id
                JOIN instruments i ON i.id = bp.instrument_id
                WHERE ba.slug = 'live-sim-ibkr-like' AND i.symbol = 'AMD'
                FOR UPDATE OF bp
                """
            )
        ).mappings().first()
        eth_broker = c.execute(
            sa.text(
                """
                SELECT bp.net_quantity
                FROM broker_positions bp
                JOIN broker_accounts ba ON ba.id = bp.broker_account_id
                JOIN instruments i ON i.id = bp.instrument_id
                WHERE ba.slug = 'quantara_paper_competition' AND i.symbol = 'ETHUSD'
                FOR UPDATE OF bp
                """
            )
        ).scalar()

        same_dir = D(
            c.execute(
                sa.text(
                    """
                    SELECT COALESCE(SUM(l.remaining_qty), 0)
                    FROM broker_attribution_lots l
                    JOIN live_sim_positions lsp ON lsp.id = l.strategy_position_id
                    WHERE l.strategy_position_id = CAST(:pid AS uuid)
                      AND l.remaining_qty > 0
                      AND lower(l.direction::text) = lower(lsp.direction::text)
                    """
                ),
                {"pid": AMD_PID},
            ).scalar()
        )
        opp_lots = c.execute(
            sa.text(
                """
                SELECT id::text, direction::text, remaining_qty
                FROM broker_attribution_lots
                WHERE strategy_position_id = CAST(:pid AS uuid)
                  AND remaining_qty > 0
                  AND lower(direction::text) = 'short'
                FOR UPDATE
                """
            ),
            {"pid": AMD_PID},
        ).mappings().all()
        eth_rem = D(
            c.execute(
                sa.text(
                    """
                    SELECT COALESCE(SUM(remaining_qty), 0)
                    FROM broker_attribution_lots
                    WHERE strategy_position_id = CAST(:pid AS uuid)
                    """
                ),
                {"pid": ETH_PID},
            ).scalar()
        )

        fills_before = c.execute(sa.text("SELECT count(*)::bigint FROM broker_fills")).scalar()
        orders_before = c.execute(sa.text("SELECT count(*)::bigint FROM broker_orders")).scalar()
        amd_realized_before = c.execute(
            sa.text(
                """
                SELECT realized_pnl FROM broker_accounts WHERE slug = 'live-sim-ibkr-like'
                """
            )
        ).scalar()

        amd_ok = bool(
            amd_shadow
            and amd_broker
            and amd_shadow["broker_account_id"] == amd_broker["broker_account_id"]
            and amd_shadow["status"] == "open"
            and amd_shadow["direction"] == "long"
            and same_dir == 0
            and D(amd_broker["net_quantity"]) == D("-24")
        )
        eth_ok = bool(
            eth_pos
            and eth_pos["status"] == "open"
            and D(eth_pos["quantity"]) != eth_rem
            and eth_rem == abs(D(eth_broker))
            and eth_rem == D("0.00285")
        )

        report["preconditions"] = {
            "amd": amd_ok,
            "eth": eth_ok,
            "amd_same_dir_remaining": str(same_dir),
            "amd_shadow_status": amd_shadow["status"] if amd_shadow else None,
            "amd_broker_net": str(amd_broker["net_quantity"]) if amd_broker else None,
            "eth_positions_qty": str(eth_pos["quantity"]) if eth_pos else None,
            "eth_attr_remaining": str(eth_rem),
            "eth_broker_net": str(eth_broker),
            "opp_orphan_lots": [dict(r) for r in opp_lots],
            "fills_before": fills_before,
            "orders_before": orders_before,
            "amd_realized_pnl_before": str(amd_realized_before),
        }

        if not (amd_ok and eth_ok):
            report["stopped"] = True
            report["stop_reason"] = "preconditions_changed_abort"
            print(json.dumps(report, indent=2, default=str))
            return 1

        if not apply:
            report["would_mutate"] = {
                "amd_shadow_close": True,
                "amd_detach_short_orphans": len(opp_lots),
                "eth_qty_sync_to": str(eth_rem),
            }
            print(json.dumps(report, indent=2, default=str))
            return 0

        # --- APPLY ---
        amd_upd = c.execute(
            sa.text(
                """
                UPDATE live_sim_positions
                SET status = 'closed',
                    closed_at = COALESCE(closed_at, NOW()),
                    quantity = 0,
                    updated_at = NOW()
                WHERE id = CAST(:pid AS uuid)
                  AND status = 'open'
                  AND NOT EXISTS (
                    SELECT 1 FROM broker_attribution_lots l
                    WHERE l.strategy_position_id = live_sim_positions.id
                      AND lower(l.direction::text) = lower(live_sim_positions.direction::text)
                      AND l.remaining_qty > 0
                  )
                """
            ),
            {"pid": AMD_PID},
        )
        detach = c.execute(
            sa.text(
                """
                UPDATE broker_attribution_lots
                SET strategy_position_id = NULL,
                    opportunity_key = NULL
                WHERE strategy_position_id = CAST(:pid AS uuid)
                  AND lower(direction::text) = 'short'
                  AND remaining_qty > 0
                """
            ),
            {"pid": AMD_PID},
        )
        eth_upd = c.execute(
            sa.text(
                """
                UPDATE positions
                SET quantity = sub.rem,
                    updated_at = NOW()
                FROM (
                  SELECT COALESCE(SUM(remaining_qty), 0) AS rem
                  FROM broker_attribution_lots
                  WHERE strategy_position_id = CAST(:pid AS uuid)
                ) sub
                WHERE id = CAST(:pid AS uuid)
                  AND status = 'open'
                  AND quantity <> sub.rem
                """
            ),
            {"pid": ETH_PID},
        )

        now = datetime.now(timezone.utc).isoformat()
        c.execute(
            sa.text(
                """
                INSERT INTO settings (key, value, updated_at)
                VALUES (
                  :key,
                  CAST(:value AS jsonb),
                  NOW()
                )
                ON CONFLICT (key) DO NOTHING
                """
            ),
            {
                "key": IDEMPOTENCY_KEY,
                "value": json.dumps(
                    {
                        "amd_shadow": "closed",
                        "eth_qty": "0.00285",
                        "applied_at": now,
                        "fills_before": fills_before,
                        "orders_before": orders_before,
                        "amd_broker_net_unchanged": "-24",
                    }
                ),
            },
        )

        # Postchecks inside same transaction
        amd_after = c.execute(
            sa.text(
                "SELECT status::text, quantity FROM live_sim_positions WHERE id = CAST(:pid AS uuid)"
            ),
            {"pid": AMD_PID},
        ).mappings().first()
        eth_after = c.execute(
            sa.text("SELECT quantity, status::text FROM positions WHERE id = CAST(:pid AS uuid)"),
            {"pid": ETH_PID},
        ).mappings().first()
        amd_broker_after = c.execute(
            sa.text(
                """
                SELECT bp.net_quantity
                FROM broker_positions bp
                JOIN broker_accounts ba ON ba.id = bp.broker_account_id
                JOIN instruments i ON i.id = bp.instrument_id
                WHERE ba.slug = 'live-sim-ibkr-like' AND i.symbol = 'AMD'
                """
            )
        ).scalar()
        fills_after = c.execute(sa.text("SELECT count(*)::bigint FROM broker_fills")).scalar()
        orders_after = c.execute(sa.text("SELECT count(*)::bigint FROM broker_orders")).scalar()
        amd_realized_after = c.execute(
            sa.text("SELECT realized_pnl FROM broker_accounts WHERE slug = 'live-sim-ibkr-like'")
        ).scalar()
        attached_orphans = c.execute(
            sa.text(
                """
                SELECT count(*)::int FROM broker_attribution_lots
                WHERE strategy_position_id = CAST(:pid AS uuid)
                  AND remaining_qty > 0
                """
            ),
            {"pid": AMD_PID},
        ).scalar()

        post_ok = (
            amd_after
            and amd_after["status"] == "closed"
            and D(amd_after["quantity"]) == 0
            and D(amd_broker_after) == D("-24")
            and eth_after
            and eth_after["status"] == "open"
            and D(eth_after["quantity"]) == D("0.00285")
            and fills_after == fills_before
            and orders_after == orders_before
            and D(amd_realized_after) == D(amd_realized_before)
            and attached_orphans == 0
        )
        report["mutations"] = {
            "amd_shadow_rows": amd_upd.rowcount,
            "amd_detach_lots": detach.rowcount,
            "eth_qty_rows": eth_upd.rowcount,
        }
        report["postchecks"] = {
            "ok": post_ok,
            "amd_shadow": dict(amd_after) if amd_after else None,
            "amd_broker_net": str(amd_broker_after),
            "eth": dict(eth_after) if eth_after else None,
            "fills_after": fills_after,
            "orders_after": orders_after,
            "amd_realized_pnl_after": str(amd_realized_after),
            "amd_lots_still_attached": attached_orphans,
        }

        if not post_ok:
            c.rollback()
            report["stopped"] = True
            report["stop_reason"] = "postcheck_failed_rolled_back"
            print(json.dumps(report, indent=2, default=str))
            return 5

        c.commit()
        report["applied_at"] = now
        print(json.dumps(report, indent=2, default=str))
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
