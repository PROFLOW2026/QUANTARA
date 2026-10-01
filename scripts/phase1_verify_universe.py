#!/usr/bin/env python3
"""Phase 1 — verify instruments, provider routing, and candle coverage (read-only + fetch smoke)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "engine"))

from sqlalchemy import create_engine, text

from quantara_engine.broker.instruments import get_instrument_spec
from quantara_engine.db.sqlalchemy_url import normalize_sqlalchemy_postgres_url
from quantara_engine.market_data.active_universe import PHASE1_EXPANDED_DB_SYMBOLS
from quantara_engine.market_data.registry import get_asset, list_target_assets
from quantara_engine.persistence.store import TradingStore
from quantara_engine.db.session import session_scope


def _db_url() -> str:
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if s.startswith("DATABASE_URL="):
            return s.split("=", 1)[1].strip().strip('"').strip("'")
    raise RuntimeError("DATABASE_URL missing")


def main() -> None:
    report: dict = {"symbols": {}, "errors": []}
    targets = {a.db_symbol: a for a in list_target_assets()}

    with session_scope() as session:
        store = TradingStore(session)
        for sym in PHASE1_EXPANDED_DB_SYMBOLS:
            row: dict = {"wired": False, "research_ready": False, "live_data_ready": False}
            asset = get_asset(sym)
            if not asset:
                report["errors"].append(f"{sym}: missing registry asset")
                report["symbols"][sym] = row
                continue
            try:
                spec = get_instrument_spec(sym)
            except KeyError:
                report["errors"].append(f"{sym}: missing InstrumentSpec")
                report["symbols"][sym] = row
                continue

            inst = store.get_instrument_by_symbol(sym)
            if not inst:
                report["errors"].append(f"{sym}: missing DB instrument row")
                report["symbols"][sym] = row
                continue

            row["wired"] = True
            row["provider"] = {
                "primary": asset.primary_provider.value,
                "secondary": asset.secondary_provider.value if asset.secondary_provider else None,
            }
            row["instrument_spec"] = {
                "asset_class": spec.asset_class,
                "quote_currency": spec.quote_currency,
                "tick_size": str(spec.tick_size),
                "quantity_step": str(spec.quantity_step),
                "session_key": spec.session_key,
            }

            counts: dict[str, int] = {}
            for tf in ("5m", "15m", "1h"):
                counts[tf] = store.count_candles(inst.id, tf)
            row["candle_counts"] = counts
            row["research_ready"] = counts.get("15m", 0) >= 250 and counts.get("5m", 0) >= 250

            last_5m = store.latest_candle_timestamp(inst.id, "5m")
            row["latest_5m"] = last_5m.isoformat() if last_5m else None
            row["live_data_ready"] = last_5m is not None

            report["symbols"][sym] = row

    out = ROOT / "scripts" / "research" / "phase1_universe_verify.json"
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
