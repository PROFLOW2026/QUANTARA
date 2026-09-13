"""Post-fix multi-broker execution parity verification."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "engine"))

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from quantara_engine.broker.execution_model import ExecutionModelVersion
from quantara_engine.broker.fees import load_fee_profile
from quantara_engine.broker.reconciliation_orchestrator import is_broker_execution_allowed
from quantara_engine.broker.routing import route_to_broker
from quantara_engine.persistence.store import TradingStore


def _load_database_url() -> str:
    for line in ROOT.joinpath(".env").read_text(encoding="utf-8").splitlines():
        if line.strip().startswith("DATABASE_URL="):
            return line.split("=", 1)[1].strip().strip('"')
    raise RuntimeError("DATABASE_URL not found")


def main() -> None:
    session = sessionmaker(bind=create_engine(_load_database_url()))()
    store = TradingStore(session)
    chains = []
    for sym, direction in (
        ("BTCUSD", "long"),
        ("BTCUSD", "short"),
        ("ETHUSD", "long"),
        ("ETHUSD", "short"),
        ("NVDA", "long"),
        ("NVDA", "short"),
        ("GBPJPY", "long"),
        ("XAUUSD", "long"),
    ):
        route = route_to_broker(
            store,
            symbol=sym,
            direction=direction,
            execution_model=ExecutionModelVersion.REALISTIC_BROKER_V1,
            owner_portfolio_slug="live-sim-owner",
        )
        fee = load_fee_profile(
            store,
            broker_vendor=route.broker_vendor,
            execution_product=route.execution_product,
        )
        chains.append(
            {
                "symbol": sym,
                "direction": direction,
                "execution_product": route.execution_product.value,
                "broker_account_slug": route.broker_account_slug,
                "broker_account_id": route.broker_account_id,
                "fee_taker": str(fee.taker_rate) if fee else None,
                "fee_maker": str(fee.maker_rate) if fee else None,
            }
        )

    models = {
        r["slug"]: r["execution_model"]
        for r in session.execute(
            text(
                """
                SELECT slug, execution_model::text AS execution_model
                FROM broker_accounts
                WHERE slug LIKE 'live-sim-%'
                ORDER BY slug
                """
            )
        ).mappings().all()
    }
    legacy_orders = session.execute(
        text(
            """
            SELECT COUNT(*) FROM broker_orders bo
            JOIN broker_accounts ba ON ba.id = bo.broker_account_id
            WHERE ba.slug = 'live-sim-10k'
            """
        )
    ).scalar()

    report = {
        "runtime_chains": chains,
        "execution_models": models,
        "legacy_broker_orders_total": int(legacy_orders or 0),
        "execution_allowed": {
            slug: is_broker_execution_allowed(store, slug)
            for slug in ("live-sim-10k", "live-sim-ibkr-like", "live-sim-kraken-like")
        },
    }
    print(json.dumps(report, indent=2))
    session.close()


if __name__ == "__main__":
    main()
