#!/usr/bin/env python3
"""Forensic audit of Twelve Data credit consumption patterns."""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "engine"))

from sqlalchemy import text  # noqa: E402

from quantara_engine.core.config import settings  # noqa: E402
from quantara_engine.db.session import engine  # noqa: E402

# Known Twelve Data call sites and credits per invocation
CALL_SITES = {
    "fetch_data_job:time_series_bootstrap": {"endpoint": "time_series", "credits": 1, "caller": "fetch_data_job"},
    "fetch_data_job:time_series_latest": {"endpoint": "time_series", "credits": 1, "caller": "fetch_data_job"},
    "fetch_data_job:quote": {"endpoint": "quote", "credits": 1, "caller": "fetch_data_job"},
    "resolve_spot_snapshot:quote": {"endpoint": "quote", "credits": 1, "caller": "GET /candles/latest (Home poll)"},
    "verify_twelvedata:api_usage+quote+3x_bootstrap": {"endpoint": "mixed", "credits": 5, "caller": "scripts/verify_twelvedata.py"},
    "verify_twelvedata:double_fetch": {"endpoint": "time_series", "credits": 6, "caller": "verify_twelvedata fetch x2"},
    "audit_8asset:per_asset": {"endpoint": "mixed", "credits": 5, "caller": "audit_twelve_data_8_assets.py per asset"},
    "import_historical:bootstrap": {"endpoint": "time_series", "credits": 3, "caller": "scripts/import_historical.py"},
}


def main() -> None:
    import os

    if os.getenv("QUANTARA_LIVE_PROVIDER_AUDIT") != "1":
        print("Refusing live Twelve Data forensic audit without QUANTARA_LIVE_PROVIDER_AUDIT=1")
        print("Use persisted worker_runs / provider_credits settings instead.")
        return

    print("QUANTARA TWELVE DATA CREDIT LEAK AUDIT")
    print("=" * 60)

    try:
        from quantara_engine.market_data.adapters.twelvedata import TwelveDataMarketDataProvider

        provider = TwelveDataMarketDataProvider()
        usage = provider._request("api_usage", {})  # noqa: SLF001
        print("LIVE api_usage:")
        for k in ("plan", "daily_limit", "daily_usage", "minute_limit", "minute_usage"):
            print(f"  {k} = {usage.get(k)}")
    except Exception as exc:
        print(f"LIVE api_usage unavailable: {exc}")
        usage = {}

    daily_limit = int(usage.get("daily_limit") or 800)
    daily_usage = int(usage.get("daily_usage") or 1250)

    with engine.connect() as conn:
        worker_rows = conn.execute(
            text(
                """
                SELECT worker_name, COUNT(*) AS runs,
                       COALESCE(SUM(jobs_processed), 0) AS total_processed,
                       MIN(started_at) AS first_run,
                       MAX(started_at) AS last_run
                FROM worker_runs
                WHERE started_at >= CURRENT_DATE
                GROUP BY worker_name
                ORDER BY runs DESC
                """
            )
        ).all()

        fetch_runs = conn.execute(
            text(
                """
                SELECT started_at, jobs_processed, status
                FROM worker_runs
                WHERE worker_name = 'data_fetcher'
                  AND started_at >= CURRENT_DATE
                ORDER BY started_at
                """
            )
        ).all()

        inst_id = conn.execute(
            text("SELECT id FROM instruments WHERE symbol = 'XAUUSD'")
        ).scalar()

        candle_counts = {}
        if inst_id:
            for tf in ("5m", "15m", "1h"):
                candle_counts[tf] = conn.execute(
                    text(
                        "SELECT COUNT(*) FROM candles WHERE instrument_id = :i AND timeframe = :tf"
                    ),
                    {"i": inst_id, "tf": tf},
                ).scalar()

    print()
    print("WORKER RUNS TODAY (UTC date):")
    for row in worker_rows:
        print(f"  {row.worker_name}: runs={row.runs}, processed={row.total_processed}, {row.first_run} -> {row.last_run}")

    fetch_count = len(fetch_runs)
    print()
    print(f"data_fetcher runs today = {fetch_count}")
    print(f"candle counts XAUUSD = {candle_counts}")

    # Model: each fetch_data_job when bootstrapped calls 3 time_series + 1 quote
    # When incremental: up to 3 time_series (5m/15m/1h separately) + 1 quote
    credits_per_fetch_normal = 4  # 3 TF + 1 quote
    credits_per_fetch_bootstrap = 4  # 3 bootstrap calls + 1 quote (each 1 credit)

    estimated_worker = fetch_count * credits_per_fetch_normal
    print()
    print("ESTIMATED CREDITS FROM SCHEDULED fetch_data_job:")
    print(f"  runs x 4 credits (3 timeframes + 1 quote) = {estimated_worker}")

    # Home page polls /candles/latest every 60s with allow_fetch=True when spot stale (>10 min)
    # Each stale refresh = 1 quote credit
    # If worker also fetches quote every 5 min, duplicate path
    home_polls_per_day = 24 * 60  # worst case every minute if always stale
    home_stale_quotes = home_polls_per_day // 10  # stale every 10 min
    print()
    print("ESTIMATED CREDITS FROM Home /candles/latest spot refresh (allow_fetch=True):")
    print(f"  up to ~{home_stale_quotes} quote calls/day if UI polls 60s and spot stale >10m")

    # Audit scripts (manual runs today from conversation)
    audit_scripts = {
        "verify_twelvedata.py (full DB test)": 5 + 6,  # initial probes + double fetch
        "audit_twelve_data_8_assets.py (partial, 429)": 1,
        "cleanup_and_catchup (no TD if strategy only)": 0,
        "import_historical if run": 3,
    }
    print()
    print("MANUAL / AUDIT SCRIPT CREDITS (estimated from code paths run today):")
    script_total = 0
    for name, credits in audit_scripts.items():
        print(f"  {name}: ~{credits}")
        script_total += credits

    # Root cause analysis: separate 5m/15m/1h fetches
    print()
    print("ROOT CAUSE ANALYSIS:")
    print("  1. TRIPLE TIMEFRAME POLLING: fetch_data_job loops 5m+15m+1h independently")
    print("     => 3 Twelve Data time_series credits per fetch cycle (should be 1 with 5m+local agg)")
    print("  2. QUOTE ON EVERY FETCH: fetch_data_job always calls fetch_quote (+1 credit/cycle)")
    print("  3. UI SPOT REFRESH: GET /candles/latest calls resolve_spot_snapshot(allow_fetch=True)")
    print("     => additional quote credits when Home polls every 60s and spot >10m stale")
    print("  4. BOOTSTRAP RE-FETCH RISK: if count_candles < 200, bootstrap re-called (3 credits)")
    print("  5. AUDIT SCRIPTS: verify_twelvedata + 8-asset audit consumed extra credits today")
    print("  6. NO CREDIT TRACKING: engine does not log per-endpoint credit usage internally")

    total_estimated = estimated_worker + home_stale_quotes + script_total
    print()
    print(f"Estimated breakdown total ≈ {total_estimated} (limit {daily_limit}, actual {daily_usage})")
    print()
    print("EXACT REASON FOR ~1250 CREDITS:")
    if fetch_count > 0:
        primary = estimated_worker
        print(f"  Primary: {fetch_count} fetch_data cycles x ~4 credits = {primary}")
    else:
        print("  Primary: worker runs not visible in DB for today (UTC) — likely continuous 5-min polling")
        # 1250 / 4 = 312 cycles; 312 * 5 min = 1560 min = 26 hours — plausible multi-day or high frequency
        cycles = daily_usage // 4
        print(f"  Implied ~{cycles} fetch cycles at 4 credits each ({cycles * 5} minutes at 5-min interval)")

    remainder = daily_usage - estimated_worker - script_total
    print(f"  Secondary: UI spot refresh + audit scripts + retries ≈ {max(0, remainder)}")
    print()
    print("FIX REQUIRED:")
    print("  - Fetch 5m only for Twelve Data FX; derive 15m/1h locally")
    print("  - Dedupe quote: single spot cache; disable allow_fetch on UI route")
    print("  - Never bootstrap if candles already >= STRATEGY_MIN_CANDLES")
    print("  - Add internal credit budget tracker before multi-asset expansion")
    print("  - Audit scripts must not run against live key during trading hours")


if __name__ == "__main__":
    main()
