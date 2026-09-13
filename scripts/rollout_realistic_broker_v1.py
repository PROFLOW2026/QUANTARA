#!/usr/bin/env python3
"""
Archive legacy Research run and start realistic_broker_v1 run.

Preserves all historical trades/orders/denials under legacy_spot_limited.
Does NOT replay historical short denials.
Upgrades Live Sim in place when pristine ($10K, no fills).
"""

from __future__ import annotations

import argparse
import json
import sys
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "engine"))

from sqlalchemy import text

from quantara_engine.broker.accounts import LIVE_SIM_10K_ACCOUNT_SLUG, RESEARCH_PAPER_ACCOUNT_SLUG
from quantara_engine.competition.constants import ACTIVE_COMPETITION_PORTFOLIOS
from quantara_engine.competition.paper_run import (
    create_paper_run,
    end_paper_run,
    get_current_paper_run_id,
)
from quantara_engine.persistence.store import TradingStore

EXPECTED_RUN_ID = "681988b3-36e7-48dc-9354-7582e96d37ce"


def _fill_count(store: TradingStore, account_slug: str) -> int:
    row = store.session.execute(
        text(
            """
            SELECT COUNT(*)::int AS c
            FROM broker_fills f
            JOIN broker_orders o ON o.id = f.broker_order_id
            JOIN broker_accounts ba ON ba.id = o.broker_account_id
            WHERE ba.slug = :slug
            """
        ),
        {"slug": account_slug},
    ).mappings().first()
    return int(row["c"]) if row else 0


def _closed_trades(store: TradingStore, run_id: str | None) -> int:
    if not run_id:
        return 0
    try:
        row = store.session.execute(
            text(
                """
                SELECT COUNT(*)::int AS c FROM trades
                WHERE paper_run_id = :rid AND closed_at IS NOT NULL
                """
            ),
            {"rid": run_id},
        ).mappings().first()
        return int(row["c"]) if row else 0
    except Exception:
        store.session.rollback()
        return 0


def _position_count(store: TradingStore, account_slug: str) -> int:
    row = store.session.execute(
        text(
            """
            SELECT COUNT(*)::int AS c
            FROM broker_positions bp
            JOIN broker_accounts ba ON ba.id = bp.broker_account_id
            WHERE ba.slug = :slug AND bp.net_quantity != 0
            """
        ),
        {"slug": account_slug},
    ).mappings().first()
    return int(row["c"]) if row else 0


def _account_snapshot(store: TradingStore, slug: str) -> dict:
    row = store.session.execute(
        text(
            """
            SELECT cash, balance, execution_model::text AS execution_model
            FROM broker_accounts WHERE slug = :slug
            """
        ),
        {"slug": slug},
    ).mappings().first()
    return dict(row) if row else {}


def dry_run_report(store: TradingStore) -> dict:
    current_run = get_current_paper_run_id(store)
    live = _account_snapshot(store, LIVE_SIM_10K_ACCOUNT_SLUG)
    research = _account_snapshot(store, RESEARCH_PAPER_ACCOUNT_SLUG)
    live_fills = _fill_count(store, LIVE_SIM_10K_ACCOUNT_SLUG)
    closed = _closed_trades(store, current_run)
    live_positions = _position_count(store, LIVE_SIM_10K_ACCOUNT_SLUG)

    return {
        "current_run": current_run,
        "expected_run_match": current_run == EXPECTED_RUN_ID,
        "will_archive_current_run": current_run is not None,
        "old_execution_model": research.get("execution_model", "legacy_spot_limited"),
        "historical_closed_trades_preserved": closed,
        "historical_short_denials_replayed": 0,
        "new_run_execution_model": "realistic_broker_v1",
        "research_portfolios": len(ACTIVE_COMPETITION_PORTFOLIOS),
        "live_sim_cash": str(live.get("cash", "unknown")),
        "live_sim_fills": live_fills,
        "live_sim_open_positions": live_positions,
        "live_sim_in_place_upgrade_eligible": live_fills == 0 and live_positions == 0,
        "dry_run": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Roll Research to realistic_broker_v1")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    store = TradingStore()
    report = dry_run_report(store)
    print(json.dumps(report, indent=2))

    if args.dry_run:
        print("\nDRY RUN — no changes applied.")
        return 0

    current_run = report["current_run"]
    if not current_run:
        print("No active paper run — nothing to archive.")
        return 1

    end_paper_run(store, current_run)
    store.session.execute(
        text(
            """
            UPDATE paper_runs
            SET ended_reason = 'execution_model_upgrade',
                execution_model = 'legacy_spot_limited'
            WHERE id = :id
            """
        ),
        {"id": current_run},
    )

    new_run = create_paper_run(
        store,
        starting_broker_cash=Decimal("320000"),
        metadata={"execution_model": "realistic_broker_v1", "reason": "execution_model_upgrade"},
        execution_model="realistic_broker_v1",
    )

    store.session.execute(
        text(
            """
            UPDATE broker_accounts
            SET execution_model = 'realistic_broker_v1'
            WHERE slug = :slug
            """
        ),
        {"slug": RESEARCH_PAPER_ACCOUNT_SLUG},
    )

    if report["live_sim_in_place_upgrade_eligible"]:
        store.session.execute(
            text(
                """
                UPDATE broker_accounts
                SET execution_model = 'realistic_broker_v1'
                WHERE slug = :slug
                """
            ),
            {"slug": LIVE_SIM_10K_ACCOUNT_SLUG},
        )

    store.session.commit()
    print(json.dumps({"archived_run": current_run, "new_run": new_run}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
