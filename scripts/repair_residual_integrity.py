#!/usr/bin/env python3
"""Focused residual integrity repair — no containment, no strategy changes."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "engine"))

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from quantara_engine.broker.attribution import link_orphan_lots_via_intent_chain
from quantara_engine.broker.physical_risk import compute_physical_broker_risk
from quantara_engine.live_sim.missed_exit_recovery import _close_shadow_only
from quantara_engine.owner_portfolio.aggregation import aggregate_owner_portfolio
from quantara_engine.owner_portfolio.service import LIVE_SIM_OWNER_SLUG
from quantara_engine.persistence.store import TradingStore


def _load_database_url() -> str:
    env_path = ROOT / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            if key.strip() in ("DATABASE_URL", "DIRECT_URL"):
                return value.strip().strip('"').strip("'")
    url = __import__("os").environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL not configured")
    return url


def _metrics(store: TradingStore, session) -> dict:
    shadow_desync = session.execute(
        text(
            """
            SELECT COUNT(*) FROM live_sim_positions lsp
            LEFT JOIN broker_positions bp ON bp.broker_account_id = lsp.broker_account_id
              AND bp.instrument_id = lsp.instrument_id
            WHERE lsp.status = 'open' AND COALESCE(bp.net_quantity, 0) = 0
            """
        )
    ).scalar()
    broker_open_no_shadow = session.execute(
        text(
            """
            SELECT COUNT(*) FROM broker_positions bp
            JOIN broker_accounts ba ON ba.id = bp.broker_account_id
            WHERE ba.slug IN ('live-sim-ibkr-like', 'live-sim-kraken-like')
              AND bp.net_quantity <> 0
              AND NOT EXISTS (
                SELECT 1 FROM live_sim_positions lsp
                WHERE lsp.broker_account_id = bp.broker_account_id
                  AND lsp.instrument_id = bp.instrument_id AND lsp.status = 'open'
              )
            """
        )
    ).scalar()
    orphan_lots = session.execute(
        text(
            """
            SELECT COUNT(*) FROM broker_attribution_lots
            WHERE remaining_qty > 0 AND strategy_position_id IS NULL
            """
        )
    ).scalar()
    pr = compute_physical_broker_risk(store)
    dup_groups = session.execute(
        text(
            """
            SELECT COUNT(*) FROM (
              SELECT broker_order_id, fill_quantity, fill_price, filled_at
              FROM broker_fills
              GROUP BY broker_order_id, fill_quantity, fill_price, filled_at
              HAVING COUNT(*) > 1
            ) d
            """
        )
    ).scalar()
    dup_active_qty = session.execute(
        text(
            """
            SELECT COALESCE(SUM(l.remaining_qty), 0)
            FROM broker_fills f
            JOIN broker_attribution_lots l ON l.broker_fill_id = f.id
            WHERE f.id IN (
              SELECT unnest(array_agg(id))
              FROM broker_fills
              GROUP BY broker_order_id, fill_quantity, fill_price, filled_at
              HAVING COUNT(*) > 1
            ) AND l.remaining_qty > 0
            """
        )
    ).scalar()
    accepted_limbo = session.execute(
        text(
            """
            SELECT COUNT(*) FROM live_sim_allocation_log
            WHERE accepted = TRUE AND broker_order_id IS NULL
            """
        )
    ).scalar()
    owner = aggregate_owner_portfolio(store, slug=LIVE_SIM_OWNER_SLUG)
    brokers = session.execute(
        text(
            """
            SELECT slug, equity, realized_pnl, unrealized_pnl
            FROM broker_accounts
            WHERE slug IN ('live-sim-ibkr-like', 'live-sim-kraken-like')
            """
        )
    ).mappings().all()
    ibkr = next((b for b in brokers if b["slug"] == "live-sim-ibkr-like"), None)
    kraken = next((b for b in brokers if b["slug"] == "live-sim-kraken-like"), None)
    broker_sum = Decimal("0")
    broker_realized = Decimal("0")
    broker_unrealized = Decimal("0")
    if ibkr and kraken:
        broker_sum = Decimal(str(ibkr["equity"] or 0)) + Decimal(str(kraken["equity"] or 0))
        broker_realized = Decimal(str(ibkr["realized_pnl"] or 0)) + Decimal(
            str(kraken["realized_pnl"] or 0)
        )
        broker_unrealized = Decimal(str(ibkr["unrealized_pnl"] or 0)) + Decimal(
            str(kraken["unrealized_pnl"] or 0)
        )
    research = session.execute(
        text(
            """
            SELECT starting_cash, equity, realized_pnl, unrealized_pnl
            FROM broker_accounts WHERE slug = 'quantara_paper_competition'
            """
        )
    ).mappings().first()
    research_diff = None
    if research:
        start = Decimal(str(research["starting_cash"] or 0))
        eq = Decimal(str(research["equity"] or 0))
        realized = Decimal(str(research["realized_pnl"] or 0))
        unrealized = Decimal(str(research["unrealized_pnl"] or 0))
        research_diff = (start + realized + unrealized - eq).quantize(Decimal("0.01"))
    return {
        "shadow_broker_desync": int(shadow_desync or 0),
        "broker_open_no_shadow": int(broker_open_no_shadow or 0),
        "orphan_lots": int(orphan_lots or 0),
        "physical_risk_missing": int(pr.get("physical_risk_missing_count") or 0),
        "physical_risk_complete": bool(pr.get("physical_risk_complete")),
        "duplicate_fill_groups": int(dup_groups or 0),
        "duplicate_active_qty": float(dup_active_qty or 0),
        "accepted_limbo": int(accepted_limbo or 0),
        "owner_equity_diff": float(
            (Decimal(str(owner.total_equity or 0)) - broker_sum).quantize(Decimal("0.01"))
        ),
        "realized_diff": float(
            (Decimal(str(owner.total_realized_pnl or 0)) - broker_realized).quantize(Decimal("0.01"))
        ),
        "unrealized_diff": float(
            (Decimal(str(owner.total_unrealized_pnl or 0)) - broker_unrealized).quantize(Decimal("0.01"))
        ),
        "research_financial_diff": float(research_diff) if research_diff is not None else None,
    }


def _close_stale_live_sim_shadows(store: TradingStore, *, dry_run: bool) -> list[dict]:
    rows = store.session.execute(
        text(
            """
            SELECT lsp.id::text AS position_id, i.symbol, lsp.quantity,
                   lsp.opened_at, ba.slug AS broker_account
            FROM live_sim_positions lsp
            JOIN instruments i ON i.id = lsp.instrument_id
            JOIN broker_accounts ba ON ba.id = lsp.broker_account_id
            LEFT JOIN broker_positions bp ON bp.broker_account_id = lsp.broker_account_id
              AND bp.instrument_id = lsp.instrument_id
            WHERE lsp.status = 'open'
              AND COALESCE(bp.net_quantity, 0) = 0
              AND ba.slug IN ('live-sim-ibkr-like', 'live-sim-kraken-like')
            """
        )
    ).mappings().all()
    actions: list[dict] = []
    for row in rows:
        exit_fill = store.session.execute(
            text(
                """
                SELECT f.id::text AS fill_id, f.filled_at, o.order_purpose::text
                FROM broker_fills f
                JOIN broker_orders o ON o.id = f.broker_order_id
                JOIN broker_attribution_ledger bl ON bl.broker_fill_id = f.id
                WHERE bl.strategy_position_id = CAST(:pid AS uuid)
                  AND o.order_purpose IN ('sl', 'tp', 'manual_close', 'flatten')
                ORDER BY f.filled_at DESC
                LIMIT 1
                """
            ),
            {"pid": row["position_id"]},
        ).mappings().first()
        if not exit_fill:
            exit_fill = store.session.execute(
                text(
                    """
                    SELECT f.id::text AS fill_id, f.filled_at, o.order_purpose::text
                    FROM broker_fills f
                    JOIN broker_orders o ON o.id = f.broker_order_id
                    WHERE o.broker_account_id = (
                        SELECT broker_account_id FROM live_sim_positions WHERE id = CAST(:pid AS uuid)
                      )
                      AND o.order_purpose IN ('sl', 'tp')
                      AND f.filled_at >= (
                        SELECT opened_at FROM live_sim_positions WHERE id = CAST(:pid AS uuid)
                      )
                    ORDER BY f.filled_at DESC
                    LIMIT 1
                    """
                ),
                {"pid": row["position_id"]},
            ).mappings().first()
        closed_at = exit_fill["filled_at"] if exit_fill else datetime.now(timezone.utc)
        action = {
            "position_id": row["position_id"],
            "symbol": row["symbol"],
            "broker_account": row["broker_account"],
            "action": "close_shadow_broker_already_flat"
            if exit_fill
            else "close_shadow_broker_flat_no_exit_fill",
            "exit_fill_id": exit_fill["fill_id"] if exit_fill else None,
            "closed_at": closed_at.isoformat() if hasattr(closed_at, "isoformat") else str(closed_at),
        }
        if not dry_run:
            _close_shadow_only(
                store,
                position_id=row["position_id"],
                closed_at=closed_at,
                reason=action["action"],
                trigger_ts=closed_at,
            )
        actions.append(action)
    return actions


def repair(*, dry_run: bool = False) -> dict:
    engine = create_engine(_load_database_url())
    Session = sessionmaker(bind=engine)
    session = Session()
    store = TradingStore(session)
    report: dict = {
        "dry_run": dry_run,
        "before": _metrics(store, session),
        "actions": [],
    }
    try:
        report["actions"].extend(_close_stale_live_sim_shadows(store, dry_run=dry_run))
        linked = link_orphan_lots_via_intent_chain(store) if not dry_run else []
        if dry_run:
            would_link = session.execute(
                text(
                    """
                    SELECT COUNT(DISTINCT l.id)
                    FROM broker_attribution_lots l
                    JOIN broker_fills f ON f.id = l.broker_fill_id
                    JOIN broker_orders o ON o.id = f.broker_order_id
                    JOIN order_intents oi
                      ON oi.id = CAST(replace(o.idempotency_key, 'intent:', '') AS uuid)
                    JOIN orders ord ON ord.intent_id = oi.id
                    JOIN fills ff ON ff.order_id = ord.id
                      AND ff.position_id IS NOT NULL
                      AND ff.fill_price = f.fill_price
                    WHERE l.remaining_qty > 0
                      AND l.strategy_position_id IS NULL
                      AND o.idempotency_key LIKE 'intent:%'
                    """
                )
            ).scalar()
            report["actions"].append({"orphan_lots_would_link": int(would_link or 0)})
        else:
            report["actions"].append({"orphan_lots_linked": linked})
        report["after"] = _metrics(store, session) if not dry_run else report["before"]
        if dry_run:
            session.rollback()
        else:
            store.update_settings(
                "integrity:residual_repair",
                {
                    "repaired_at": datetime.now(timezone.utc).isoformat(),
                    "before": report["before"],
                    "after": report["after"],
                    "actions": report["actions"],
                },
                flush=True,
            )
            session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    report = repair(dry_run=args.dry_run)
    print(json.dumps(report, indent=2, default=str))
    if args.dry_run:
        return 0
    after = report["after"]
    ok = (
        after["shadow_broker_desync"] == 0
        and after["broker_open_no_shadow"] == 0
        and after["orphan_lots"] == 0
        and after["physical_risk_complete"]
        and after["owner_equity_diff"] == 0.0
        and after["realized_diff"] == 0.0
        and after["unrealized_diff"] == 0.0
        and (after["research_financial_diff"] or 0) == 0.0
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
