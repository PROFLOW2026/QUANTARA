#!/usr/bin/env python3
"""
Archive legacy Research run and start realistic_broker_v1 run.

Preserves all historical trades/orders/denials under legacy_spot_limited.
Does NOT replay historical short denials.
Upgrades Live Sim in place when financially pristine ($10K, no fills/positions).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "engine"))

from quantara_engine.competition.realistic_broker_rollout import (
    RolloutError,
    capture_environment_snapshot,
    execute_rollout,
    rollout_report_json,
    snapshots_equal,
)
from quantara_engine.db.session import SessionLocal
from quantara_engine.persistence.store import TradingStore


def main() -> int:
    parser = argparse.ArgumentParser(description="Roll Research to realistic_broker_v1")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    session = SessionLocal()
    try:
        store = TradingStore(session)
        before = capture_environment_snapshot(store)

        if args.dry_run:
            report = execute_rollout(store, dry_run=True)
            after = capture_environment_snapshot(store)
            report["side_effects_detected"] = not snapshots_equal(before, after)
            print(rollout_report_json(report))
            session.rollback()
            if report.get("side_effects_detected"):
                print("\nDRY RUN FAILED — side effects detected.", file=sys.stderr)
                return 1
            print("\nDRY RUN — no changes applied.")
            return 0

        result = execute_rollout(store, dry_run=False)
        session.commit()
        print(rollout_report_json(result))
        return 0
    except RolloutError as exc:
        session.rollback()
        print(f"Rollout blocked ({exc.code}): {exc}", file=sys.stderr)
        return 1
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())
