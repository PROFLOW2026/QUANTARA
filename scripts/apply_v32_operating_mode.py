#!/usr/bin/env python3
"""Apply V3.2 operating mode: 1% risk + parallel SL exposure limits (no reset, no anchor change)."""

from __future__ import annotations

import json
import sys
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "engine"))

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from quantara_engine.broker.accounts import LIVE_SIM_10K_ACCOUNT_SLUG
from quantara_engine.db.sqlalchemy_url import normalize_sqlalchemy_postgres_url
from quantara_engine.live_sim.constants import (
    DEFAULT_MAX_GROUP_SL_RISK_PCT,
    DEFAULT_MAX_SYMBOL_SL_RISK_PCT,
    DEFAULT_MAX_TOTAL_OPEN_SL_RISK_PCT,
    DEFAULT_RISK_PER_TRADE_PCT,
)
from quantara_engine.live_sim.v32_experiment import V32_INITIAL_RISK_PCT

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


def main() -> None:
    session = sessionmaker(bind=create_engine(normalize_sqlalchemy_postgres_url(db_url())))()
    updated: list[str] = []
    anchors: dict[str, str | None] = {}
    for slug in _LIVE_SIM_RISK_SLUGS:
        row = session.execute(
            text(
                """
                SELECT id::text, risk_settings, account_metadata
                FROM broker_accounts WHERE slug = :slug
                """
            ),
            {"slug": slug},
        ).mappings().first()
        if not row:
            continue
        meta = dict(row.get("account_metadata") or {})
        anchors[slug] = meta.get("v32_observation_anchor")
        risk = dict(row.get("risk_settings") or {})
        risk["risk_per_trade_pct"] = str(Decimal(str(V32_INITIAL_RISK_PCT)))
        risk["max_total_open_sl_risk_pct"] = str(DEFAULT_MAX_TOTAL_OPEN_SL_RISK_PCT)
        risk["max_symbol_sl_risk_pct"] = str(DEFAULT_MAX_SYMBOL_SL_RISK_PCT)
        risk["max_group_sl_risk_pct"] = str(DEFAULT_MAX_GROUP_SL_RISK_PCT)
        session.execute(
            text(
                """
                UPDATE broker_accounts
                SET risk_settings = CAST(:risk AS jsonb)
                WHERE id = CAST(:aid AS uuid)
                """
            ),
            {"aid": row["id"], "risk": json.dumps(risk)},
        )
        updated.append(slug)

    session.commit()
    session.close()
    print(
        json.dumps(
            {
                "ok": True,
                "accounts_updated": updated,
                "risk_per_trade_pct": float(DEFAULT_RISK_PER_TRADE_PCT),
                "observation_anchors_preserved": anchors,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
