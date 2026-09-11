"""Central market-data provider resolution with ordered failover."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Literal

from quantara_engine.core.config import settings
from quantara_engine.domain.types import Candle
from quantara_engine.market_data.adapters.alpaca import AlpacaError
from quantara_engine.market_data.adapters.tiingo import TiingoError
from quantara_engine.market_data.adapters.twelvedata import TwelveDataError
from quantara_engine.market_data.credits import FetchPriority, can_fetch, is_blocked as twelve_data_blocked
from quantara_engine.market_data.factory import get_market_data_provider
from quantara_engine.market_data.provider_budgets import can_request
from quantara_engine.market_data.provider_cooldown import is_in_cooldown, mark_cooldown
from quantara_engine.market_data.registry import AssetDefinition, ProviderName

logger = logging.getLogger(__name__)

ProviderFetchFn = Callable[..., list[Candle]]

# Alpaca FX endpoints return 404 on this account — excluded from FX failover chains.
_ALPACA_FX_ENABLED = False


@dataclass(frozen=True)
class FetchOutcome:
    candles: list[Candle]
    provider: ProviderName | None
    error: str | None = None


def provider_chain_for_asset(asset: AssetDefinition) -> tuple[ProviderName, ...]:
    """Ordered providers for an asset — primary then secondary."""
    chain: list[ProviderName] = [asset.primary_provider]
    if asset.secondary_provider and asset.secondary_provider not in chain:
        chain.append(asset.secondary_provider)
    return tuple(chain)


def fx_conversion_provider_chain() -> tuple[ProviderName, ...]:
    """JPY→USD refresh order (Alpaca only when FX entitlement exists)."""
    chain: list[ProviderName] = []
    if _ALPACA_FX_ENABLED and settings.alpaca_api_key_id.strip():
        chain.append(ProviderName.ALPACA)
    if settings.tiingo_api_key.strip():
        chain.append(ProviderName.TIINGO)
    if settings.market_data_api_key.strip():
        chain.append(ProviderName.TWELVE_DATA)
    return tuple(chain)


def is_provider_configured(provider: ProviderName) -> bool:
    if provider == ProviderName.TWELVE_DATA:
        return bool(settings.market_data_api_key.strip())
    if provider == ProviderName.TIINGO:
        return bool(settings.tiingo_api_key.strip())
    if provider == ProviderName.ALPACA:
        return bool(settings.alpaca_api_key_id.strip() and settings.alpaca_api_secret_key.strip())
    return False


def is_provider_eligible(
    store,
    provider: ProviderName,
    *,
    priority: FetchPriority = FetchPriority.SCHEDULED,
    purpose: Literal["candles", "fx_rate"] = "candles",
) -> bool:
    if not is_provider_configured(provider):
        return False
    if is_in_cooldown(store, provider.value):
        return False

    if provider == ProviderName.TWELVE_DATA:
        if twelve_data_blocked(store):
            return False
        if not can_fetch(store, priority):
            return False
        return True

    if provider == ProviderName.TIINGO:
        if store is not None and not can_request(store, "tiingo", purpose=purpose):
            return False
        return True

    if provider == ProviderName.ALPACA:
        if purpose == "fx_rate" and not _ALPACA_FX_ENABLED:
            return False
        if purpose == "candles" and store is not None and not can_request(store, "alpaca"):
            return False
        return True

    return False


def has_eligible_provider(
    store,
    asset: AssetDefinition,
    *,
    priority: FetchPriority = FetchPriority.SCHEDULED,
) -> bool:
    return any(
        is_provider_eligible(store, name, priority=priority)
        for name in provider_chain_for_asset(asset)
    )


def _classify_failure(exc: Exception) -> tuple[bool, str]:
    """Return (should_cooldown, message)."""
    msg = str(exc)
    code = getattr(exc, "code", None)
    lower = msg.lower()
    if code in (429, 401, 403) or "run out of api credits" in lower:
        return False, msg
    if code in (404, 422) or "not found" in lower:
        return True, msg
    if "timeout" in lower or "network error" in lower:
        return True, msg
    if isinstance(code, int) and code >= 500:
        return True, msg
    return True, msg


def _record_failure(store, provider: ProviderName, exc: Exception) -> None:
    should_cooldown, msg = _classify_failure(exc)
    if provider == ProviderName.TWELVE_DATA and (
        getattr(exc, "code", None) == 429 or "run out of api credits" in msg.lower()
    ):
        from quantara_engine.market_data.credits import mark_blocked

        mark_blocked(store, msg)
        return
    if should_cooldown:
        mark_cooldown(store, provider.value, reason=msg)


def fetch_with_failover(
    store,
    asset: AssetDefinition,
    *,
    caller: str,
    priority: FetchPriority,
    fetch_fn: ProviderFetchFn,
) -> FetchOutcome:
    """Try providers in chain; only one provider supplies each fetch batch."""
    last_error: str | None = None
    for provider_name in provider_chain_for_asset(asset):
        if not is_provider_eligible(store, provider_name, priority=priority):
            continue
        try:
            provider = get_market_data_provider(provider_name.value, asset=asset)
            if hasattr(provider, "bind_context"):
                provider.bind_context(  # type: ignore[attr-defined]
                    store=store,
                    caller=caller,
                    asset=asset,
                    priority=priority,
                )
            candles = fetch_fn(provider)
            if candles:
                logger.info(
                    "Market data fetch %s via %s (%d candles)",
                    asset.db_symbol,
                    provider_name.value,
                    len(candles),
                )
            return FetchOutcome(candles=candles, provider=provider_name)
        except (TwelveDataError, TiingoError, AlpacaError) as exc:
            last_error = f"{provider_name.value}: {exc}"
            logger.warning("Provider %s failed for %s — %s", provider_name.value, asset.db_symbol, exc)
            _record_failure(store, provider_name, exc)
        except Exception as exc:
            last_error = f"{provider_name.value}: {exc}"
            logger.exception("Unexpected provider failure for %s", asset.db_symbol)
            _record_failure(store, provider_name, exc)

    return FetchOutcome(candles=[], provider=None, error=last_error)


def dedupe_complete_candles(candles: list[Candle]) -> list[Candle]:
    """Keep one complete candle per canonical (instrument, timeframe, timestamp)."""
    by_key: dict[tuple[str, str, datetime], Candle] = {}
    for candle in candles:
        if not candle.is_complete:
            continue
        key = (candle.instrument_id, candle.timeframe, candle.timestamp)
        existing = by_key.get(key)
        if existing is None or candle.timestamp >= existing.timestamp:
            by_key[key] = candle
    return sorted(by_key.values(), key=lambda c: c.timestamp)
