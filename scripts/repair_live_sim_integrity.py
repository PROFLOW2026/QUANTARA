"""Repair Live Sim state from broker physical truth — run after lifecycle code fixes."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "engine"))

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from quantara_engine.broker.attribution import link_strategy_position_to_fill
from quantara_engine.live_sim.entry_authority import position_has_physical_entry_evidence
from quantara_engine.live_sim.integrity_containment import (
    activate_live_sim_entry_containment,
    is_live_sim_entries_blocked,
)
from quantara_engine.live_sim.risk_policy import update_high_water_mark
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


def _signed_qty(direction: str, qty: Decimal) -> Decimal:
    return qty if direction.lower() == "long" else -qty


def repair(*, dry_run: bool = False) -> dict:
    engine = create_engine(_load_database_url())
    Session = sessionmaker(bind=engine)
    session = Session()
    store = TradingStore(session)
    report: dict = {"dry_run": dry_run, "actions": []}

    if not is_live_sim_entries_blocked(store):
        if not dry_run:
            activate_live_sim_entry_containment(
                store,
                reason="EOD integrity repair — block new entries until reconciliation clean",
            )
        report["actions"].append("containment_activated")

    brokers = session.execute(
        text(
            """
            SELECT id::text, slug, equity, starting_cash, risk_settings
            FROM broker_accounts
            WHERE slug IN ('live-sim-ibkr-like', 'live-sim-kraken-like')
            ORDER BY slug
            """
        )
    ).mappings().all()

    for b in brokers:
        equity = Decimal(str(b["equity"] or b["starting_cash"]))
        if not dry_run:
            new_hwm = update_high_water_mark(store, b["id"], equity)
        else:
            rs = b["risk_settings"] or {}
            old = Decimal(str(rs.get("high_water_mark") or equity))
            new_hwm = max(old, equity)
        report.setdefault("hwm", {})[b["slug"]] = float(new_hwm)

    open_positions = session.execute(
        text(
            """
            SELECT p.id::text AS id, p.broker_account_id::text AS account_id,
                   p.direction::text AS direction, p.quantity, p.status, p.opportunity_key,
                   i.symbol, ba.slug AS broker_slug
            FROM live_sim_positions p
            JOIN instruments i ON i.id = p.instrument_id
            JOIN broker_accounts ba ON ba.id = p.broker_account_id
            WHERE p.status = 'open'
            """
        )
    ).mappings().all()

    for pos in open_positions:
        sym = str(pos["symbol"]).upper()
        has_entry = position_has_physical_entry_evidence(
            store,
            position_id=pos["id"],
            broker_account_id=pos["account_id"],
            symbol=sym,
            opportunity_key=pos.get("opportunity_key"),
        )
        attr = session.execute(
            text(
                """
                SELECT COALESCE(SUM(remaining_qty), 0) AS qty
                FROM broker_attribution_lots
                WHERE broker_account_id = :aid AND symbol = :sym
                  AND strategy_position_id = :pid AND remaining_qty > 0
                """
            ),
            {"aid": pos["account_id"], "sym": sym, "pid": pos["id"]},
        ).scalar()
        phys = session.execute(
            text(
                """
                SELECT COALESCE(bp.net_quantity, 0) AS qty
                FROM broker_positions bp
                JOIN instruments i ON i.id = bp.instrument_id
                WHERE bp.broker_account_id = :aid AND i.symbol = :sym
                """
            ),
            {"aid": pos["account_id"], "sym": sym},
        ).scalar()
        attr_qty = Decimal(str(attr or 0))
        phys_qty = Decimal(str(phys or 0))

        if attr_qty == 0:
            reason = (
                "never_had_physical_entry"
                if not has_entry
                else "physical_flat_attribution_consumed"
            )
            report["actions"].append(f"close_orphan_shadow:{pos['id']}:{sym}:{reason}")
            if not dry_run:
                session.execute(
                    text(
                        """
                        UPDATE live_sim_positions
                        SET status = 'closed', closed_at = NOW(), updated_at = NOW()
                        WHERE id = :id AND status = 'open'
                        """
                    ),
                    {"id": pos["id"]},
                )
                store.update_settings(
                    f"live_sim_integrity:invalid_close:{pos['id']}",
                    {
                        "reason": reason,
                        "repaired_at": datetime.now(timezone.utc).isoformat(),
                    },
                    flush=False,
                )
            continue

        lot_row = session.execute(
            text(
                """
                SELECT direction::text, remaining_qty
                FROM broker_attribution_lots
                WHERE broker_account_id = :aid AND strategy_position_id = :pid
                  AND remaining_qty > 0
                ORDER BY opened_at DESC
                LIMIT 1
                """
            ),
            {"aid": pos["account_id"], "pid": pos["id"]},
        ).mappings().first()
        if lot_row and lot_row["direction"] != str(pos["direction"]).lower():
            report["actions"].append(
                f"reconcile_shadow_to_attribution:{pos['id']}:{sym}:"
                f"{pos['direction']}->{lot_row['direction']}"
            )
            if not dry_run:
                session.execute(
                    text(
                        """
                        UPDATE live_sim_positions
                        SET direction = CAST(:dir AS direction),
                            quantity = :qty, updated_at = NOW()
                        WHERE id = :id AND status = 'open'
                        """
                    ),
                    {
                        "id": pos["id"],
                        "dir": lot_row["direction"],
                        "qty": lot_row["remaining_qty"],
                    },
                )

    # Rebind attribution from entry fills where strategy_position_id missing
    orphan_lots = session.execute(
        text(
            """
            SELECT l.id::text AS lot_id, l.broker_fill_id::text AS fill_id,
                   l.direction::text AS lot_dir, l.remaining_qty,
                   p.id::text AS position_id, p.direction::text AS pos_dir
            FROM broker_attribution_lots l
            JOIN broker_fills f ON f.id = l.broker_fill_id
            JOIN broker_orders o ON o.id = f.broker_order_id
            JOIN live_sim_positions p ON p.opportunity_key = l.opportunity_key
              AND p.broker_account_id = l.broker_account_id
              AND p.status = 'open'
            WHERE l.strategy_position_id IS NULL
              AND o.order_purpose = 'entry'
              AND l.remaining_qty > 0
            """
        )
    ).mappings().all()
    for row in orphan_lots:
        if row["lot_dir"] != row["pos_dir"]:
            report["actions"].append(
                f"zero_misbound_lot:{row['lot_id']}:dir_{row['lot_dir']}_vs_{row['pos_dir']}"
            )
            if not dry_run:
                session.execute(
                    text(
                        """
                        UPDATE broker_attribution_lots
                        SET remaining_qty = 0, strategy_position_id = NULL
                        WHERE id = CAST(:id AS uuid)
                        """
                    ),
                    {"id": row["lot_id"]},
                )
            continue
        report["actions"].append(f"rebind_lot:{row['lot_id']}->{row['position_id']}")
        if not dry_run:
            link_strategy_position_to_fill(
                store,
                broker_fill_id=row["fill_id"],
                strategy_position_id=row["position_id"],
            )

    # Strip opposite-direction lots incorrectly bound to a single strategy leg.
    mixed = session.execute(
        text(
            """
            SELECT p.id::text AS position_id, p.direction::text AS pos_dir,
                   l.id::text AS lot_id, l.direction::text AS lot_dir
            FROM live_sim_positions p
            JOIN broker_attribution_lots l ON l.strategy_position_id = p.id
            WHERE p.status = 'open' AND l.remaining_qty > 0
              AND l.direction::text <> p.direction::text
            """
        )
    ).mappings().all()
    for row in mixed:
        report["actions"].append(
            f"zero_opposite_lot:{row['lot_id']}:on:{row['position_id']}"
        )
        if not dry_run:
            session.execute(
                text(
                    """
                    UPDATE broker_attribution_lots
                    SET remaining_qty = 0
                    WHERE id = CAST(:id AS uuid)
                    """
                ),
                {"id": row["lot_id"]},
            )

    # Remove duplicate close ledger rows (keep earliest)
    dupes = session.execute(
        text(
            """
            SELECT broker_fill_id::text, strategy_position_id::text,
                   quantity, entry_price, exit_price, direction::text,
                   array_agg(id::text ORDER BY created_at) AS ids
            FROM broker_attribution_ledger
            WHERE exit_price IS NOT NULL
            GROUP BY broker_fill_id, strategy_position_id, quantity, entry_price, exit_price, direction
            HAVING COUNT(*) > 1
            """
        )
    ).mappings().all()
    for d in dupes:
        ids = list(d["ids"] or [])
        for extra_id in ids[1:]:
            report["actions"].append(f"remove_duplicate_ledger:{extra_id}")
            if not dry_run:
                session.execute(
                    text("DELETE FROM broker_attribution_ledger WHERE id = CAST(:id AS uuid)"),
                    {"id": extra_id},
                )

    # Reconciliation summary
    recon: dict = {"brokers": {}, "symbols": {}}
    for b in brokers:
        rows = session.execute(
            text(
                """
                SELECT i.symbol, bp.net_quantity, bp.average_price, bp.mark_price,
                       bp.unrealized_pnl
                FROM broker_positions bp
                JOIN instruments i ON i.id = bp.instrument_id
                WHERE bp.broker_account_id = :aid AND ABS(bp.net_quantity) > 0
                """
            ),
            {"aid": b["id"]},
        ).mappings().all()
        recon["brokers"][b["slug"]] = [dict(r) for r in rows]

    for sym_row in session.execute(
        text(
            """
            SELECT i.symbol,
                   SUM(CASE WHEN p.direction = 'long' THEN p.quantity ELSE -p.quantity END) AS strat_signed,
                   ba.slug
            FROM live_sim_positions p
            JOIN instruments i ON i.id = p.instrument_id
            JOIN broker_accounts ba ON ba.id = p.broker_account_id
            WHERE p.status = 'open'
              AND ba.slug IN ('live-sim-ibkr-like', 'live-sim-kraken-like')
            GROUP BY i.symbol, ba.slug
            """
        )
    ).mappings():
        sym = str(sym_row["symbol"]).upper()
        broker = sym_row["slug"]
        attr_sum = session.execute(
            text(
                """
                SELECT COALESCE(SUM(
                  CASE WHEN l.direction = 'long' THEN l.remaining_qty ELSE -l.remaining_qty END
                ), 0) AS signed
                FROM broker_attribution_lots l
                JOIN broker_accounts ba ON ba.id = l.broker_account_id
                WHERE l.symbol = :sym AND ba.slug = :slug AND l.remaining_qty > 0
                """
            ),
            {"sym": sym, "slug": broker},
        ).scalar()
        phys = session.execute(
            text(
                """
                SELECT COALESCE(bp.net_quantity, 0)
                FROM broker_positions bp
                JOIN instruments i ON i.id = bp.instrument_id
                JOIN broker_accounts ba ON ba.id = bp.broker_account_id
                WHERE i.symbol = :sym AND ba.slug = :slug
                """
            ),
            {"sym": sym, "slug": broker},
        ).scalar()
        recon["symbols"][f"{broker}:{sym}"] = {
            "strategy_signed": float(sym_row["strat_signed"] or 0),
            "attribution_signed": float(attr_sum or 0),
            "physical_signed": float(phys or 0),
            "diff": float(
                Decimal(str(sym_row["strat_signed"] or 0))
                - Decimal(str(attr_sum or 0))
            ),
        }

    report["reconciliation"] = recon

    if not dry_run:
        session.commit()
    else:
        session.rollback()

    session.close()
    return report


if __name__ == "__main__":
    dry = "--dry-run" in sys.argv
    out = repair(dry_run=dry)
    print(json.dumps(out, indent=2, default=str))
