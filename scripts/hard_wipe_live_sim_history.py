#!/usr/bin/env python3
"""Hard-delete Owner Live Sim trading history and start a fresh $10k experiment.

Deletes canonical simulation rows for live-sim-* accounts (positions, fills,
orders, attribution, allocation log). Does NOT touch research/competition
accounts. No backup — intentional full wipe for private sim restart.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "engine"))

from sqlalchemy import text

from quantara_engine.broker.accounts import LIVE_SIM_10K_ACCOUNT_SLUG
from quantara_engine.core.config import settings
from quantara_engine.db.guardrails import PRODUCTION_DB_NAME, db_name_from_url
from quantara_engine.db.session import session_scope
from quantara_engine.db.sqlalchemy_url import normalize_sqlalchemy_postgres_url
from quantara_engine.live_sim.integrity_containment import (
    activate_live_sim_entry_containment,
    is_live_sim_entries_blocked,
    release_live_sim_entry_containment,
)
from quantara_engine.live_sim.v32_experiment import V32_EXPERIMENT_ID
from quantara_engine.owner_portfolio.constants import (
    LIVE_SIM_IBKR_TOTAL,
    LIVE_SIM_KRAKEN_TOTAL,
    LIVE_SIM_TARGET_CAPITAL,
)
from quantara_engine.owner_portfolio.service import LIVE_SIM_OWNER_SLUG
from quantara_engine.persistence.store import TradingStore

_LIVE_SIM_SLUGS = (
    LIVE_SIM_10K_ACCOUNT_SLUG,
    "live-sim-ibkr-like",
    "live-sim-kraken-like",
)


def _require_prod() -> None:
    if not settings.database_configured:
        raise SystemExit("DATABASE_URL not configured")
    settings.validate_runtime_database()
    name = db_name_from_url(settings.database_url)
    if name != PRODUCTION_DB_NAME:
        raise SystemExit(f"Refusing: expected {PRODUCTION_DB_NAME}, got {name!r}")


def _aids(session) -> list[str]:
    rows = session.execute(
        text("SELECT id::text FROM broker_accounts WHERE slug = ANY(:slugs)"),
        {"slugs": list(_LIVE_SIM_SLUGS)},
    ).scalars().all()
    if len(rows) < 2:
        raise SystemExit(f"Expected live-sim accounts, found {len(rows)}")
    return list(rows)


def wipe(store: TradingStore, *, archive_id: str, reset_at: datetime) -> dict:
    aids = _aids(store.session)
    actions: dict[str, int | str] = {"archive_id": archive_id, "reset_at": reset_at.isoformat()}

    # Break circular FKs between positions <-> allocation_log <-> orders.
    actions["null_position_allocation_fk"] = store.session.execute(
        text(
            """
            UPDATE live_sim_positions
            SET allocation_log_id = NULL
            WHERE broker_account_id = ANY(CAST(:aids AS uuid[]))
              AND allocation_log_id IS NOT NULL
            """
        ),
        {"aids": aids},
    ).rowcount
    actions["null_allocation_position_fk"] = store.session.execute(
        text(
            """
            UPDATE live_sim_allocation_log
            SET live_sim_position_id = NULL,
                broker_order_id = NULL
            WHERE broker_account_id = ANY(CAST(:aids AS uuid[]))
              AND (live_sim_position_id IS NOT NULL OR broker_order_id IS NOT NULL)
            """
        ),
        {"aids": aids},
    ).rowcount

    actions["broker_fills"] = store.session.execute(
        text(
            """
            DELETE FROM broker_fills bf
            USING broker_orders o
            WHERE bf.broker_order_id = o.id
              AND o.broker_account_id = ANY(CAST(:aids AS uuid[]))
            """
        ),
        {"aids": aids},
    ).rowcount

    actions["broker_order_rejections"] = store.session.execute(
        text(
            """
            DELETE FROM broker_order_rejections r
            USING broker_orders o
            WHERE r.broker_order_id = o.id
              AND o.broker_account_id = ANY(CAST(:aids AS uuid[]))
            """
        ),
        {"aids": aids},
    ).rowcount

    actions["broker_protective_orders"] = store.session.execute(
        text(
            """
            DELETE FROM broker_protective_orders
            WHERE broker_account_id = ANY(CAST(:aids AS uuid[]))
            """
        ),
        {"aids": aids},
    ).rowcount

    actions["sim_broker_authority_orders"] = store.session.execute(
        text(
            """
            DELETE FROM sim_broker_authority_orders
            WHERE broker_account_id = ANY(CAST(:aids AS uuid[]))
            """
        ),
        {"aids": aids},
    ).rowcount

    actions["broker_orders"] = store.session.execute(
        text(
            """
            DELETE FROM broker_orders
            WHERE broker_account_id = ANY(CAST(:aids AS uuid[]))
            """
        ),
        {"aids": aids},
    ).rowcount

    actions["broker_attribution_ledger"] = store.session.execute(
        text(
            """
            DELETE FROM broker_attribution_ledger l
            USING live_sim_positions p
            WHERE l.strategy_position_id = p.id
              AND p.broker_account_id = ANY(CAST(:aids AS uuid[]))
            """
        ),
        {"aids": aids},
    ).rowcount
    # Also clear ledger rows tied only via lots (no live_sim position row).
    actions["broker_attribution_ledger_via_lots"] = store.session.execute(
        text(
            """
            DELETE FROM broker_attribution_ledger l
            USING broker_attribution_lots lot
            WHERE l.strategy_position_id = lot.strategy_position_id
              AND lot.broker_account_id = ANY(CAST(:aids AS uuid[]))
            """
        ),
        {"aids": aids},
    ).rowcount

    actions["broker_attribution_lots"] = store.session.execute(
        text(
            """
            DELETE FROM broker_attribution_lots
            WHERE broker_account_id = ANY(CAST(:aids AS uuid[]))
            """
        ),
        {"aids": aids},
    ).rowcount

    actions["broker_cost_accrual_log"] = store.session.execute(
        text(
            """
            DELETE FROM broker_cost_accrual_log
            WHERE broker_account_id = ANY(CAST(:aids AS uuid[]))
            """
        ),
        {"aids": aids},
    ).rowcount

    actions["live_sim_positions"] = store.session.execute(
        text(
            """
            DELETE FROM live_sim_positions
            WHERE broker_account_id = ANY(CAST(:aids AS uuid[]))
            """
        ),
        {"aids": aids},
    ).rowcount

    actions["live_sim_allocation_log"] = store.session.execute(
        text(
            """
            DELETE FROM live_sim_allocation_log
            WHERE broker_account_id = ANY(CAST(:aids AS uuid[]))
            """
        ),
        {"aids": aids},
    ).rowcount

    actions["broker_positions"] = store.session.execute(
        text(
            """
            DELETE FROM broker_positions
            WHERE broker_account_id = ANY(CAST(:aids AS uuid[]))
            """
        ),
        {"aids": aids},
    ).rowcount

    actions["learning_planned_vs_actual_risk_live_sim"] = store.session.execute(
        text("DELETE FROM learning_planned_vs_actual_risk WHERE source_type = 'live_sim'")
    ).rowcount

    # Runtime pointers that reference deleted positions (exit-resume cursors etc.).
    store.session.execute(
        text(
            "DELETE FROM settings WHERE key IN "
            "('live_sim_exit_resume_after', 'live_sim_slot_symbol_history')"
        )
    )

    actions["live_sim_dynamic_universe_snapshots"] = store.session.execute(
        text("DELETE FROM live_sim_dynamic_universe_snapshots")
    ).rowcount

    # Reset balances: shared $10k = IBKR 7500 + Kraken 2500.
    store.session.execute(
        text(
            """
            UPDATE broker_accounts SET
              starting_cash = :cap, cash = :cap, balance = :cap, equity = :cap,
              realized_pnl = 0, gross_realized_pnl = 0, unrealized_pnl = 0,
              gross_exposure = 0, net_exposure = 0, fees_paid = 0,
              initial_margin_used = 0, maintenance_margin_required = 0,
              free_margin = :cap, available_margin = :cap,
              pending_owner_reset = FALSE, updated_at = NOW()
            WHERE slug = :slug
            """
        ),
        {"slug": LIVE_SIM_10K_ACCOUNT_SLUG, "cap": LIVE_SIM_TARGET_CAPITAL},
    )
    store.session.execute(
        text(
            """
            UPDATE broker_accounts SET
              starting_cash = :cap, cash = :cap, balance = :cap, equity = :cap,
              realized_pnl = 0, gross_realized_pnl = 0, unrealized_pnl = 0,
              gross_exposure = 0, net_exposure = 0, fees_paid = 0,
              initial_margin_used = 0, maintenance_margin_required = 0,
              free_margin = :cap, available_margin = :cap, updated_at = NOW()
            WHERE slug = 'live-sim-ibkr-like'
            """
        ),
        {"cap": LIVE_SIM_IBKR_TOTAL},
    )
    store.session.execute(
        text(
            """
            UPDATE broker_accounts SET
              starting_cash = :cap, cash = :cap, balance = :cap, equity = :cap,
              spot_crypto_cash = :cap,
              realized_pnl = 0, gross_realized_pnl = 0, unrealized_pnl = 0,
              gross_exposure = 0, net_exposure = 0, fees_paid = 0,
              initial_margin_used = 0, maintenance_margin_required = 0,
              free_margin = :cap, available_margin = :cap, updated_at = NOW()
            WHERE slug = 'live-sim-kraken-like'
            """
        ),
        {"cap": LIVE_SIM_KRAKEN_TOTAL},
    )

    # Stamp fresh experiment anchor on all live-sim accounts.
    anchor_iso = reset_at.isoformat()
    for slug in _LIVE_SIM_SLUGS:
        row = store.session.execute(
            text("SELECT id, account_metadata, risk_settings FROM broker_accounts WHERE slug = :s"),
            {"s": slug},
        ).mappings().first()
        if not row:
            continue
        meta = dict(row["account_metadata"] or {})
        meta["v32_observation_anchor"] = anchor_iso
        meta["live_sim_experiment"] = V32_EXPERIMENT_ID
        meta["baseline_reset_at"] = anchor_iso
        meta["hard_wipe_id"] = archive_id
        risk = dict(row["risk_settings"] or {})
        risk["daily_start_equity"] = float(
            LIVE_SIM_TARGET_CAPITAL
            if slug == LIVE_SIM_10K_ACCOUNT_SLUG
            else (LIVE_SIM_IBKR_TOTAL if "ibkr" in slug else LIVE_SIM_KRAKEN_TOTAL)
        )
        risk["high_water_mark"] = risk["daily_start_equity"]
        risk["daily_start_date"] = reset_at.astimezone(timezone.utc).strftime("%Y-%m-%d")
        store.session.execute(
            text(
                """
                UPDATE broker_accounts
                SET account_metadata = CAST(:meta AS jsonb),
                    risk_settings = CAST(:risk AS jsonb),
                    updated_at = NOW()
                WHERE id = CAST(:aid AS uuid)
                """
            ),
            {"aid": row["id"], "meta": json.dumps(meta), "risk": json.dumps(risk)},
        )

    portfolio_id = store.session.execute(
        text("SELECT id::text FROM owner_trading_portfolios WHERE slug = :slug"),
        {"slug": LIVE_SIM_OWNER_SLUG},
    ).scalar()
    if portfolio_id:
        store.session.execute(
            text(
                """
                UPDATE owner_portfolio_asset_allocations
                SET current_cash = starting_allocated_capital,
                    realized_pnl = 0, unrealized_pnl = 0, fees_paid = 0,
                    funding_paid = 0, gross_exposure = 0, open_sl_risk_usd = 0,
                    -- Risk baselines + counters belong to the experiment: a stale HWM /
                    -- daily start / trade_count from the previous run would keep old
                    -- losses alive inside the new experiment's drawdown & daily gates.
                    trade_count = 0,
                    high_water_mark = starting_allocated_capital,
                    daily_start_equity = starting_allocated_capital,
                    daily_start_date = (NOW() AT TIME ZONE 'UTC')::date::text,
                    live_sim_new_entries_allowed = TRUE,
                    updated_at = NOW()
                WHERE owner_portfolio_id = CAST(:pid AS uuid)
                """
            ),
            {"pid": portfolio_id},
        )
        # Owner master risk baselines (HWM / daily start) live in risk_settings JSON.
        owner_row = store.session.execute(
            text("SELECT risk_settings FROM owner_trading_portfolios WHERE id = CAST(:pid AS uuid)"),
            {"pid": portfolio_id},
        ).scalar()
        owner_risk = dict(owner_row or {})
        owner_risk["high_water_mark"] = float(LIVE_SIM_TARGET_CAPITAL)
        owner_risk["daily_start_equity"] = float(LIVE_SIM_TARGET_CAPITAL)
        owner_risk["daily_start_date"] = reset_at.astimezone(timezone.utc).strftime("%Y-%m-%d")
        store.session.execute(
            text(
                """
                UPDATE owner_trading_portfolios
                SET risk_settings = CAST(:risk AS jsonb), updated_at = NOW()
                WHERE id = CAST(:pid AS uuid)
                """
            ),
            {"pid": portfolio_id, "risk": json.dumps(owner_risk)},
        )
        store.session.execute(
            text(
                """
                UPDATE owner_trading_portfolios
                SET target_capital = :target, updated_at = NOW()
                WHERE id = CAST(:pid AS uuid)
                """
            ),
            {"pid": portfolio_id, "target": LIVE_SIM_TARGET_CAPITAL},
        )

    for slug, cap in (
        ("live-sim-ibkr-like", LIVE_SIM_IBKR_TOTAL),
        ("live-sim-kraken-like", LIVE_SIM_KRAKEN_TOTAL),
    ):
        store.session.execute(
            text(
                """
                UPDATE portfolio_broker_accounts pba
                SET allocated_capital = :cap, updated_at = NOW()
                FROM broker_accounts ba
                WHERE pba.broker_account_id = ba.id AND ba.slug = :slug
                """
            ),
            {"slug": slug, "cap": cap},
        )

    store.update_settings(
        f"live_sim_hard_wipe:{archive_id}",
        {
            "wiped_at": reset_at.isoformat(),
            "reason": "complete_simulation_data_wipe",
            "actions": {k: v for k, v in actions.items() if isinstance(v, int)},
        },
        description="Hard wipe of Owner Live Sim history for fresh $10k experiment",
    )
    return actions


def verify(store: TradingStore) -> dict:
    aids = _aids(store.session)
    out = {}
    out["live_sim_positions"] = store.session.execute(
        text("SELECT count(*) FROM live_sim_positions WHERE broker_account_id = ANY(CAST(:aids AS uuid[]))"),
        {"aids": aids},
    ).scalar()
    out["broker_orders"] = store.session.execute(
        text("SELECT count(*) FROM broker_orders WHERE broker_account_id = ANY(CAST(:aids AS uuid[]))"),
        {"aids": aids},
    ).scalar()
    out["broker_fills"] = store.session.execute(
        text(
            """
            SELECT count(*) FROM broker_fills bf
            JOIN broker_orders o ON o.id = bf.broker_order_id
            WHERE o.broker_account_id = ANY(CAST(:aids AS uuid[]))
            """
        ),
        {"aids": aids},
    ).scalar()
    out["allocation_log"] = store.session.execute(
        text(
            "SELECT count(*) FROM live_sim_allocation_log WHERE broker_account_id = ANY(CAST(:aids AS uuid[]))"
        ),
        {"aids": aids},
    ).scalar()
    out["attribution_lots"] = store.session.execute(
        text(
            "SELECT count(*) FROM broker_attribution_lots WHERE broker_account_id = ANY(CAST(:aids AS uuid[]))"
        ),
        {"aids": aids},
    ).scalar()
    bals = store.session.execute(
        text(
            """
            SELECT slug, starting_cash, equity, realized_pnl,
                   account_metadata->>'v32_observation_anchor' AS anchor
            FROM broker_accounts WHERE slug = ANY(:slugs) ORDER BY slug
            """
        ),
        {"slugs": list(_LIVE_SIM_SLUGS)},
    ).mappings().all()
    out["accounts"] = [dict(r) for r in bals]
    out["clean"] = all(
        int(out[k] or 0) == 0
        for k in (
            "live_sim_positions",
            "broker_orders",
            "broker_fills",
            "allocation_log",
            "attribution_lots",
        )
    )
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--execute", action="store_true")
    ap.add_argument("--release-containment", action="store_true")
    args = ap.parse_args()

    _require_prod()
    normalize_sqlalchemy_postgres_url(settings.database_url)

    with session_scope() as session:
        store = TradingStore(session)
        before = verify(store)
        print(json.dumps({"phase": "audit", "before": before}, indent=2, default=str))
        if not args.execute:
            print("Dry run — pass --execute to hard-wipe.")
            return

        if not is_live_sim_entries_blocked(store):
            activate_live_sim_entry_containment(
                store,
                reason="Hard wipe Live Sim history — temporary entry pause",
                activated_by="hard_wipe_live_sim_history",
            )

        reset_at = datetime.now(timezone.utc)
        archive_id = f"hard-wipe-{reset_at.strftime('%Y%m%dT%H%M%SZ')}-{uuid4().hex[:8]}"
        actions = wipe(store, archive_id=archive_id, reset_at=reset_at)

        containment = {"block_new_entries": True}
        if args.release_containment:
            after = verify(store)
            if not after["clean"]:
                session.commit()
                raise SystemExit(f"Post-wipe verification failed: {after}")
            containment = release_live_sim_entry_containment(store)

        session.commit()
        after = verify(store)
        print(
            json.dumps(
                {
                    "phase": "complete",
                    "archive_id": archive_id,
                    "observation_anchor": reset_at.isoformat(),
                    "actions": actions,
                    "containment": containment,
                    "after": after,
                    "starting": {
                        "owner_total": str(LIVE_SIM_TARGET_CAPITAL),
                        "ibkr": str(LIVE_SIM_IBKR_TOTAL),
                        "kraken": str(LIVE_SIM_KRAKEN_TOTAL),
                    },
                },
                indent=2,
                default=str,
            )
        )


if __name__ == "__main__":
    main()
