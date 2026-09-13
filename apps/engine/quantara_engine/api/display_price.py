"""Resolve dashboard display price — prefer fresher canonical 1m marks."""

from __future__ import annotations

from datetime import datetime

from quantara_engine.execution.crypto_mark_valuation import display_price_from_canonical_mark
from quantara_engine.persistence.store import TradingStore


def resolve_asset_display_price(
    store: TradingStore,
    db_symbol: str,
    *,
    fallback_price: float | None,
    fallback_candle: datetime | None,
) -> tuple[float | None, datetime | None]:
    """Return (price, as_of) — in-memory stream hub first, then persisted canonical mark."""
    from quantara_engine.market_data.streaming.live_mark_read import hub_mark_price

    live = hub_mark_price(db_symbol)
    if live:
        return float(live[0]), live[1]

    canon = display_price_from_canonical_mark(store, db_symbol)
    if canon:
        return float(canon[0]), canon[1]
    return fallback_price, fallback_candle
