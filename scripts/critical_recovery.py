#!/usr/bin/env python3
"""Critical recovery orchestrator — containment, repair, reconciliation."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "engine"))

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from quantara_engine.db.session import session_scope
from quantara_engine.execution.crypto_missed_exit_recovery import recover_missed_research_crypto_exits
from quantara_engine.live_sim.candidate_log import (
    finalize_expired_accepted_limbo,
    reconcile_accepted_limbo_allocations,
)
from quantara_engine.live_sim.integrity_containment import (
    activate_live_sim_entry_containment,
    is_live_sim_entries_blocked,
)
from quantara_engine.live_sim.missed_exit_recovery import recover_missed_live_sim_exits
from quantara_engine.persistence.store import TradingStore
from quantara_engine.trading.asset_trading_controls import (
    SCOPE_RESEARCH,
    list_paused_symbols,
    set_asset_trading_paused,
)

TZ = ZoneInfo("Asia/Jerusalem")
BACKUP_DIR = ROOT / "backups" / "critical_recovery"


def _db_url() -> str:
    return next(
        l.split("=", 1)[1].strip().strip('"').strip("'")
        for l in (ROOT / ".env").read_text(encoding="utf-8").splitlines()
        if l.startswith("DATABASE_URL=")
    )


def phase1_containment(store: TradingStore) -> dict:
    if not is_live_sim_entries_blocked(store):
        activate_live_sim_entry_containment(
            store,
            reason="Critical recovery — block new Live Sim entries",
            activated_by="critical_recovery",
        )
    for sym in ("BTCUSD", "ETHUSD"):
        set_asset_trading_paused(store, scope=SCOPE_RESEARCH, symbol=sym, paused=True)
    store.session.commit()
    return {
        "live_sim_containment": is_live_sim_entries_blocked(store),
        "research_paused": list_paused_symbols(store.get_settings_dict(), SCOPE_RESEARCH),
    }


def phase4_backup_and_snapshot() -> dict:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(TZ).strftime("%Y%m%d_%H%M%S")
    dump_path = BACKUP_DIR / f"quantara_prod_pre_recovery_{stamp}.sql"
    url = _db_url()
    # pg_dump via subprocess
    pg_dump = __import__("shutil").which("pg_dump")
    if not pg_dump:
        for candidate in (
            r"C:\Program Files\PostgreSQL\17\bin\pg_dump.exe",
            r"C:\Program Files\PostgreSQL\16\bin\pg_dump.exe",
        ):
            if Path(candidate).exists():
                pg_dump = candidate
                break
    if not pg_dump:
        raise RuntimeError("pg_dump not found — install PostgreSQL client tools")
    subprocess.run(
        [pg_dump, url, "-f", str(dump_path), "--no-owner", "--no-acl"],
        check=True,
        capture_output=True,
    )
    session = sessionmaker(bind=create_engine(url))()
    snap = {
        "backup_file": str(dump_path),
        "research_open": session.execute(
            text("SELECT COUNT(*) FROM positions WHERE status='open' AND mode='paper'")
        ).scalar(),
        "live_sim_open": session.execute(
            text("SELECT COUNT(*) FROM live_sim_positions WHERE status='open'")
        ).scalar(),
        "accepted_limbo": session.execute(
            text(
                """
                SELECT COUNT(*) FROM live_sim_allocation_log
                WHERE accepted=TRUE AND broker_order_id IS NULL
                """
            )
        ).scalar(),
        "orphan_lots": session.execute(
            text(
                """
                SELECT COUNT(*) FROM broker_attribution_lots
                WHERE remaining_qty > 0 AND strategy_position_id IS NULL
                """
            )
        ).scalar(),
        "shadow_broker_flat": session.execute(
            text(
                """
                SELECT COUNT(*) FROM live_sim_positions lsp
                LEFT JOIN broker_positions bp
                  ON bp.broker_account_id = lsp.broker_account_id
                 AND bp.instrument_id = lsp.instrument_id
                WHERE lsp.status='open' AND COALESCE(bp.net_quantity,0)=0
                """
            )
        ).scalar(),
    }
    snapshot_path = BACKUP_DIR / f"snapshot_{stamp}.json"
    snapshot_path.write_text(json.dumps(snap, indent=2, default=str), encoding="utf-8")
    session.close()
    return snap


def phase5_research_recovery(store: TradingStore, *, apply: bool) -> dict:
    return recover_missed_research_crypto_exits(
        store,
        symbols={"BTCUSD", "ETHUSD"},
        dry_run=not apply,
    )


def phase6_live_sim_recovery(store: TradingStore, *, apply: bool) -> dict:
    return recover_missed_live_sim_exits(store, dry_run=not apply)


def phase7_limbo(store: TradingStore, *, apply: bool) -> dict:
    if not apply:
        count = store.session.execute(
            text(
                """
                SELECT COUNT(*) FROM live_sim_allocation_log
                WHERE accepted=TRUE AND broker_order_id IS NULL
                  AND live_sim_position_id IS NULL
                """
            )
        ).scalar()
        return {"dry_run": True, "would_reconcile": int(count or 0)}
    finalized = finalize_expired_accepted_limbo(store)
    n = reconcile_accepted_limbo_allocations(store, datetime.now(timezone.utc))
    store.session.commit()
    remaining = store.session.execute(
        text(
            """
            SELECT COUNT(*) FROM live_sim_allocation_log
            WHERE accepted=TRUE AND broker_order_id IS NULL
            """
        )
    ).scalar()
    return {"finalized_expired": finalized, "reconciled": n, "remaining": int(remaining or 0)}


def phase8_integrity(store: TradingStore, *, apply: bool) -> dict:
    import importlib.util

    path = ROOT / "scripts" / "repair_live_sim_integrity.py"
    spec = importlib.util.spec_from_file_location("repair_live_sim_integrity", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod.repair(dry_run=not apply)


def financial_reconciliation(session) -> dict:
    owner = session.execute(
        text(
            """
            SELECT COALESCE(SUM(equity),0), COALESCE(SUM(realized_pnl),0),
                   COALESCE(SUM(unrealized_pnl),0)
            FROM broker_accounts WHERE slug IN ('live-sim-ibkr-like','live-sim-kraken-like')
            """
        )
    ).one()
    from quantara_engine.owner_portfolio.aggregation import aggregate_owner_portfolio
    from quantara_engine.owner_portfolio.service import LIVE_SIM_OWNER_SLUG

    store = TradingStore(session)
    agg = aggregate_owner_portfolio(store, slug=LIVE_SIM_OWNER_SLUG)
    return {
        "broker_equity_sum": float(owner[0]),
        "owner_equity": float(agg.total_equity),
        "equity_diff": float(agg.total_equity) - float(owner[0]),
        "broker_realized_sum": float(owner[1]),
        "owner_realized": float(agg.total_realized_pnl),
        "realized_diff": float(agg.total_realized_pnl) - float(owner[1]),
        "broker_unrealized_sum": float(owner[2]),
        "owner_unrealized": float(agg.total_unrealized_pnl),
        "unrealized_diff": float(agg.total_unrealized_pnl) - float(owner[2]),
    }


def prove_crypto_1m(store: TradingStore) -> dict:
    out = {}
    for sym in ("BTCUSD", "ETHUSD"):
        inst = store.get_instrument_by_symbol(sym)
        ts = store.latest_candle_timestamp(inst.id, "1m") if inst else None
        age = None
        if ts:
            age = round((datetime.now(timezone.utc) - ts.astimezone(timezone.utc)).total_seconds() / 60, 1)
        out[sym] = {"latest_1m": ts.isoformat() if ts else None, "age_min": age}
    ws = store.get_settings_dict().get("worker_status:crypto_fast_protection") or {}
    out["crypto_worker"] = {"status": ws.get("status"), "error": ws.get("error")}
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", choices=["1", "4", "5", "6", "7", "8", "9", "all"], default="all")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--skip-backup", action="store_true")
    args = parser.parse_args()

    report: dict = {"phases": {}}

    with session_scope() as session:
        store = TradingStore(session)
        if args.phase in ("1", "all"):
            report["phases"]["1_containment"] = phase1_containment(store)
            session.commit()

        if args.phase in ("4", "all") and not args.skip_backup:
            report["phases"]["4_backup"] = phase4_backup_and_snapshot()

        if args.phase in ("5", "all") and args.apply:
            report["phases"]["5_research"] = phase5_research_recovery(store, apply=True)
            session.commit()

        if args.phase in ("6", "all") and args.apply:
            report["phases"]["6_live_sim"] = phase6_live_sim_recovery(store, apply=True)
            session.commit()

        if args.phase in ("7", "all") and args.apply:
            report["phases"]["7_limbo"] = phase7_limbo(store, apply=True)

        if args.phase in ("8", "all") and args.apply:
            report["phases"]["8_integrity"] = phase8_integrity(store, apply=True)
            session.commit()

        if args.phase in ("9", "all"):
            report["phases"]["9_financial"] = financial_reconciliation(session)
            report["phases"]["crypto_1m"] = prove_crypto_1m(store)

    print(json.dumps(report, indent=2, default=str))


if __name__ == "__main__":
    main()
