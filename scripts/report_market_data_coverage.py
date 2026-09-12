#!/usr/bin/env python3
"""Report candle coverage and sources per active asset (production readiness)."""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "apps" / "engine"
sys.path.insert(0, str(ENGINE))

from sqlalchemy import text  # noqa: E402

from quantara_engine.core.config import settings  # noqa: E402
from quantara_engine.db.session import session_scope  # noqa: E402
from quantara_engine.market_data.active_universe import list_active_db_symbols  # noqa: E402
from quantara_engine.market_data.polling import STRATEGY_MIN_CANDLES, is_market_data_fresh  # noqa: E402
from quantara_engine.persistence.store import TradingStore  # noqa: E402

TIMEFRAMES = ("5m", "15m", "1h")


def main() -> int:
    if not settings.database_configured:
        print("DATABASE_URL not configured.")
        return 1

    now = datetime.now(timezone.utc)
    mock_total = 0
    rows: list[dict] = []

    with session_scope() as session:
        store = TradingStore(session)
        mock_total = int(
            session.execute(text("SELECT COUNT(*) FROM candles WHERE source = 'mock'")).scalar() or 0
        )

        for symbol in list_active_db_symbols():
            inst = store.get_instrument_by_symbol(symbol)
            if not inst:
                rows.append({"asset": symbol, "error": "instrument missing"})
                continue

            entry: dict = {"asset": symbol}
            for tf in TIMEFRAMES:
                count = store.count_candles(inst.id, tf)
                latest = store.latest_candle_timestamp(inst.id, tf)
                sources = session.execute(
                    text(
                        """
                        SELECT DISTINCT source FROM candles
                        WHERE instrument_id = CAST(:iid AS uuid) AND timeframe = :tf
                        ORDER BY source
                        """
                    ),
                    {"iid": inst.id, "tf": tf},
                ).scalars().all()
                entry[f"{tf}_count"] = count
                entry[f"{tf}_sources"] = ",".join(sources) if sources else "-"
                entry[f"{tf}_latest"] = latest.isoformat() if latest else "-"
                entry[f"{tf}_fresh"] = (
                    "yes" if latest and is_market_data_fresh(latest, tf, now) else "stale"
                )
            rows.append(entry)

    print("QUANTARA market data coverage")
    print("=" * 120)
    print(f"mock rows in DB: {mock_total}")
    print(f"required bars per timeframe: >={STRATEGY_MIN_CANDLES}")
    print()
    header = (
        f"{'Asset':<8} {'5m':>5} {'15m':>5} {'1h':>5} "
        f"{'Sources (5m/15m/1h)':<45} {'Latest 5m':<22} {'Fresh'}"
    )
    print(header)
    print("-" * 120)

    all_ok = mock_total == 0
    for r in rows:
        if "error" in r:
            print(f"{r['asset']:<8} ERROR: {r['error']}")
            all_ok = False
            continue
        counts_ok = all(r[f"{tf}_count"] >= STRATEGY_MIN_CANDLES for tf in TIMEFRAMES)
        sources_blob = "/".join(r[f"{tf}_sources"] for tf in TIMEFRAMES)
        has_mock = "mock" in sources_blob
        if has_mock or not counts_ok:
            all_ok = False
        print(
            f"{r['asset']:<8} {r['5m_count']:>5} {r['15m_count']:>5} {r['1h_count']:>5} "
            f"{sources_blob:<45} {r['5m_latest']:<22} {r['5m_fresh']}"
        )

    print("=" * 120)
    print("PASS" if all_ok else "FAIL")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
