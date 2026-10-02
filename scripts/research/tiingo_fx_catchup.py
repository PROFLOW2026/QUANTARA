#!/usr/bin/env python3
"""Incremental Tiingo 5m catch-up for EURUSD/USDJPY only (no US equity spend)."""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps" / "engine"))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from quantara_engine.market_data.registry import get_asset
from quantara_engine.persistence.store import TradingStore
from quantara_engine.market_data.provider_budgets import can_request_tiingo_candle

FX_ONLY = ("EURUSD", "USDJPY")


def db_url() -> str:
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if line.startswith("DATABASE_URL="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise RuntimeError("DATABASE_URL missing")


def main() -> None:
    from quantara_engine.market_data.tiingo_fallback_scheduler import build_tiingo_fallback_plan
    from quantara_workers.jobs.fetch_data import _fetch_asset_live

    session = sessionmaker(bind=create_engine(db_url(), pool_pre_ping=True))()
    store = TradingStore(session)
    now = datetime.now(timezone.utc)
    plan = build_tiingo_fallback_plan(store, now)
    results = {}
    for sym in FX_ONLY:
        if not can_request_tiingo_candle(store):
            results[sym] = {"status": "budget_exhausted"}
            break
        asset = get_asset(sym)
        inst = store.get_instrument_by_symbol(sym)
        if not inst:
            results[sym] = {"status": "no_instrument"}
            continue
        try:
            count, derived, error, _provider = _fetch_asset_live(
                store, inst, asset, now, {}, plan=plan
            )
            store.session.commit()
            latest = store.latest_candle_timestamp(inst.id, "5m")
            results[sym] = {
                "status": "ok" if not error else "error",
                "bars_5m": count,
                "derived": derived,
                "error": error,
                "latest_5m": latest.isoformat() if latest else None,
            }
        except Exception as exc:
            store.session.rollback()
            results[sym] = {"status": "error", "error": str(exc)}
    session.close()
    print(results)


if __name__ == "__main__":
    main()
