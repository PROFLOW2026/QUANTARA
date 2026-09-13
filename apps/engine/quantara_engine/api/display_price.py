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
    """Return (price, as_of) for Home/market status — canonical 1m wins when stored."""
    canon = display_price_from_canonical_mark(store, db_symbol)
    if canon:
        return float(canon[0]), canon[1]
    return fallback_price, fallback_candle
