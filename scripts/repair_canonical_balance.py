#!/usr/bin/env python3
"""
One-time deterministic balance repair from trade ledger.

Canonical: balance = initial_capital + SUM(trades.realized_pnl)
             equity  = balance + unrealized_pnl

Default: DRY-RUN (no DB writes). Pass --apply to mutate (NOT for production without owner approval).
"""

from __future__ import annotations

import argparse
import json
import sys
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "engine"))

from quantara_engine.competition.constants import ACTIVE_COMPETITION_EXPERIMENT_ID  # noqa: E402
from quantara_engine.competition.orb_constants import ORB_COMPETITION_EXPERIMENT_ID  # noqa: E402
from quantara_engine.db.session import session_scope  # noqa: E402
from quantara_engine.persistence.store import TradingStore  # noqa: E402
from quantara_engine.portfolio.balance_reconciliation import (  # noqa: E402
    apply_canonical_balance,
    reconcile_portfolio_balance,
)

Q = Decimal("0.01")


def repair(*, apply: bool = False) -> dict:
    report: dict = {
        "dry_run": not apply,
        "production_db_modified": False,
        "portfolios_checked": 0,
        "portfolios_repaired": 0,
        "aggregate_balance_correction": 0.0,
        "aggregate_equity_correction": 0.0,
        "details": [],
    }

    with session_scope() as session:
        store = TradingStore(session)
        _, _, entries = store.list_all_competition_entries()
        pids = [e["portfolio"].id for e in entries]
        realized_map = store.sum_realized_pnl_for_portfolio_ids(pids)

        agg_bal_corr = Decimal("0")
        agg_eq_corr = Decimal("0")

        for entry in entries:
            portfolio = entry["portfolio"]
            realized = realized_map.get(portfolio.id, Decimal("0"))
            result = reconcile_portfolio_balance(portfolio, realized)
            report["portfolios_checked"] += 1

            if result.balance_difference == 0 and result.equity_difference == 0:
                continue

            report["portfolios_repaired"] += 1
            agg_bal_corr += result.balance_difference
            agg_eq_corr += result.equity_difference
            report["details"].append(
                {
                    "portfolio_id": portfolio.id,
                    "name": portfolio.name,
                    "stored_balance": float(result.stored_balance),
                    "canonical_balance": float(result.canonical_balance),
                    "balance_correction": float(-result.balance_difference),
                    "stored_equity": float(result.stored_equity),
                    "canonical_equity": float(result.canonical_equity),
                    "equity_correction": float(-result.equity_difference),
                    "realized_from_trades": float(result.realized_from_trades),
                    "unrealized": float(result.unrealized_pnl),
                }
            )

            if apply:
                apply_canonical_balance(portfolio, realized)
                store.update_portfolio(portfolio)

        if apply and report["portfolios_repaired"]:
            session.commit()
            report["production_db_modified"] = True

    report["aggregate_balance_correction"] = float(agg_bal_corr.quantize(Q))
    report["aggregate_equity_correction"] = float(agg_eq_corr.quantize(Q))
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Repair portfolio balance from trade ledger")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply repairs (default: dry-run only)",
    )
    args = parser.parse_args()
    result = repair(apply=args.apply)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
