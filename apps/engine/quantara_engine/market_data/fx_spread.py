"""Optional cached Tiingo FX bid/ask for paper spread modeling."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import TYPE_CHECKING

from quantara_engine.core.config import settings
from quantara_engine.market_data.adapters.tiingo import TiingoMarketDataProvider
from quantara_engine.market_data.credits import FetchPriority
from quantara_engine.market_data.registry import AssetDefinition, ProviderName, get_asset, provider_symbol

if TYPE_CHECKING:
    from quantara_engine.persistence.store import TradingStore

logger = logging.getLogger(__name__)

QUOTE_CACHE_KEY = "fx_quote_cache"
QUOTE_TTL = timedelta(minutes=15)


@dataclass(frozen=True)
class FxQuoteSpread:
    bid: Decimal
    ask: Decimal
    spread: Decimal
    source: str
    updated_at: datetime


def _load_cache(store: TradingStore) -> dict:
    raw = store.get_settings_dict().get(QUOTE_CACHE_KEY)
    return raw if isinstance(raw, dict) else {}


def _save_cache(store: TradingStore, cache: dict) -> None:
    store.update_settings(QUOTE_CACHE_KEY, cache, description="Cached Tiingo FX bid/ask quotes")


def get_cached_fx_spread(
    store: TradingStore | None,
    db_symbol: str,
    *,
    reference_price: Decimal,
) -> FxQuoteSpread | None:
    """Return bid-ask spread when a fresh Tiingo FX quote exists; never raises."""
    if store is None or not settings.tiingo_api_key.strip():
        return None
    asset = get_asset(db_symbol)
    if asset is None or ProviderName.TIINGO.value not in asset.provider_symbols:
        return None
    if asset.asset_class.value not in ("forex", "commodity"):
        return None

    now = datetime.now(timezone.utc)
    cache = _load_cache(store)
    entry = cache.get(db_symbol)
    if isinstance(entry, dict):
        try:
            updated = datetime.fromisoformat(str(entry["updated_at"]))
            if updated.tzinfo is None:
                updated = updated.replace(tzinfo=timezone.utc)
            if now - updated < QUOTE_TTL:
                bid = Decimal(str(entry["bid"]))
                ask = Decimal(str(entry["ask"]))
                return FxQuoteSpread(
                    bid=bid,
                    ask=ask,
                    spread=(ask - bid).quantize(Decimal("0.00000001")),
                    source=str(entry.get("source") or "tiingo_cached"),
                    updated_at=updated,
                )
        except (ValueError, ArithmeticError):
            pass

    try:
        provider = TiingoMarketDataProvider(
            store=store,
            caller="fx_spread",
            priority=FetchPriority.SCHEDULED,
            asset=asset,
        )
        ticker = provider_symbol(asset, ProviderName.TIINGO)
        quote = provider.fetch_fx_top_quote(ticker)
        if not quote:
            return None
        bid = quote["bid"]
        ask = quote["ask"]
        cache[db_symbol] = {
            "bid": str(bid),
            "ask": str(ask),
            "updated_at": now.isoformat(),
            "source": "tiingo",
        }
        _save_cache(store, cache)
        return FxQuoteSpread(
            bid=bid,
            ask=ask,
            spread=(ask - bid).quantize(Decimal("0.00000001")),
            source="tiingo",
            updated_at=now,
        )
    except Exception as exc:
        logger.debug("Tiingo FX spread unavailable for %s: %s", db_symbol, exc)
        return None
