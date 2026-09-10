#!/usr/bin/env python3
"""
Deterministic financial repair from trade ledger + open-position unrealized.

Canonical:
  balance    = initial_capital + SUM(trades.realized_pnl)
  unrealized = SUM(OPEN position unrealized_pnl)
  equity     = balance + unrealized

Default: DRY-RUN. Pass --apply to mutate (Paper only unless owner approves).
"""

from __future__ import annotations

import argparse
import json
import sys
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "engine"))

from quantara_engine.db.session import session_scope  # noqa: E402
from quantara_engine.persistence.store import TradingStore  # noqa: E402
from quantara_engine.portfolio.balance_reconciliation import (  # noqa: E402
    apply_canonical_financial_state,
    reconcile_portfolio_financial_state,
)

Q = Decimal("0.01")


def repair(*, apply: bool = False) -> dict:
    report: dict = {
        "dry_run": not apply,
        "production_db_modified": False,
        "portfolios_checked": 0,
        "portfolios_repaired": 0,
        "aggregate_balance_correction": 0.0,
        "aggregate_unrealized_correction": 0.0,
        "aggregate_equity_correction": 0.0,
        "details": [],
    }

    with session_scope() as session:
        store = TradingStore(session)
        _, _, entries = store.list_all_competition_entries()
        pids = [e["portfolio"].id for e in entries]
        realized_map = store.sum_realized_pnl_for_portfolio_ids(pids)
        open_fin = store.sum_open_position_financials_for_portfolio_ids(pids)

        agg_bal = Decimal("0")
        agg_unreal = Decimal("0")
        agg_eq = Decimal("0")

        for entry in entries:
            portfolio = entry["portfolio"]
            realized = realized_map.get(portfolio.id, Decimal("0"))
            unreal, exposure = open_fin.get(portfolio.id, (Decimal("0"), Decimal("0")))
            result = reconcile_portfolio_financial_state(portfolio, realized, unreal)
            report["portfolios_checked"] += 1

            bal_diff = result.balance_difference
            unreal_diff = (portfolio.unrealized_pnl - unreal).quantize(Q)
            eq_diff = result.equity_difference

            if bal_diff == 0 and unreal_diff == 0 and eq_diff == 0:
                continue

            report["portfolios_repaired"] += 1
            agg_bal += bal_diff
            agg_unreal += unreal_diff
            agg_eq += eq_diff
            report["details"].append(
                {
                    "portfolio_id": portfolio.id,
                    "name": portfolio.name,
                    "stored_balance": float(result.stored_balance),
                    "canonical_balance": float(result.canonical_balance),
                    "balance_correction": float(-bal_diff),
                    "stored_unrealized": float(portfolio.unrealized_pnl),
                    "canonical_unrealized": float(unreal),
                    "unrealized_correction": float(-unreal_diff),
                    "stored_equity": float(result.stored_equity),
                    "canonical_equity": float(result.canonical_equity),
                    "equity_correction": float(-eq_diff),
                    "realized_from_trades": float(result.realized_from_trades),
                }
            )

            if apply:
                apply_canonical_financial_state(
                    portfolio,
                    realized_pnl_sum=realized,
                    unrealized_pnl_sum=unreal,
                    exposure_notional=exposure,
                )
                store.update_portfolios_financial_canonical_batch([portfolio])

        if apply and report["portfolios_repaired"]:
            session.commit()
            report["production_db_modified"] = True

    report["aggregate_balance_correction"] = float(agg_bal.quantize(Q))
    report["aggregate_unrealized_correction"] = float(agg_unreal.quantize(Q))
    report["aggregate_equity_correction"] = float(agg_eq.quantize(Q))
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Repair portfolio financials from canonical ledger")
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
