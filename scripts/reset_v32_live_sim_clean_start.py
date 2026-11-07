#!/usr/bin/env python3
"""
Archive pre-V3.2 Live Sim execution state and start a clean $10k virtual experiment.

Preserves historical rows; does not synthesize exit fills or PnL.
Research / competition accounts are never touched.
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
from quantara_engine.live_sim.v32_experiment import V32_EXPERIMENT_ID, V32_INITIAL_RISK_PCT
from quantara_engine.owner_portfolio.constants import (
    LIVE_SIM_IBKR_TOTAL,
    LIVE_SIM_KRAKEN_TOTAL,
    LIVE_SIM_PER_ASSET_CAPITAL,
    LIVE_SIM_TARGET_CAPITAL,
)
from quantara_engine.owner_portfolio.service import LIVE_SIM_OWNER_SLUG
from quantara_engine.persistence.store import TradingStore

_LIVE_SIM_SLUGS = (
    LIVE_SIM_10K_ACCOUNT_SLUG,
    "live-sim-ibkr-like",
    "live-sim-kraken-like",
)

_PAUSED_LIVE_STRATEGIES = (
    "gold-trend-pullback",
    "opening-range-breakout",
    "mean-reversion",
    "volatility-squeeze",
    "momentum-continuation",
)


def _require_prod() -> None:
    if not settings.database_configured:
        raise SystemExit("DATABASE_URL not configured")
    settings.validate_runtime_database()
    name = db_name_from_url(settings.database_url)
    if name != PRODUCTION_DB_NAME:
        raise SystemExit(f"Refusing: expected {PRODUCTION_DB_NAME}, got {name!r}")


def _account_map(session) -> dict[str, str]:
    rows = session.execute(
        text(
            """
            SELECT id::text, slug FROM broker_accounts
            WHERE slug = ANY(:slugs)
            """
        ),
        {"slugs": list(_LIVE_SIM_SLUGS)},
    ).mappings().all()
    out = {r["slug"]: r["id"] for r in rows}
    missing = [s for s in _LIVE_SIM_SLUGS if s not in out]
    if missing:
        raise SystemExit(f"Missing live sim broker accounts: {missing}")
    return out


def audit_live_sim(session, account_ids: list[str]) -> dict:
    aids = account_ids
    open_pos = session.execute(
        text(
            """
            SELECT COUNT(*) FROM live_sim_positions
            WHERE broker_account_id = ANY(CAST(:aids AS uuid[])) AND status = 'open'
            """
        ),
        {"aids": aids},
    ).scalar()
    old_live = session.execute(
        text(
            """
            SELECT COUNT(*) FROM live_sim_positions
            WHERE broker_account_id = ANY(CAST(:aids AS uuid[]))
              AND status = 'open'
              AND (
                strategy_slug = ANY(:paused)
                OR strategy_slug NOT IN ('v32-p2-live-sim', 'v31-rule-replay')
              )
            """
        ),
        {"aids": aids, "paused": list(_PAUSED_LIVE_STRATEGIES)},
    ).scalar()
    open_lots = session.execute(
        text(
            """
            SELECT COUNT(*) FROM broker_attribution_lots
            WHERE broker_account_id = ANY(CAST(:aids AS uuid[])) AND remaining_qty > 0
            """
        ),
        {"aids": aids},
    ).scalar()
    pending_orders = session.execute(
        text(
            """
            SELECT COUNT(*) FROM broker_orders
            WHERE broker_account_id = ANY(CAST(:aids AS uuid[]))
              AND status IN (
                'created', 'validating', 'accepted', 'submitted', 'partially_filled'
              )
            """
        ),
        {"aids": aids},
    ).scalar()
    pending_allocs = session.execute(
        text(
            """
            SELECT COUNT(*) FROM live_sim_allocation_log
            WHERE broker_account_id = ANY(CAST(:aids AS uuid[]))
              AND accepted = TRUE
              AND live_sim_position_id IS NULL
              AND broker_order_id IS NULL
              AND COALESCE(metadata->>'expired', 'false') <> 'true'
            """
        ),
        {"aids": aids},
    ).scalar()
    phys = session.execute(
        text(
            """
            SELECT COUNT(*) FROM broker_positions
            WHERE broker_account_id = ANY(CAST(:aids AS uuid[]))
              AND net_quantity <> 0
            """
        ),
        {"aids": aids},
    ).scalar()
    accounts: dict[str, dict] = {}
    for slug in _LIVE_SIM_SLUGS:
        row = session.execute(
            text(
                """
                SELECT slug, cash, balance, equity, realized_pnl, unrealized_pnl,
                       gross_exposure, net_exposure, is_active,
                       account_metadata->>'v32_observation_anchor' AS v32_anchor
                FROM broker_accounts WHERE slug = :slug
                """
            ),
            {"slug": slug},
        ).mappings().first()
        if row:
            accounts[slug] = dict(row)
        open_n = session.execute(
            text(
                """
                SELECT COUNT(*) FROM live_sim_positions p
                JOIN broker_accounts ba ON ba.id = p.broker_account_id
                WHERE ba.slug = :slug AND p.status = 'open'
                """
            ),
            {"slug": slug},
        ).scalar()
        exp = session.execute(
            text(
                """
                SELECT COALESCE(SUM(ABS(bp.net_quantity * bp.mark_price)), 0)
                FROM broker_positions bp
                JOIN broker_accounts ba ON ba.id = bp.broker_account_id
                WHERE ba.slug = :slug
                """
            ),
            {"slug": slug},
        ).scalar()
        accounts[slug]["open_positions"] = int(open_n or 0)
        accounts[slug]["open_exposure"] = float(exp or 0)
    return {
        "open_live_sim_positions": int(open_pos or 0),
        "old_live_strategy_open_positions": int(old_live or 0),
        "open_attribution_lots": int(open_lots or 0),
        "pending_broker_orders": int(pending_orders or 0),
        "pending_live_sim_intents": int(pending_allocs or 0),
        "nonzero_broker_positions": int(phys or 0),
        "accounts": accounts,
    }


def _stamp_v32_anchor(store: TradingStore, *, anchor: datetime) -> None:
    anchor_iso = anchor.isoformat()
    for slug in _LIVE_SIM_SLUGS:
        row = store.session.execute(
            text(
                """
                SELECT id::text, account_metadata, risk_settings
                FROM broker_accounts WHERE slug = :slug
                """
            ),
            {"slug": slug},
        ).mappings().first()
        if not row:
            continue
        meta = dict(row.get("account_metadata") or {})
        prev = meta.get("v32_observation_anchor")
        if prev:
            archived = meta.get("v32_archived_anchors") or []
            if isinstance(archived, list):
                archived.append(prev)
            meta["v32_archived_anchors"] = archived
        meta["baseline_reset_at"] = anchor_iso
        meta["v32_observation_anchor"] = anchor_iso
        meta["live_sim_experiment"] = V32_EXPERIMENT_ID
        meta["v32_clean_start_at"] = anchor_iso
        risk = dict(row.get("risk_settings") or {})
        risk["risk_per_trade_pct"] = str(Decimal(str(V32_INITIAL_RISK_PCT)))
        risk["high_water_mark"] = str(LIVE_SIM_TARGET_CAPITAL)
        risk["daily_start_equity"] = str(LIVE_SIM_TARGET_CAPITAL)
        risk["daily_start_date"] = anchor.date().isoformat()
        store.session.execute(
            text(
                """
                UPDATE broker_accounts
                SET account_metadata = CAST(:meta AS jsonb),
                    risk_settings = CAST(:risk AS jsonb)
                WHERE id = CAST(:aid AS uuid)
                """
            ),
            {"aid": row["id"], "meta": json.dumps(meta), "risk": json.dumps(risk)},
        )


def reset_live_sim_state(store: TradingStore, *, archive_id: str, reset_at: datetime) -> dict:
    accounts = _account_map(store.session)
    aids = list(accounts.values())
    actions: dict[str, int | str] = {"archive_id": archive_id}

    closed = store.session.execute(
        text(
            """
            UPDATE live_sim_positions
            SET status = 'closed',
                closed_at = :ts,
                unrealized_pnl = 0,
                updated_at = NOW()
            WHERE broker_account_id = ANY(CAST(:aids AS uuid[]))
              AND status = 'open'
            """
        ),
        {"aids": aids, "ts": reset_at},
    ).rowcount
    actions["live_sim_positions_archived"] = closed

    lots = store.session.execute(
        text(
            """
            UPDATE broker_attribution_lots
            SET remaining_qty = 0
            WHERE broker_account_id = ANY(CAST(:aids AS uuid[]))
              AND remaining_qty > 0
            """
        ),
        {"aids": aids},
    ).rowcount
    actions["attribution_lots_cleared"] = lots

    # Detach fill history from positions before delete (preserve fills; clear live inventory).
    detached = store.session.execute(
        text(
            """
            UPDATE broker_fills bf
            SET broker_position_id = NULL
            FROM broker_positions bp
            WHERE bf.broker_position_id = bp.id
              AND bp.broker_account_id = ANY(CAST(:aids AS uuid[]))
            """
        ),
        {"aids": aids},
    ).rowcount
    actions["broker_fills_detached"] = detached

    deleted_bp = store.session.execute(
        text(
            """
            DELETE FROM broker_positions
            WHERE broker_account_id = ANY(CAST(:aids AS uuid[]))
            """
        ),
        {"aids": aids},
    ).rowcount
    actions["broker_positions_removed"] = deleted_bp

    cancelled = store.session.execute(
        text(
            """
            UPDATE broker_orders
            SET status = 'cancelled', updated_at = NOW()
            WHERE broker_account_id = ANY(CAST(:aids AS uuid[]))
              AND status IN (
                'created', 'validating', 'accepted', 'submitted', 'partially_filled'
              )
            """
        ),
        {"aids": aids},
    ).rowcount
    actions["broker_orders_cancelled"] = cancelled

    prot = store.session.execute(
        text(
            """
            UPDATE broker_protective_orders
            SET status = 'cancelled', updated_at = NOW()
            WHERE broker_account_id = ANY(CAST(:aids AS uuid[]))
              AND status = 'active'
            """
        ),
        {"aids": aids},
    ).rowcount
    actions["protective_orders_cancelled"] = prot

    expired = store.session.execute(
        text(
            """
            UPDATE live_sim_allocation_log
            SET metadata = COALESCE(metadata, '{}'::jsonb)
                || jsonb_build_object(
                  'expired', true,
                  'expired_reason', 'v32_clean_start',
                  'archived_run', :archive_id
                )
            WHERE broker_account_id = ANY(CAST(:aids AS uuid[]))
              AND accepted = TRUE
              AND live_sim_position_id IS NULL
              AND broker_order_id IS NULL
              AND COALESCE(metadata->>'expired', 'false') <> 'true'
            """
        ),
        {"aids": aids, "archive_id": archive_id},
    ).rowcount
    actions["pending_allocations_expired"] = expired

    store.session.execute(
        text(
            """
            UPDATE broker_accounts
            SET starting_cash = :cap,
                cash = :cap,
                balance = :cap,
                equity = :cap,
                realized_pnl = 0,
                gross_realized_pnl = 0,
                unrealized_pnl = 0,
                gross_exposure = 0,
                net_exposure = 0,
                initial_margin_used = 0,
                maintenance_margin_required = 0,
                free_margin = :cap,
                available_margin = :cap,
                fees_paid = 0,
                pending_owner_reset = FALSE,
                updated_at = NOW()
            WHERE slug = :slug
            """
        ),
        {"slug": LIVE_SIM_10K_ACCOUNT_SLUG, "cap": LIVE_SIM_TARGET_CAPITAL},
    )
    # Per-slug execution broker caps (kraken spot_crypto)
    store.session.execute(
        text(
            """
            UPDATE broker_accounts SET
              starting_cash = :cap, cash = :cap, balance = :cap, equity = :cap,
              realized_pnl = 0, gross_realized_pnl = 0, unrealized_pnl = 0,
              gross_exposure = 0, net_exposure = 0, fees_paid = 0,
              initial_margin_used = 0, maintenance_margin_required = 0,
              free_margin = :cap, available_margin = :cap
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
              free_margin = :cap, available_margin = :cap
            WHERE slug = 'live-sim-kraken-like'
            """
        ),
        {"cap": LIVE_SIM_KRAKEN_TOTAL},
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
                    realized_pnl = 0,
                    unrealized_pnl = 0,
                    fees_paid = 0,
                    funding_paid = 0,
                    gross_exposure = 0,
                    open_sl_risk_usd = 0,
                    updated_at = NOW()
                WHERE owner_portfolio_id = CAST(:pid AS uuid)
                """
            ),
            {"pid": portfolio_id},
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

    per_asset = LIVE_SIM_PER_ASSET_CAPITAL
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
        f"live_sim_v32_archive:{archive_id}",
        {
            "archived_at": reset_at.isoformat(),
            "reason": "v32_clean_experiment_start",
            "positions_archived": closed,
            "attribution_lots_cleared": lots,
        },
        description="V3.2 clean start — prior Live Sim run archived",
    )
    return actions


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true", help="Apply reset (default: audit only)")
    parser.add_argument(
        "--release-containment",
        action="store_true",
        help="Release Live Sim entry containment after successful reset (execute only)",
    )
    args = parser.parse_args()

    _require_prod()
    normalize_sqlalchemy_postgres_url(settings.database_url)

    with session_scope() as session:
        store = TradingStore(session)
        accounts = _account_map(session)
        aids = list(accounts.values())
        before = audit_live_sim(session, aids)
        print(json.dumps({"phase": "audit", "before": before}, indent=2, default=str))

        if not args.execute:
            print("Dry run — pass --execute to archive state and reset balances.")
            return

        if not is_live_sim_entries_blocked(store):
            activate_live_sim_entry_containment(
                store,
                reason="V3.2 clean experiment reset — temporary entry pause",
                activated_by="reset_v32_live_sim_clean_start",
            )

        reset_at = datetime.now(timezone.utc)
        archive_id = f"v32-clean-{reset_at.strftime('%Y%m%dT%H%M%SZ')}-{uuid4().hex[:8]}"
        actions = reset_live_sim_state(store, archive_id=archive_id, reset_at=reset_at)
        _stamp_v32_anchor(store, anchor=reset_at)

        containment = {"block_new_entries": True}
        if args.release_containment:
            after = audit_live_sim(session, aids)
            ok = (
                after["open_live_sim_positions"] == 0
                and after["open_attribution_lots"] == 0
                and after["pending_broker_orders"] == 0
                and after["pending_live_sim_intents"] == 0
                and after["nonzero_broker_positions"] == 0
            )
            if not ok:
                session.commit()
                raise SystemExit(f"Post-reset verification failed: {after}")
            containment = release_live_sim_entry_containment(store)

        session.commit()
        after = audit_live_sim(session, aids)
        print(
            json.dumps(
                {
                    "phase": "complete",
                    "archive_id": archive_id,
                    "observation_anchor": reset_at.isoformat(),
                    "actions": actions,
                    "containment": containment,
                    "after": after,
                },
                indent=2,
                default=str,
            )
        )


if __name__ == "__main__":
    main()
