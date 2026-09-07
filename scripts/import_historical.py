#!/usr/bin/env python3
"""Import historical XAU/USD candles from Twelve Data into PostgreSQL."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "apps" / "engine"
sys.path.insert(0, str(ENGINE))

from quantara_engine.core.config import settings  # noqa: E402
from quantara_engine.db.session import session_scope  # noqa: E402
from quantara_engine.market_data.adapters.twelvedata import (  # noqa: E402
    TwelveDataError,
    TwelveDataMarketDataProvider,
)
from quantara_engine.market_data.validation import validate_candle  # noqa: E402
from quantara_engine.persistence.store import TradingStore  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Import Twelve Data historical candles")
    parser.add_argument("--timeframe", default="1h", choices=("5m", "15m", "1h"))
    parser.add_argument("--days", type=int, default=30, help="Lookback window in days")
    parser.add_argument("--bars", type=int, default=0, help="Bootstrap bars instead of range")
    args = parser.parse_args()

    if settings.market_data_provider != "twelvedata":
        print("Set MARKET_DATA_PROVIDER=twelvedata in .env")
        return 1
    if not settings.market_data_api_key.strip():
        print("Set MARKET_DATA_API_KEY in .env")
        return 1
    if not settings.database_configured:
        print("DATABASE_URL not configured")
        return 1

    provider = TwelveDataMarketDataProvider()
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=args.days)

    with session_scope() as session:
        store = TradingStore(session)
        instrument = store.get_instrument_by_symbol("XAUUSD")
        if not instrument:
            print("XAUUSD instrument not found — run seed first")
            return 1

        try:
            if args.bars > 0:
                candles = provider.fetch_bootstrap(
                    instrument.id, args.timeframe, bars=args.bars
                )
            else:
                candles = provider.fetch_range(
                    instrument.id, args.timeframe, start, end
                )
        except TwelveDataError as exc:
            print(f"Twelve Data error: {exc}")
            return 1

        count = 0
        for candle in candles:
            validate_candle(candle)
            store.upsert_candle(candle)
            count += 1

        print(
            f"Imported {count} {args.timeframe} candles for XAU/USD "
            f"(stored total: {store.count_candles(instrument.id, args.timeframe)})"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
