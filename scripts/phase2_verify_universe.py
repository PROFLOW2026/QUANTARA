#!/usr/bin/env python3
"""Phase 2 — verify data gate for new symbols (read-only)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "engine"))

from quantara_engine.broker.instruments import get_instrument_spec
from quantara_engine.db.session import session_scope
from quantara_engine.market_data.active_universe import PHASE2_EXPANDED_DB_SYMBOLS
from quantara_engine.market_data.registry import get_asset
from quantara_engine.persistence.store import TradingStore

OUT = ROOT / "scripts" / "research" / "phase2_universe_verify.json"


def main() -> None:
    report: dict = {"symbols": {}, "passed": [], "failed": [], "xrp_included": "XRPUSD" in PHASE2_EXPANDED_DB_SYMBOLS}
    with session_scope() as session:
        store = TradingStore(session)
        for sym in PHASE2_EXPANDED_DB_SYMBOLS:
            row: dict = {"wired": False, "research_ready": False, "live_data_ready": False}
            asset = get_asset(sym)
            if not asset:
                report["failed"].append(sym)
                report["symbols"][sym] = {**row, "error": "missing registry"}
                continue
            try:
                spec = get_instrument_spec(sym)
            except KeyError:
                report["failed"].append(sym)
                report["symbols"][sym] = {**row, "error": "missing InstrumentSpec"}
                continue
            inst = store.get_instrument_by_symbol(sym)
            if not inst:
                report["failed"].append(sym)
                report["symbols"][sym] = {**row, "error": "missing DB instrument"}
                continue
            row["wired"] = True
            row["provider"] = asset.primary_provider.value
            row["instrument_spec"] = {
                "asset_class": spec.asset_class,
                "quote_currency": spec.quote_currency,
                "session_key": spec.session_key,
            }
            counts = {tf: store.count_candles(inst.id, tf) for tf in ("5m", "15m", "1h")}
            row["candle_counts"] = counts
            row["research_ready"] = counts.get("15m", 0) >= 250 and counts.get("5m", 0) >= 250
            last_5m = store.latest_candle_timestamp(inst.id, "5m")
            row["latest_5m"] = last_5m.isoformat() if last_5m else None
            if last_5m:
                age_h = (
                    __import__("datetime").datetime.now(__import__("datetime").timezone.utc)
                    - last_5m.replace(tzinfo=__import__("datetime").timezone.utc)
                ).total_seconds() / 3600
                row["live_data_ready"] = age_h <= 48
            else:
                row["live_data_ready"] = False
            report["symbols"][sym] = row
            if row["research_ready"] and row["live_data_ready"]:
                report["passed"].append(sym)
            else:
                report["failed"].append(sym)
    OUT.write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, default=str))


if __name__ == "__main__":
    main()
