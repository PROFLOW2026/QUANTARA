"""Dry-run verifier for AMD/ETH reconciliation. Read-only — never mutates DB."""

from __future__ import annotations

import json
import os
import sys
from decimal import Decimal
from pathlib import Path

from dotenv import load_dotenv
import sqlalchemy as sa

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")

AMD_PID = "11154b64-ee49-402f-9bbc-31885631fec2"
ETH_PID = "2ce6cba9-d2b4-4052-80a3-882689621a55"
IDEMPOTENCY_KEY = "recon_amd_eth_2026_11_07_v1"


def D(v) -> Decimal:
    return Decimal(str(v or 0))


def main() -> int:
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("DATABASE_URL missing", file=sys.stderr)
        return 2
    eng = sa.create_engine(url, pool_pre_ping=True)
    report: dict = {"idempotency_key": IDEMPOTENCY_KEY, "mode": "dry_run", "mutations": 0}
    with eng.connect() as c:
        amd_shadow = c.execute(
            sa.text(
                """
                SELECT id::text, broker_account_id::text, direction, quantity, status,
                       stop_loss, entry_price
                FROM live_sim_positions WHERE id = :pid
                """
            ),
            {"pid": AMD_PID},
        ).mappings().first()
        amd_broker = c.execute(
            sa.text(
                """
                SELECT ba.id::text AS broker_account_id, bp.net_quantity, bp.average_price,
                       bp.unrealized_pnl
                FROM broker_positions bp
                JOIN broker_accounts ba ON ba.id = bp.broker_account_id
                JOIN instruments i ON i.id = bp.instrument_id
                WHERE ba.slug = 'live-sim-ibkr-like' AND i.symbol = 'AMD'
                """
            )
        ).mappings().first()
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
        opp_dir = D(
            c.execute(
                sa.text(
                    """
                    SELECT COALESCE(SUM(l.remaining_qty), 0)
                    FROM broker_attribution_lots l
                    JOIN live_sim_positions lsp ON lsp.id = l.strategy_position_id
                    WHERE l.strategy_position_id = CAST(:pid AS uuid)
                      AND l.remaining_qty > 0
                      AND lower(l.direction::text) <> lower(lsp.direction::text)
                    """
                ),
                {"pid": AMD_PID},
            ).scalar()
        )
        eth_pos = c.execute(
            sa.text(
                """
                SELECT id::text, portfolio_id::text, direction, quantity, status
                FROM positions WHERE id = :pid
                """
            ),
            {"pid": ETH_PID},
        ).mappings().first()
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
        eth_broker = c.execute(
            sa.text(
                """
                SELECT bp.net_quantity
                FROM broker_positions bp
                JOIN broker_accounts ba ON ba.id = bp.broker_account_id
                JOIN instruments i ON i.id = bp.instrument_id
                WHERE ba.slug = 'quantara_paper_competition' AND i.symbol = 'ETHUSD'
                """
            )
        ).scalar()

        amd_ok_account = (
            amd_shadow
            and amd_broker
            and amd_shadow["broker_account_id"] == amd_broker["broker_account_id"]
        )
        amd_ready = bool(
            amd_shadow
            and amd_ok_account
            and amd_shadow["status"] == "open"
            and same_dir == 0
            and amd_shadow["direction"] == "long"
        )
        eth_ready = bool(
            eth_pos
            and eth_pos["status"] == "open"
            and D(eth_pos["quantity"]) != eth_rem
            and eth_rem == abs(D(eth_broker))
        )

        report["amd"] = {
            "account_match": amd_ok_account,
            "shadow_status": amd_shadow["status"] if amd_shadow else None,
            "shadow_qty": str(amd_shadow["quantity"]) if amd_shadow else None,
            "broker_net": str(amd_broker["net_quantity"]) if amd_broker else None,
            "same_dir_remaining": str(same_dir),
            "opp_dir_remaining_on_spid": str(opp_dir),
            "proposed_action": (
                "CLOSE live_sim shadow only; DETACH short orphan lots from spid; "
                "DO NOT flatten broker short -24 (would create new trades/P&L)"
            ),
            "preconditions_met_for_apply": amd_ready,
            "pnl_impact": "none on historical fills; shadow close is bookkeeping only",
        }
        report["eth"] = {
            "positions_qty": str(eth_pos["quantity"]) if eth_pos else None,
            "attribution_remaining": str(eth_rem),
            "broker_net": str(eth_broker),
            "proposed_action": f"UPDATE positions.quantity -> {eth_rem} (no broker order)",
            "preconditions_met_for_apply": eth_ready,
            "pnl_impact": "none — quantity sync only; realized already in ledger",
        }
        report["apply_blocked"] = True
        report["apply_blocked_reason"] = "dry_run_only_awaiting_explicit_approval"
        report["rollback"] = (
            "Restore row snapshots exported before apply; "
            "do not reverse via market orders"
        )
    print(json.dumps(report, indent=2))
    return 0 if report["amd"]["preconditions_met_for_apply"] and report["eth"]["preconditions_met_for_apply"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
