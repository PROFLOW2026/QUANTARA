"""Legacy Live Sim audit before equal-asset activation."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "engine"))

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from quantara_engine.owner_portfolio.asset_allocation import audit_legacy_live_sim_before_equal_asset_activation
from quantara_engine.persistence.store import TradingStore


def _load_database_url() -> str:
    for line in ROOT.joinpath(".env").read_text(encoding="utf-8").splitlines():
        if line.strip().startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip() in ("DATABASE_URL", "DIRECT_URL"):
            return value.strip().strip('"')
    raise RuntimeError("DATABASE_URL not found")


def main() -> None:
    engine = create_engine(_load_database_url())
    session = sessionmaker(bind=engine)()
    store = TradingStore(session)
    audit = audit_legacy_live_sim_before_equal_asset_activation(store)
    detail = session.execute(
        text(
            """
            SELECT ba.slug,
                   (SELECT COUNT(*) FROM live_sim_positions p
                    WHERE p.broker_account_id = ba.id AND p.status = 'open') AS open_positions,
                   (SELECT COUNT(*) FROM broker_orders o
                    WHERE o.broker_account_id = ba.id) AS orders,
                   (SELECT COUNT(*) FROM broker_fills f
                    JOIN broker_orders o ON o.id = f.broker_order_id
                    WHERE o.broker_account_id = ba.id) AS fills,
                   ba.realized_pnl, ba.unrealized_pnl, ba.equity
            FROM broker_accounts ba
            WHERE ba.slug = 'live-sim-10k'
            """
        )
    ).mappings().first()
    print(json.dumps({"audit": audit, "legacy_detail": dict(detail) if detail else None}, default=str))


if __name__ == "__main__":
    main()
