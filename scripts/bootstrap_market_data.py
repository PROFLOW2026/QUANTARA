#!/usr/bin/env python3
"""Bootstrap minimum REAL strategy candle history (no Worker, no mock on quantara_prod)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "apps" / "engine"
sys.path.insert(0, str(ENGINE))
sys.path.insert(0, str(ENGINE / "quantara_workers"))

from quantara_engine.core.config import settings  # noqa: E402
from quantara_engine.db.guardrails import (  # noqa: E402
    is_production_database,
    validate_production_market_data_provider,
)
from quantara_engine.db.session import session_scope  # noqa: E402
from quantara_engine.market_data.active_universe import list_active_db_symbols  # noqa: E402
from quantara_engine.market_data.polling import STRATEGY_MIN_CANDLES  # noqa: E402
from quantara_engine.market_data.provider_resolver import is_provider_configured  # noqa: E402
from quantara_engine.market_data.registry import ProviderName, list_target_assets  # noqa: E402
from quantara_engine.persistence.store import TradingStore  # noqa: E402
from quantara_workers.jobs.fetch_data import (  # noqa: E402
    _bootstrap_asset_isolated,
    bootstrap_coverage,
    bootstrap_is_complete,
)
from quantara_workers.jobs.fetch_data import bootstrap_missing_timeframes  # noqa: E402


def _asset_has_live_provider(asset) -> tuple[bool, str | None]:
    if is_provider_configured(asset.primary_provider):
        return True, None
    if asset.secondary_provider and is_provider_configured(asset.secondary_provider):
        return True, None
    detail_parts: list[str] = []
    if asset.primary_provider == ProviderName.TWELVE_DATA:
        detail_parts.append("MARKET_DATA_API_KEY (Twelve Data primary)")
    if asset.primary_provider == ProviderName.ALPACA:
        detail_parts.append("ALPACA_API_KEY_ID + ALPACA_API_SECRET_KEY (Alpaca primary)")
    if asset.secondary_provider == ProviderName.TIINGO:
        detail_parts.append("TIINGO_API_KEY (Tiingo fallback)")
    if asset.secondary_provider == ProviderName.ALPACA:
        detail_parts.append("ALPACA_API_KEY_ID + ALPACA_API_SECRET_KEY (Alpaca fallback)")
    return False, "; ".join(detail_parts) if detail_parts else f"{asset.primary_provider.value} unavailable"


def missing_credentials_report() -> list[str]:
    missing: list[str] = []
    for asset in list_target_assets():
        ok, detail = _asset_has_live_provider(asset)
        if not ok and detail:
            missing.append(f"{asset.db_symbol}: {detail}")
    return missing


def _format_coverage(coverage: dict[str, int]) -> str:
    return (
        f"5m={coverage.get('5m', 0)} "
        f"15m={coverage.get('15m', 0)} "
        f"1h={coverage.get('1h', 0)}"
    )


def _coverage_gaps() -> list[str]:
    gaps: list[str] = []
    with session_scope() as session:
        store = TradingStore(session)
        for symbol in list_active_db_symbols():
            inst = store.get_instrument_by_symbol(symbol)
            if not inst:
                gaps.append(f"{symbol}: instrument missing")
                continue
            missing = bootstrap_missing_timeframes(bootstrap_coverage(store, inst.id))
            for tf in missing:
                gaps.append(f"{symbol}/{tf}")
    return gaps


def main() -> int:
    if not settings.database_configured:
        print("DATABASE_URL not configured.", flush=True)
        return 1

    settings.validate_runtime_database()

    if is_production_database(settings.database_url):
        try:
            validate_production_market_data_provider(
                settings.database_url.strip(),
                settings.market_data_provider,
            )
        except RuntimeError as exc:
            print(f"FAIL  {exc}", flush=True)
            return 1

    if settings.market_data_provider.strip().lower() == "mock":
        print("FAIL  MARKET_DATA_PROVIDER=mock refused on quantara_prod.", flush=True)
        return 1

    missing = missing_credentials_report()
    if missing:
        print("STOP  Missing provider credentials (no mock fallback):", flush=True)
        for line in missing:
            print(f"  - {line}", flush=True)
        print("", flush=True)
        print("Add keys to repo root .env, then re-run: npm run db:bootstrap", flush=True)
        return 1

    print("Live bootstrap via fetch_bulk asset isolation (existing provider registry)", flush=True)
    had_errors = False

    for asset in list_target_assets():
        symbol = asset.db_symbol
        print(f"START {symbol}", flush=True)

        with session_scope() as session:
            store = TradingStore(session)
            inst = store.get_instrument_by_symbol(symbol)
            if not inst:
                print(f"  {symbol}: ERROR — instrument missing", flush=True)
                had_errors = True
                continue
            before = bootstrap_coverage(store, inst.id)
            print(f"  {symbol} existing: {_format_coverage(before)}", flush=True)

        if bootstrap_is_complete(before):
            print(f"  {symbol} final: {_format_coverage(before)} PASS (no-op)", flush=True)
            continue

        print(f"  {symbol} fetching/deriving...", flush=True)
        try:
            count, derived, error = _bootstrap_asset_isolated(asset)
        except Exception as exc:
            print(f"  {symbol}: ERROR — {exc}", flush=True)
            had_errors = True
            continue

        if error:
            print(f"  {symbol}: ERROR — {error}", flush=True)
            had_errors = True
        elif count or derived:
            print(f"  {symbol}: upserted {count} 5m, derived {derived}", flush=True)

        with session_scope() as session:
            store = TradingStore(session)
            inst = store.get_instrument_by_symbol(symbol)
            if not inst:
                continue
            after = bootstrap_coverage(store, inst.id)
            status = "PASS" if bootstrap_is_complete(after) else "FAIL"
            print(f"  {symbol} final: {_format_coverage(after)} {status}", flush=True)
            if status == "FAIL":
                had_errors = True

    gaps = _coverage_gaps()
    if gaps or had_errors:
        print("FAIL  Coverage gaps remain:", flush=True)
        for gap in gaps:
            print(f"  - {gap}", flush=True)
        return 1

    print("PASS  Real market data bootstrap", flush=True)
    print("Run: python scripts/report_market_data_coverage.py", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
