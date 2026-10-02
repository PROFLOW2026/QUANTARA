#!/usr/bin/env python3
"""Backfill EURUSD/USDJPY Tiingo 5m history for V3 research, then derive 15m/1h."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps" / "engine"))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from quantara_engine.market_data.adapters.tiingo import TiingoMarketDataProvider, TiingoError
from quantara_engine.market_data.provider_budgets import can_request
from quantara_engine.market_data.provider_resolver import dedupe_complete_candles
from quantara_engine.market_data.registry import get_asset
from quantara_engine.market_data.validation import validate_candle
from quantara_engine.persistence.store import TradingStore
from quantara_engine.research.v3.constants import V3_RESEARCH_START
from quantara_workers.jobs.fetch_data import _derive_full, _persist_candles_chunked


def db_url() -> str:
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if line.startswith("DATABASE_URL="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise RuntimeError("DATABASE_URL missing")


def catchup_symbol(
    store: TradingStore,
    symbol: str,
    *,
    chunk_days: int,
    dry_run: bool,
) -> dict:
    asset = get_asset(symbol)
    inst = store.get_instrument_by_symbol(symbol)
    if not inst:
        return {"symbol": symbol, "error": "no_instrument"}

    provider = TiingoMarketDataProvider(
        store=store,
        caller="fx_research_catchup:5m",
        asset=asset,
        allow_non_canonical_timeframes=True,
    )

    end = datetime.now(timezone.utc)
    cursor = V3_RESEARCH_START.replace(tzinfo=timezone.utc)
    total = 0
    chunks = 0
    errors: list[str] = []

    while cursor < end:
        if not can_request(store, "tiingo", count=1, purpose="candles"):
            errors.append("tiingo_budget_blocked")
            break
        chunk_end = min(end, cursor + timedelta(days=chunk_days))
        try:
            candles = provider.fetch_range(
                str(inst.id),
                "5m",
                cursor,
                chunk_end,
            )
        except TiingoError as exc:
            errors.append(f"{cursor.date()}:{exc}")
            break

        validated = []
        for candle in dedupe_complete_candles(candles):
            try:
                validate_candle(candle)
            except Exception as exc:
                errors.append(f"invalid:{exc}")
                continue
            validated.append(candle)

        if validated and not dry_run:
            total += _persist_candles_chunked(validated)
        elif validated:
            total += len(validated)
        chunks += 1
        cursor = chunk_end + timedelta(minutes=5)

    derived = 0
    if total and not dry_run:
        store.session.commit()
        derived = _derive_full(
            store,
            str(inst.id),
            session_mode="utc",
            timeframes=("15m", "1h"),
        )
        store.session.commit()

    return {
        "symbol": symbol,
        "chunks": chunks,
        "upserted_5m": total,
        "derived_higher": derived,
        "errors": errors[:5],
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default="EURUSD,USDJPY")
    ap.add_argument("--chunk-days", type=int, default=14)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    session = sessionmaker(bind=create_engine(db_url(), pool_pre_ping=True))()
    store = TradingStore(session)
    out = []
    for sym in [s.strip() for s in args.symbols.split(",") if s.strip()]:
        out.append(
            catchup_symbol(
                store,
                sym,
                chunk_days=args.chunk_days,
                dry_run=args.dry_run,
            )
        )
    import json

    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
