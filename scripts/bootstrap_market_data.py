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
from quantara_workers.jobs.fetch_data import _bootstrap_asset_isolated  # noqa: E402


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


def _coverage_gaps() -> list[str]:
    gaps: list[str] = []
    with session_scope() as session:
        store = TradingStore(session)
        for symbol in list_active_db_symbols():
            inst = store.get_instrument_by_symbol(symbol)
            if not inst:
                gaps.append(f"{symbol}: instrument missing")
                continue
            for tf in ("5m", "15m", "1h"):
                if store.count_candles(inst.id, tf) < STRATEGY_MIN_CANDLES:
                    gaps.append(f"{symbol}/{tf}")
    return gaps


def main() -> int:
    if not settings.database_configured:
        print("DATABASE_URL not configured.")
        return 1

    settings.validate_runtime_database()

    if is_production_database(settings.database_url):
        try:
            validate_production_market_data_provider(
                settings.database_url.strip(),
                settings.market_data_provider,
            )
        except RuntimeError as exc:
            print(f"FAIL  {exc}")
            return 1

    if settings.market_data_provider.strip().lower() == "mock":
        print("FAIL  MARKET_DATA_PROVIDER=mock refused on quantara_prod.")
        return 1

    missing = missing_credentials_report()
    if missing:
        print("STOP  Missing provider credentials (no mock fallback):")
        for line in missing:
            print(f"  - {line}")
        print("")
        print("Add keys to repo root .env, then re-run: npm run db:bootstrap")
        return 1

    print("Live bootstrap via fetch_bulk asset isolation (existing provider registry)")
    for asset in list_target_assets():
        count, derived, error = _bootstrap_asset_isolated(asset)
        if error:
            print(f"  {asset.db_symbol}: ERROR — {error}")
        elif count or derived:
            print(f"  {asset.db_symbol}: upserted {count} 5m, derived {derived}")
        else:
            print(f"  {asset.db_symbol}: already sufficient or deferred")

    gaps = _coverage_gaps()
    if gaps:
        print("FAIL  Coverage gaps remain:")
        for gap in gaps:
            print(f"  - {gap}")
        return 1

    print("PASS  Real market data bootstrap")
    print("Run: python scripts/report_market_data_coverage.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
