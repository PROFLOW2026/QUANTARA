#!/usr/bin/env python3
"""Twelve Data verification for QUANTARA XAU/USD market data."""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
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
from quantara_engine.market_data.aggregation import DERIVED_FROM_5M
from quantara_engine.market_data.credits import FetchPriority, status_payload
from quantara_engine.market_data.polling import PROVIDER_TIMEFRAME
from quantara_engine.market_data.symbols import CANONICAL_XAUUSD, TWELVEDATA_XAUUSD
from quantara_engine.market_data.validation import validate_candle  # noqa: E402
from quantara_engine.persistence.store import TradingStore  # noqa: E402
from quantara_workers.jobs.fetch_data import fetch_data_job  # noqa: E402
from quantara_workers.jobs.run_strategy import run_strategy_job  # noqa: E402

TIMEFRAMES = ("5m", "15m", "1h")


def _status(ok: bool) -> str:
    return "PASS" if ok else "FAIL"


def _avail(ok: bool) -> str:
    return "YES" if ok else "NO"


def _live_enabled(args: argparse.Namespace) -> bool:
    return args.live or os.getenv("QUANTARA_LIVE_PROVIDER_AUDIT") == "1"


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify Twelve Data integration")
    parser.add_argument(
        "--live",
        action="store_true",
        help="Opt in to live Twelve Data API probes (consumes credits)",
    )
    args = parser.parse_args()

    print("QUANTARA TWELVE DATA MARKET VERIFICATION")
    print("=" * 55)
    print(f"Live provider probes = {'ENABLED' if _live_enabled(args) else 'DISABLED (DB-only)'}")
    print()

    if not settings.database_configured:
        print("DATABASE_URL not configured")
        return 1

    with session_scope() as session:
        store = TradingStore(session)
        instrument = store.get_instrument_by_symbol("XAUUSD")
        if not instrument:
            print("XAUUSD instrument missing — run seed first")
            return 1

        counts = {tf: store.count_candles(instrument.id, tf) for tf in TIMEFRAMES}
        print("Persisted candle counts:", counts)
        print("Credit status:", status_payload(store))
        print()

        if not _live_enabled(args):
            print("Live API probes skipped to protect daily credits.")
            print("Re-run with --live or QUANTARA_LIVE_PROVIDER_AUDIT=1 for provider probes.")
            fetch_data_job(store)
            after = {tf: store.count_candles(instrument.id, tf) for tf in TIMEFRAMES}
            print("After local fetch_data_job:", after)
            run_strategy_job(store)
            print(f"Strategy decisions available = {len(store.list_decisions(limit=3)) > 0}")
            print("\nFINAL STATUS = DB PIPELINE CHECK COMPLETE (NO LIVE CREDITS USED)")
            return 0

        api_key = settings.market_data_api_key.strip()
        if not api_key:
            print("MARKET_DATA_API_KEY missing")
            return 1

        provider = TwelveDataMarketDataProvider(
            store=store,
            caller="scripts/verify_twelvedata.py",
            priority=FetchPriority.AUDIT,
        )
        try:
            usage = provider.fetch_api_usage()
            print("api_usage:", usage)
        except TwelveDataError as exc:
            print(f"api_usage failed: {exc}")
            return 1

        try:
            candles = provider.fetch_bootstrap(instrument.id, PROVIDER_TIMEFRAME, bars=5)
            for candle in candles:
                validate_candle(candle)
            print(f"{PROVIDER_TIMEFRAME} bootstrap = {_status(bool(candles))} ({len(candles)} bars)")
        except TwelveDataError as exc:
            print(f"{PROVIDER_TIMEFRAME} bootstrap = FAIL ({exc})")
            return 1

        for tf in ("15m", "1h"):
            try:
                provider.fetch_bootstrap(instrument.id, tf, bars=5)
                print(f"{tf} direct provider fetch = FAIL (should be blocked)")
                return 1
            except TwelveDataError:
                print(f"{tf} direct provider fetch blocked = PASS")

        before = {tf: store.count_candles(instrument.id, tf) for tf in TIMEFRAMES}
        fetch_data_job(store)
        after = {tf: store.count_candles(instrument.id, tf) for tf in TIMEFRAMES}
        print("Counts after fetch_data_job:", after)
        derived_ok = all(after[tf] >= before[tf] for tf in DERIVED_FROM_5M)
        print(f"Derived timeframe persistence = {_status(derived_ok)}")
        print(f"Canonical instrument = {CANONICAL_XAUUSD}")
        print(f"Provider symbol = {TWELVEDATA_XAUUSD}")
        print("Credit status:", status_payload(store))
        print("\nFINAL STATUS = LIVE VERIFICATION COMPLETE")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
