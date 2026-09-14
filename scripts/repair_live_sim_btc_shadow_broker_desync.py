#!/usr/bin/env python3
"""Targeted BTC Live Sim shadow/broker desync recovery against quantara_prod."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "engine"))

from quantara_engine.db.session import session_scope  # noqa: E402
from quantara_engine.live_sim.btc_desync_recovery import (  # noqa: E402
    recover_live_sim_btc_shadow_broker_desync,
)
from quantara_engine.persistence.store import TradingStore  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--position-id",
        action="append",
        dest="position_ids",
        default=None,
        help="Repeatable. Defaults to original BTC LONG + residual LONG desync.",
    )
    args = parser.parse_args()
    position_ids = args.position_ids or [
        "5f30c5ce-d946-4173-8587-af067a7af563",
        "17f6a9c1-42fc-4ae7-a08c-3b6fe0352e1f",
    ]

    results = []
    with session_scope() as session:
        store = TradingStore(session)
        for pid in position_ids:
            result = recover_live_sim_btc_shadow_broker_desync(
                store,
                position_id=pid,
                dry_run=args.dry_run,
            )
            results.append(result)
            print(json.dumps(result, indent=2, default=str))
        if args.dry_run:
            session.rollback()
            return 0
        ok_statuses = {"recovered", "already_recovered", "no_broker_qty"}
        if all(r.get("status") in ok_statuses for r in results):
            # session_scope commits on clean exit
            return 0
        if all(
            bool(r.get("physical_succeeded")) or r.get("status") in ok_statuses
            for r in results
        ):
            return 0
        # Force abort persistence
        raise RuntimeError(f"BTC recovery incomplete: {results}")


if __name__ == "__main__":
    raise SystemExit(main())
