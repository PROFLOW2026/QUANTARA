#!/usr/bin/env python3
"""
Clean paper broker reset — DRY-RUN by default. Owner approval required for --execute.

See docs/broker/PAPER-RESET-PLAN.md
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "engine"))

STARTING_CASH = 320_000


def main() -> None:
    parser = argparse.ArgumentParser(description="Paper broker competition reset")
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually perform reset (requires owner approval)",
    )
    args = parser.parse_args()

    steps = [
        "Apply migration packages/db/migrations/0006_broker_account.sql",
        f"Set broker_accounts cash/balance = ${STARTING_CASH:,}",
        "Clear broker_positions, broker_orders, broker_fills, broker_order_rejections",
        "Mark prior run LEGACY_SIMULATION in broker_accounts metadata",
        "Close open competition strategy positions (competition scope)",
        "Cancel pending order_intents",
        "Preserve historical trades read-only",
        "Re-verify financial reconciliation = $0.00",
    ]

    print("QUANTARA Paper Broker Reset")
    print("=" * 40)
    for i, step in enumerate(steps, 1):
        print(f"  {i}. {step}")

    if not args.execute:
        print("\nDRY-RUN only. Pass --execute after owner approval.")
        return

    print("\n--execute requested but reset SQL is not automated yet.")
    print("Owner must run manual steps per docs/broker/PAPER-RESET-PLAN.md")
    sys.exit(1)


if __name__ == "__main__":
    main()
