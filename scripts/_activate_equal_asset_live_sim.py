"""Activate equal-asset Live Sim on quantara_prod via application service."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "engine"))

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from quantara_engine.owner_portfolio.asset_allocation import (
    audit_legacy_live_sim_before_equal_asset_activation,
    configure_equal_asset_allocations,
)
from quantara_engine.owner_portfolio.service import LIVE_SIM_OWNER_SLUG
from quantara_engine.persistence.store import TradingStore


def _load_database_url() -> str:
    for line in ROOT.joinpath(".env").read_text(encoding="utf-8").splitlines():
        if line.strip().startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip() in ("DATABASE_URL", "DIRECT_URL"):
            return value.strip().strip('"')
    raise RuntimeError("DATABASE_URL not found")


def main() -> int:
    engine = create_engine(_load_database_url())
    Session = sessionmaker(bind=engine)
    session = Session()
    store = TradingStore(session)

    audit = audit_legacy_live_sim_before_equal_asset_activation(store)
    print("LEGACY_AUDIT", json.dumps(audit, default=str))

    if audit.get("requires_manual_attribution_review"):
        print("BLOCKED: legacy economic state requires attribution review")
        return 1

    flags = session.execute(
        text(
            """
            SELECT multi_broker_mode_enabled, equal_asset_allocation_enabled
            FROM owner_trading_portfolios WHERE slug = :slug
            """
        ),
        {"slug": LIVE_SIM_OWNER_SLUG},
    ).mappings().first()
    print("FLAGS_BEFORE", dict(flags))

    if flags and flags["multi_broker_mode_enabled"] and flags["equal_asset_allocation_enabled"]:
        print("ALREADY_ACTIVE")
        return 0

    result = configure_equal_asset_allocations(store, LIVE_SIM_OWNER_SLUG, activate=True)
    print("ACTIVATION", json.dumps(result, default=str))
    return 0 if result.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
