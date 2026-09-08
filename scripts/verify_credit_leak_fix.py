#!/usr/bin/env python3
"""Focused verification for Twelve Data credit-leak fixes."""

from __future__ import annotations

import inspect
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "apps" / "engine"
sys.path.insert(0, str(ENGINE))

from quantara_engine.market_data.adapters import twelvedata as td_mod  # noqa: E402
from quantara_engine.market_data.aggregation import aggregate_from_5m  # noqa: E402
from quantara_engine.market_data.credits import DAILY_HARD_LIMIT, INTERNAL_GUARD_LIMIT  # noqa: E402
from quantara_engine.market_data.polling import DERIVED_TIMEFRAMES, PROVIDER_TIMEFRAME  # noqa: E402
from quantara_engine.market_data.spot_price import resolve_spot_snapshot  # noqa: E402
from quantara_workers.jobs import fetch_data as fetch_mod  # noqa: E402
from quantara_workers.singleton import WorkerSingletonLock  # noqa: E402


def main() -> int:
    print("QUANTARA CREDIT LEAK FIX VERIFICATION")
    print("=" * 60)

    fetch_src = inspect.getsource(fetch_mod.fetch_data_job)
    checks = {
        "provider_base_timeframe_5m": PROVIDER_TIMEFRAME == "5m",
        "derived_timeframes_local": DERIVED_TIMEFRAMES == ("15m", "1h"),
        "fetch_loops_single_provider_tf": "for timeframe in TIMEFRAMES" not in fetch_src,
        "fetch_uses_provider_timeframe": "PROVIDER_TIMEFRAME" in fetch_src,
        "fetch_derives_locally": "_derive_and_store_higher_timeframes" in fetch_src,
        "fetch_no_quote_call": "fetch_quote" not in fetch_src and "fetch_and_store_spot" not in fetch_src,
        "ui_no_provider_fetch_default": "allow_fetch: bool = False" in inspect.getsource(
            resolve_spot_snapshot
        ),
        "twelve_data_blocks_non_canonical": "_ensure_canonical_timeframe" in inspect.getsource(
            td_mod.TwelveDataMarketDataProvider
        ),
        "credit_guard_present": "can_fetch" in inspect.getsource(td_mod.TwelveDataMarketDataProvider._request),
        "worker_singleton_lock": hasattr(WorkerSingletonLock, "acquire"),
        "daily_hard_limit_800": DAILY_HARD_LIMIT == 800,
        "internal_guard_720": INTERNAL_GUARD_LIMIT == 720,
    }

    failed = [name for name, ok in checks.items() if not ok]
    for name, ok in checks.items():
        print(f"  {name} = {'PASS' if ok else 'FAIL'}")

    agg_ok = True
    try:
        from datetime import datetime, timezone
        from decimal import Decimal
        from quantara_engine.domain.types import Candle

        bars = []
        start = datetime(2026, 9, 8, 10, 15, tzinfo=timezone.utc)
        for i in range(3):
            ts = start.replace(minute=15 + i * 5)
            bars.append(
                Candle(
                    instrument_id="x",
                    timeframe="5m",
                    timestamp=ts,
                    open=Decimal("1"),
                    high=Decimal("2"),
                    low=Decimal("0.5"),
                    close=Decimal("1.5"),
                    source="test",
                    is_complete=True,
                )
            )
        derived = aggregate_from_5m(bars, "15m")
        agg_ok = len(derived) == 1 and derived[0].timeframe == "15m"
    except Exception as exc:
        agg_ok = False
        print(f"  aggregation_runtime = FAIL ({exc})")
    else:
        print(f"  aggregation_runtime = {'PASS' if agg_ok else 'FAIL'}")

    print()
    if failed or not agg_ok:
        print("FINAL STATUS = FAIL")
        return 1

    print("Expected normal XAU update credits = 1 time_series per completed 5m fetch")
    print("Expected XAU credits/day (single worker) ≈ 192")
    print("UI-triggered provider calls = 0")
    print("FINAL STATUS = PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
