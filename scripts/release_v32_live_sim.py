#!/usr/bin/env python3
"""Release Live Sim containment for V3.2 P2 portfolio (virtual Live Sim @ 0.25%)."""

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

from quantara_engine.broker.accounts import LIVE_SIM_10K_ACCOUNT_SLUG
from quantara_engine.db.sqlalchemy_url import normalize_sqlalchemy_postgres_url
from quantara_engine.live_sim.integrity_containment import release_live_sim_entry_containment
from quantara_engine.live_sim.v32_experiment import V32_EXPERIMENT_ID, V32_INITIAL_RISK_PCT
from quantara_engine.persistence.store import TradingStore

REPORT = ROOT / "scripts" / "research" / "v3_2_portfolio_p2_p3_p4_report.json"
MANIFEST = ROOT / "scripts" / "research" / "v3_2_live_sim_manifest.json"

_LIVE_SIM_RISK_SLUGS = (
    LIVE_SIM_10K_ACCOUNT_SLUG,
    "live-sim-ibkr-like",
    "live-sim-kraken-like",
)


def db_url() -> str:
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if line.startswith("DATABASE_URL="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise RuntimeError("DATABASE_URL missing")


def _stamp_v32_observation_anchor(store: TradingStore, *, anchor: datetime) -> dict:
    anchor_iso = anchor.isoformat()
    updated: list[str] = []
    for slug in _LIVE_SIM_RISK_SLUGS:
        row = store.session.execute(
            text(
                """
                SELECT id::text, account_metadata, risk_settings
                FROM broker_accounts
                WHERE slug = :slug
                """
            ),
            {"slug": slug},
        ).mappings().first()
        if not row:
            continue
        meta = dict(row.get("account_metadata") or {})
        meta["baseline_reset_at"] = anchor_iso
        meta["v32_observation_anchor"] = anchor_iso
        meta["live_sim_experiment"] = V32_EXPERIMENT_ID
        risk = dict(row.get("risk_settings") or {})
        risk["risk_per_trade_pct"] = str(Decimal(str(V32_INITIAL_RISK_PCT)))
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
        updated.append(slug)
    if LIVE_SIM_10K_ACCOUNT_SLUG not in updated:
        raise RuntimeError(
            f"live sim execution account missing: {LIVE_SIM_10K_ACCOUNT_SLUG}"
        )
    return {
        "accounts_updated": updated,
        "observation_anchor": anchor_iso,
        "risk_per_trade_pct": V32_INITIAL_RISK_PCT,
    }


def main() -> None:
    if not REPORT.is_file():
        raise SystemExit(f"missing report: {REPORT}")
    if not MANIFEST.is_file():
        raise SystemExit(f"missing manifest: {MANIFEST}")
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    report = json.loads(REPORT.read_text(encoding="utf-8"))
    if not report.get("live_sim_release_gate"):
        raise SystemExit("V3.2 Live Sim gate failed — not releasing containment")
    if manifest.get("strategy_slug") != "v32-p2-live-sim":
        raise SystemExit("manifest strategy_slug must be v32-p2-live-sim")

    anchor = datetime.now(timezone.utc)
    session = sessionmaker(bind=create_engine(normalize_sqlalchemy_postgres_url(db_url())))()
    store = TradingStore(session)
    anchor_payload = _stamp_v32_observation_anchor(store, anchor=anchor)
    payload = release_live_sim_entry_containment(store)
    session.commit()
    session.close()
    print(
        json.dumps(
            {
                "ok": True,
                "containment": payload,
                "observation": anchor_payload,
                "selected_portfolio": report.get("selected_portfolio"),
                "strategies": report.get("selected_strategies"),
                "manifest_combinations": [c.get("key") for c in manifest.get("combinations", [])],
                "initial_risk_pct": V32_INITIAL_RISK_PCT,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
