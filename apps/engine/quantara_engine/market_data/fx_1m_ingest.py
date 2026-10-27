"""Scheduled FX / XAU 1m ingestion with Tiingo primary and Twelve Data fallback."""

from __future__ import annotations

import logging
from datetime import datetime

from quantara_engine.domain.types import Candle
from quantara_engine.market_data.adapters.tiingo import TiingoError, TiingoMarketDataProvider
from quantara_engine.market_data.adapters.twelvedata import TwelveDataError, TwelveDataMarketDataProvider
from quantara_engine.market_data.credits import FetchPriority
from quantara_engine.market_data.polling import FAST_PROTECTION_TIMEFRAME
from quantara_engine.market_data.provider_resolver import (
    ProviderName,
    is_provider_eligible,
)
from quantara_engine.market_data.registry import AssetDefinition
from quantara_engine.persistence.store import TradingStore

logger = logging.getLogger(__name__)


def _twelve_data_priority(*, stale_canonical: bool) -> FetchPriority:
    """Use catch-up credits when canonical 5m is stale and Tiingo cannot refresh 1m."""
    return FetchPriority.CATCH_UP if stale_canonical else FetchPriority.SCHEDULED


def fetch_tiingo_fx_1m(
    store: TradingStore,
    asset: AssetDefinition,
    instrument_id: str,
    *,
    since: datetime | None,
    caller: str,
) -> list[Candle]:
    if not is_provider_eligible(
        store,
        ProviderName.TIINGO,
        priority=FetchPriority.SCHEDULED,
        purpose="fx_rate",
    ):
        return []
    provider = TiingoMarketDataProvider(
        store=store,
        caller=caller,
        priority=FetchPriority.SCHEDULED,
        asset=asset,
        allow_non_canonical_timeframes=True,
    )
    try:
        return provider.fetch_latest(instrument_id, FAST_PROTECTION_TIMEFRAME, since=since)
    except TiingoError as exc:
        logger.warning("Tiingo FX 1m fetch failed for %s: %s", asset.db_symbol, exc)
        return []


def fetch_twelve_data_fx_1m_fallback(
    store: TradingStore,
    asset: AssetDefinition,
    instrument_id: str,
    *,
    since: datetime | None,
    caller: str,
    stale_canonical: bool,
) -> list[Candle]:
    priority = _twelve_data_priority(stale_canonical=stale_canonical)
    if not is_provider_eligible(
        store,
        ProviderName.TWELVE_DATA,
        priority=priority,
        purpose="candles",
    ):
        return []
    provider = TwelveDataMarketDataProvider(
        store=store,
        caller=caller,
        priority=priority,
        allow_non_canonical_timeframes=True,
        asset=asset,
    )
    try:
        return provider.fetch_latest(instrument_id, FAST_PROTECTION_TIMEFRAME, since=since)
    except TwelveDataError as exc:
        logger.warning("Twelve Data FX 1m fallback failed for %s: %s", asset.db_symbol, exc)
        return []
