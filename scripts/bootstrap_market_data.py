#!/usr/bin/env python3
"""Bootstrap minimum strategy candle history on local production DB (no Worker required)."""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "apps" / "engine"
sys.path.insert(0, str(ENGINE))

from sqlalchemy import text  # noqa: E402

from quantara_engine.core.config import settings  # noqa: E402
from quantara_engine.db.session import session_scope  # noqa: E402
from quantara_engine.market_data.active_universe import list_active_db_symbols  # noqa: E402
from quantara_engine.market_data.adapters.mock import MockMarketDataProvider  # noqa: E402
from quantara_engine.market_data.polling import STRATEGY_MIN_CANDLES, timeframe_minutes  # noqa: E402
from quantara_engine.persistence.store import TradingStore  # noqa: E402

TIMEFRAMES = ("5m", "15m", "1h")


def _bootstrap_mock(store: TradingStore, instrument_id: str, symbol: str) -> dict[str, int]:
    provider = MockMarketDataProvider()
    end = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    counts: dict[str, int] = {}

    for tf in TIMEFRAMES:
        minutes = timeframe_minutes(tf)
        bars = STRATEGY_MIN_CANDLES + 4
        start = end - timedelta(minutes=minutes * bars)
        candles = provider.generate_candles(instrument_id, tf, bars, start)
        store.upsert_candles_batch(candles)
        counts[tf] = store.count_candles(instrument_id, tf)
        if counts[tf] < STRATEGY_MIN_CANDLES:
            raise RuntimeError(
                f"{symbol} {tf}: {counts[tf]} candles < required {STRATEGY_MIN_CANDLES}"
            )
    return counts


def main() -> int:
    if not settings.database_configured:
        print("DATABASE_URL not configured.")
        return 1

    settings.validate_runtime_database()

    provider = settings.market_data_provider.strip().lower()
    if provider != "mock":
        print(
            f"MARKET_DATA_PROVIDER={provider!r} — importing worker bulk bootstrap for live providers."
        )
        sys.path.insert(0, str(ENGINE / "quantara_workers"))
        from quantara_workers.jobs.fetch_data import fetch_bulk_job  # noqa: E402

        fetch_bulk_job(force=True)
        print("Bulk fetch job completed.")
        return 0

    print(f"Mock bootstrap — {STRATEGY_MIN_CANDLES}+ candles per asset/timeframe")
    symbols = list_active_db_symbols()
    report: dict[str, dict[str, int]] = {}

    with session_scope() as session:
        store = TradingStore(session)
        for symbol in symbols:
            instrument = store.get_instrument_by_symbol(symbol)
            if not instrument:
                print(f"SKIP {symbol}: instrument missing — run seed_8_assets.py")
                return 1
            report[symbol] = _bootstrap_mock(store, instrument.id, symbol)
            print(f"  {symbol}: 5m={report[symbol]['5m']} 15m={report[symbol]['15m']} 1h={report[symbol]['1h']}")

    print("PASS  Market data bootstrap")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
