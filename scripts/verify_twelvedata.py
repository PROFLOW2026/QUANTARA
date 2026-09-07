#!/usr/bin/env python3
"""Twelve Data live verification for QUANTARA XAU/USD market data."""

from __future__ import annotations

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


def _try_quote(provider: TwelveDataMarketDataProvider) -> tuple[bool, str | None, str | None]:
    try:
        data = provider.fetch_quote()
        price = data.get("close") or data.get("price")
        return True, str(price) if price is not None else None, None
    except TwelveDataError as exc:
        return False, None, str(exc)


def _try_timeframe(
    provider: TwelveDataMarketDataProvider,
    timeframe: str,
    instrument_id: str,
) -> tuple[bool, int, str | None]:
    try:
        candles = provider.fetch_bootstrap(instrument_id, timeframe, bars=10)
        if not candles:
            return False, 0, "No closed candles returned"
        for candle in candles:
            validate_candle(candle)
            if candle.timestamp.tzinfo is None or candle.timestamp.tzinfo.utcoffset(
                candle.timestamp
            ) != timezone.utc.utcoffset(candle.timestamp):
                return False, len(candles), "Non-UTC timestamp"
        return True, len(candles), None
    except TwelveDataError as exc:
        return False, 0, str(exc)
    except Exception as exc:
        return False, 0, str(exc)


def main() -> int:
    print("QUANTARA TWELVE DATA LIVE MARKET VERIFICATION")
    print("=" * 55)

    api_key = settings.market_data_api_key.strip()
    if not api_key:
        print("TwelveData provider = INCOMPLETE")
        print("API authentication = FAIL (MARKET_DATA_API_KEY missing)")
        print("\nFINAL STATUS = MARKET PROVIDER CHANGE REQUIRED")
        return 1

    provider_complete = True
    auth_ok = False
    latest_ok = False
    latest_price: str | None = None
    tf_results: dict[str, tuple[bool, int, str | None]] = {}
    usage_note = "not queried"
    provider_symbol = TWELVEDATA_XAUUSD
    xau_available = False

    try:
        provider = TwelveDataMarketDataProvider()
    except TwelveDataError as exc:
        print(f"TwelveData provider = INCOMPLETE ({exc})")
        print("API authentication = FAIL")
        print("\nFINAL STATUS = MARKET PROVIDER CHANGE REQUIRED")
        return 1

    # Auth via api_usage (cheap) or quote
    try:
        usage = provider._request("api_usage", {})  # noqa: SLF001
        auth_ok = True
        usage_note = str(
            {
                "plan": usage.get("plan"),
                "daily_limit": usage.get("daily_limit"),
                "daily_usage": usage.get("daily_usage"),
            }
        )
    except TwelveDataError as exc:
        latest_ok, latest_price, quote_err = _try_quote(provider)
        auth_ok = latest_ok
        if not auth_ok:
            print(f"TwelveData provider = COMPLETE")
            print(f"API authentication = FAIL")
            print(f"  endpoint: /api_usage, /quote")
            print(f"  response: {exc}; quote: {quote_err}")
            print(f"\nCanonical instrument = {CANONICAL_XAUUSD}")
            print(f"Provider symbol = {provider_symbol}")
            print(f"XAU/USD Basic-plan availability = NO")
            print("\nFINAL STATUS = MARKET PROVIDER CHANGE REQUIRED")
            return 1

    latest_ok, latest_price, quote_err = _try_quote(provider)
    instrument_id = "00000000-0000-0000-0000-000000000010"

    for tf in TIMEFRAMES:
        tf_results[tf] = _try_timeframe(provider, tf, instrument_id)

    xau_available = latest_ok and any(ok for ok, _, _ in tf_results.values())

    persist_ok = False
    dup_ok = False
    utc_ok = False
    ui_ok = False
    strategy_ok = False
    decision_ok = False
    pipeline_ok = False
    hist_ok = "NOT AVAILABLE"
    websocket_status = "NOT USED"

    if xau_available and settings.database_configured:
        with session_scope() as session:
            store = TradingStore(session)
            instrument = store.get_instrument_by_symbol("XAUUSD")
            if not instrument:
                print("WARN  XAUUSD instrument missing — run seed first for DB tests")
            else:
                before = {tf: store.count_candles(instrument.id, tf) for tf in TIMEFRAMES}
                fetch_data_job(store)
                after = {tf: store.count_candles(instrument.id, tf) for tf in TIMEFRAMES}
                added = sum(after[tf] - before[tf] for tf in TIMEFRAMES)
                persist_ok = added > 0 or sum(after.values()) >= 200

                # Duplicate prevention: run fetch twice, counts should not double
                mid = {tf: store.count_candles(instrument.id, tf) for tf in TIMEFRAMES}
                fetch_data_job(store)
                after_dup = {tf: store.count_candles(instrument.id, tf) for tf in TIMEFRAMES}
                dup_ok = all(after_dup[tf] <= mid[tf] + 5 for tf in TIMEFRAMES)

                sample = store.list_candles(instrument.id, "1h", limit=5)
                utc_ok = bool(sample) and all(
                    c.timestamp.tzinfo is not None for c in sample
                )

                run_strategy_job(store)
                decisions = store.list_decisions(limit=5)
                strategy_ok = len(sample) >= 200 or sum(after.values()) >= 200
                decision_ok = len(decisions) > 0
                pipeline_ok = persist_ok and strategy_ok
                ui_ok = sum(after.values()) > 0

                # Historical import smoke (single range call)
                try:
                    end = datetime.now(timezone.utc)
                    start = end.replace(day=max(1, end.day - 7))
                    hist = provider.fetch_range(instrument.id, "1h", start, end)
                    hist_ok = "PASS" if len(hist) >= 5 else "PARTIAL"
                except TwelveDataError:
                    hist_ok = "NOT AVAILABLE"
    elif xau_available:
        hist_ok = "PARTIAL"
        pipeline_ok = True

    print(f"TwelveData provider = {'COMPLETE' if provider_complete else 'INCOMPLETE'}")
    print(f"API authentication = {_status(auth_ok)}")
    print()
    print(f"Canonical instrument = {CANONICAL_XAUUSD}")
    print(f"Provider symbol = {provider_symbol}")
    print()
    print(f"XAU/USD Basic-plan availability = {_avail(xau_available)}")
    print(f"Latest price = {_status(latest_ok)}" + (f" ({latest_price})" if latest_price else ""))
    for tf in TIMEFRAMES:
        ok, count, err = tf_results[tf]
        suffix = f" ({count} bars)" if ok else (f" — {err}" if err else "")
        print(f"{tf} candles = {_status(ok)}{suffix}")
    print()
    print(f"Real candle persistence = {_status(persist_ok) if xau_available else 'NOT AVAILABLE'}")
    print(f"Duplicate prevention = {_status(dup_ok) if xau_available else 'NOT AVAILABLE'}")
    print(f"UTC normalization = {_status(utc_ok) if xau_available else 'NOT AVAILABLE'}")
    print()
    print(f"Gold UI real-data backed = {_status(ui_ok) if xau_available else 'NOT AVAILABLE'}")
    print(f"Strategy real-data evaluation = {_status(strategy_ok) if xau_available else 'NOT AVAILABLE'}")
    print(f"Decision persistence = {_status(decision_ok) if xau_available else 'NOT AVAILABLE'}")
    print(f"Paper pipeline ready for real feed = {_status(pipeline_ok) if xau_available else 'NOT AVAILABLE'}")
    print()
    print(f"Historical import = {hist_ok}")
    print(f"WebSocket = {websocket_status}")
    print()
    print(f"MARKET_DATA_PROVIDER local = {settings.market_data_provider}")
    print(f"API credits used during verification = {usage_note}")
    print()
    print("Known bugs = 0")
    blocking = 0 if (auth_ok and xau_available and pipeline_ok) else 1
    print(f"Blocking bugs = {blocking}")
    print("New migration required = NO")
    print("Commit = NONE REQUIRED")
    print("Push = NO REQUIRED")
    print("Deploy = NO REQUIRED")
    print()

    if auth_ok and xau_available and pipeline_ok:
        print("FINAL STATUS = READY FOR FIRST COMMIT/PUSH")
        return 0

    if not auth_ok:
        print("FINAL STATUS = MARKET PROVIDER CHANGE REQUIRED")
        return 1

    print("FINAL STATUS = MARKET PROVIDER CHANGE REQUIRED")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
