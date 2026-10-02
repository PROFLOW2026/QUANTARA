#!/usr/bin/env python3
"""Sync DB instrument quantity/tick fields from market registry + broker InstrumentSpec."""

from __future__ import annotations

import json
import sys
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "engine"))

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from quantara_engine.broker.instruments import get_instrument_spec
from quantara_engine.market_data.registry import get_asset, list_target_assets
from quantara_engine.models.instruments import Instrument as OrmInstrument
from quantara_engine.persistence.store import TradingStore


def db_url() -> str:
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if line.startswith("DATABASE_URL="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise RuntimeError("DATABASE_URL missing")


def _dec(value: str | Decimal) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value))


def sync_store(store: TradingStore) -> dict:
    report: dict = {"updated": [], "missing": [], "verified": []}
    for asset in list_target_assets():
        sym = asset.db_symbol
        row = store.session.scalar(select(OrmInstrument).where(OrmInstrument.symbol == sym))
        if not row:
            report["missing"].append(sym)
            continue
        try:
            spec = get_instrument_spec(sym)
            min_q = _dec(spec.min_quantity)
            step = _dec(spec.quantity_step)
            tick = _dec(spec.tick_size)
        except KeyError:
            min_q = _dec(asset.min_quantity)
            step = _dec(asset.quantity_step)
            tick = _dec(asset.price_tick_size)

        changed = False
        if row.min_quantity != min_q:
            row.min_quantity = min_q
            changed = True
        if row.quantity_step != step:
            row.quantity_step = step
            changed = True
        if row.price_tick_size != tick:
            row.price_tick_size = tick
            changed = True
        pip = _dec(asset.pip_size)
        if row.pip_size != pip:
            row.pip_size = pip
            changed = True
        if changed:
            report["updated"].append(
                {
                    "symbol": sym,
                    "min_quantity": str(min_q),
                    "quantity_step": str(step),
                    "price_tick_size": str(tick),
                }
            )
        else:
            report["verified"].append(sym)
    store.session.commit()
    return report


def main() -> None:
    session = sessionmaker(bind=create_engine(db_url(), pool_pre_ping=True))()
    store = TradingStore(session)
    report = sync_store(store)
    session.close()
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
