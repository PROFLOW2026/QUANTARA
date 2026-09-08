#!/usr/bin/env python3
"""Import historical XAU/USD 5m candles and derive higher timeframes locally."""

from __future__ import annotations

import argparse
import os
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
from quantara_engine.market_data.aggregation import DERIVED_FROM_5M, aggregate_from_5m
from quantara_engine.market_data.credits import FetchPriority
from quantara_engine.market_data.polling import PROVIDER_TIMEFRAME
from quantara_engine.market_data.validation import validate_candle  # noqa: E402
from quantara_engine.persistence.store import TradingStore  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Import Twelve Data historical 5m candles")
    parser.add_argument("--days", type=int, default=30, help="Lookback window in days")
    parser.add_argument("--bars", type=int, default=0, help="Bootstrap bars instead of range")
    parser.add_argument(
        "--live",
        action="store_true",
        help="Opt in to live Twelve Data API calls (consumes credits)",
    )
    args = parser.parse_args()

    if not args.live and os.getenv("QUANTARA_LIVE_PROVIDER_AUDIT") != "1":
        print("Refusing live import without --live or QUANTARA_LIVE_PROVIDER_AUDIT=1")
        return 1

    if settings.market_data_provider != "twelvedata":
        print("Set MARKET_DATA_PROVIDER=twelvedata in .env")
        return 1
    if not settings.market_data_api_key.strip():
        print("Set MARKET_DATA_API_KEY in .env")
        return 1
    if not settings.database_configured:
        print("DATABASE_URL not configured")
        return 1

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=args.days)

    with session_scope() as session:
        store = TradingStore(session)
        instrument = store.get_instrument_by_symbol("XAUUSD")
        if not instrument:
            print("XAUUSD instrument not found — run seed first")
            return 1

        provider = TwelveDataMarketDataProvider(
            store=store,
            caller="scripts/import_historical.py",
            priority=FetchPriority.AUDIT,
        )

        try:
            if args.bars > 0:
                candles = provider.fetch_bootstrap(
                    instrument.id, PROVIDER_TIMEFRAME, bars=args.bars
                )
            else:
                candles = provider.fetch_range(
                    instrument.id, PROVIDER_TIMEFRAME, start, end
                )
        except TwelveDataError as exc:
            print(f"Twelve Data error: {exc}")
            return 1

        count = 0
        for candle in candles:
            validate_candle(candle)
            store.upsert_candle(candle)
            count += 1

        derived_total = 0
        base_rows = store.list_candles(instrument.id, PROVIDER_TIMEFRAME)
        for target_tf in DERIVED_FROM_5M:
            for candle in aggregate_from_5m(base_rows, target_tf):
                validate_candle(candle)
                store.upsert_candle(candle)
                derived_total += 1

        print(
            f"Imported {count} {PROVIDER_TIMEFRAME} candles for XAU/USD "
            f"(stored total: {store.count_candles(instrument.id, PROVIDER_TIMEFRAME)})"
        )
        for tf in DERIVED_FROM_5M:
            print(f"Derived {tf} total: {store.count_candles(instrument.id, tf)}")
        print(f"Derived upserts this run: {derived_total}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
