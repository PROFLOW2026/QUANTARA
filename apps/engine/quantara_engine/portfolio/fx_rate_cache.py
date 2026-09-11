"""Canonical hourly JPY→USD FX rate cache (paper trading).

All consumers must use ``get_canonical_jpy_per_usd`` / ``TradingStore.resolve_jpy_per_usd``.
External provider calls happen only inside ``_refresh_jpy_per_usd`` when the cache is stale.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from sqlalchemy import text

if TYPE_CHECKING:
    from quantara_engine.persistence.store import TradingStore

logger = logging.getLogger(__name__)

CACHE_SETTINGS_KEY = "fx_rate_cache:jpy_usd"
REFRESH_INTERVAL = timedelta(hours=1)
LOCK_TTL = timedelta(minutes=2)
DEFAULT_JPY_PER_USD = Decimal("150")
FX_REFRESH_ADVISORY_LOCK_ID = 84_729_101

_process_refresh_lock = threading.Lock()


@dataclass(frozen=True)
class CachedFxRate:
    rate: Decimal
    updated_at: datetime
    source: str


def _parse_cache(raw: Any) -> CachedFxRate | None:
    if not isinstance(raw, dict):
        return None
    rate_raw = raw.get("rate")
    updated_raw = raw.get("updated_at")
    source = raw.get("source")
    if rate_raw is None or not updated_raw or not source:
        return None
    try:
        rate = Decimal(str(rate_raw))
        if rate <= 0:
            return None
        updated_at = datetime.fromisoformat(str(updated_raw))
        if updated_at.tzinfo is None:
            updated_at = updated_at.replace(tzinfo=timezone.utc)
        return CachedFxRate(rate=rate, updated_at=updated_at, source=str(source))
    except (ValueError, ArithmeticError):
        return None


def _serialize_cache(entry: CachedFxRate) -> dict[str, str]:
    return {
        "rate": str(entry.rate),
        "updated_at": entry.updated_at.astimezone(timezone.utc).isoformat(),
        "source": entry.source,
    }


def _cache_age(entry: CachedFxRate, now: datetime | None = None) -> timedelta:
    now = now or datetime.now(timezone.utc)
    return now - entry.updated_at.astimezone(timezone.utc)


def _load_persisted_cache(store: TradingStore) -> CachedFxRate | None:
    raw = store.get_settings_dict().get(CACHE_SETTINGS_KEY)
    return _parse_cache(raw)


def _save_persisted_cache(store: TradingStore, entry: CachedFxRate) -> None:
    store.update_settings(
        CACHE_SETTINGS_KEY,
        _serialize_cache(entry),
        description="Canonical JPY per 1 USD for account-currency conversion",
    )


def _fetch_rate_from_db_candles(store: TradingStore) -> Decimal | None:
    from quantara_engine.persistence.batch_summary import batch_latest_candle_closes
    from quantara_engine.portfolio.currency import USDJPY_DB_SYMBOL

    store.ensure_usdjpy_conversion_instrument()
    from sqlalchemy import select
    from quantara_engine.models.instruments import Instrument as OrmInstrument

    row = store.session.scalar(
        select(OrmInstrument).where(OrmInstrument.symbol == USDJPY_DB_SYMBOL)
    )
    if not row:
        return None
    closes = batch_latest_candle_closes(store, [str(row.id)], "5m")
    if closes:
        return closes[str(row.id)]
    return None


def _fetch_rate_from_tiingo(store: TradingStore) -> Decimal | None:
    from quantara_engine.market_data.adapters.tiingo import TiingoMarketDataProvider
    from quantara_engine.market_data.credits import FetchPriority
    from quantara_engine.market_data.provider_resolver import is_provider_eligible
    from quantara_engine.market_data.registry import ProviderName

    if not is_provider_eligible(store, ProviderName.TIINGO, priority=FetchPriority.OPEN_POSITION, purpose="fx_rate"):
        return None
    provider = TiingoMarketDataProvider(store=store, caller="fx_rate:tiingo", priority=FetchPriority.OPEN_POSITION)
    quote = provider.fetch_fx_top_quote("usdjpy")
    if quote:
        return quote["mid"]
    return provider.fetch_fx_latest_close("usdjpy")


def _fetch_rate_from_twelve_data(store: TradingStore) -> Decimal | None:
    from quantara_engine.market_data.adapters.twelvedata import TwelveDataMarketDataProvider
    from quantara_engine.market_data.credits import FetchPriority
    from quantara_engine.market_data.provider_resolver import is_provider_eligible
    from quantara_engine.market_data.registry import AssetClass, AssetDefinition, ProviderName
    from quantara_engine.portfolio.currency import USDJPY_DB_SYMBOL, USDJPY_PROVIDER_SYMBOL
    from sqlalchemy import select
    from quantara_engine.models.instruments import Instrument as OrmInstrument

    if not is_provider_eligible(store, ProviderName.TWELVE_DATA, priority=FetchPriority.OPEN_POSITION, purpose="fx_rate"):
        return None

    row = store.session.scalar(
        select(OrmInstrument).where(OrmInstrument.symbol == USDJPY_DB_SYMBOL)
    )
    if not row:
        return None

    asset = AssetDefinition(
        canonical_symbol="USD/JPY",
        db_symbol=USDJPY_DB_SYMBOL,
        display_symbol="USD/JPY",
        asset_class=AssetClass.FOREX,
        primary_provider=ProviderName.TWELVE_DATA,
        secondary_provider=ProviderName.TIINGO,
        provider_symbols={
            ProviderName.TWELVE_DATA.value: USDJPY_PROVIDER_SYMBOL,
            ProviderName.TIINGO.value: "usdjpy",
        },
        trading_sessions={"sessions": ["24x5"]},
        pip_size="0.01",
        price_tick_size="0.001",
        quantity_step="1000",
        min_quantity="1000",
    )
    provider = TwelveDataMarketDataProvider(store=store, caller="fx_rate:twelvedata", asset=asset)
    candles = provider.fetch_latest(str(row.id), "5m")
    if not candles:
        return None
    return candles[-1].close


def _fetch_rate_from_provider(store: TradingStore) -> tuple[Decimal | None, str]:
    """Cascade: Tiingo → Twelve Data (Alpaca FX disabled — 404 on current account)."""
    from quantara_engine.market_data.provider_cooldown import mark_cooldown
    from quantara_engine.market_data.registry import ProviderName

    from collections.abc import Callable

    fetchers: list[tuple[str, ProviderName, Callable[[TradingStore], Decimal | None]]] = [
        ("tiingo", ProviderName.TIINGO, _fetch_rate_from_tiingo),
        ("twelvedata", ProviderName.TWELVE_DATA, _fetch_rate_from_twelve_data),
    ]
    for source, provider_name, fetcher in fetchers:
        try:
            rate = fetcher(store)
            if rate is not None and rate > 0:
                return rate, source
        except Exception as exc:
            logger.warning("USDJPY %s refresh failed: %s", source, exc)
            mark_cooldown(store, provider_name.value, reason=str(exc))
    return None, "unknown"


def _try_advisory_lock(store: TradingStore) -> bool:
    session = getattr(store, "session", None)
    if session is None:
        return True
    try:
        return bool(
            session.execute(
                text("SELECT pg_try_advisory_lock(:lock_id)"),
                {"lock_id": FX_REFRESH_ADVISORY_LOCK_ID},
            ).scalar()
        )
    except Exception:
        logger.debug("Advisory lock unavailable; proceeding with process lock only", exc_info=True)
        return True


def _release_advisory_lock(store: TradingStore) -> None:
    session = getattr(store, "session", None)
    if session is None:
        return
    try:
        session.execute(
            text("SELECT pg_advisory_unlock(:lock_id)"),
            {"lock_id": FX_REFRESH_ADVISORY_LOCK_ID},
        )
    except Exception:
        logger.debug("Advisory unlock failed", exc_info=True)


def _refresh_jpy_per_usd(store: TradingStore, *, previous: CachedFxRate | None) -> CachedFxRate:
    """Refresh canonical rate — DB candles first, then provider once. Never raises."""
    now = datetime.now(timezone.utc)
    rate: Decimal | None = None
    source = "unknown"

    try:
        rate = _fetch_rate_from_db_candles(store)
        if rate is not None:
            source = "db_candle"
    except Exception as exc:
        logger.warning("USDJPY DB candle lookup failed: %s", exc)

    if rate is None:
        try:
            provider_rate, provider_source = _fetch_rate_from_provider(store)
            if provider_rate is not None:
                rate = provider_rate
                source = f"provider:{provider_source}"
        except Exception as exc:
            logger.warning(
                "USDJPY provider refresh failed — using last known rate if available: %s",
                exc,
            )

    if rate is None or rate <= 0:
        if previous is not None:
            logger.warning(
                "USDJPY refresh unavailable — continuing with cached rate %s (source=%s, age=%s)",
                previous.rate,
                previous.source,
                _cache_age(previous, now),
            )
            # Touch updated_at so provider is not retried on every consumer call this hour.
            held = CachedFxRate(
                rate=previous.rate,
                updated_at=now,
                source=f"{previous.source}_held",
            )
            _save_persisted_cache(store, held)
            return held
        rate = DEFAULT_JPY_PER_USD
        source = "fallback_default"
        logger.warning("No USDJPY rate available — using default fallback %s", rate)

    entry = CachedFxRate(rate=rate, updated_at=now, source=source)
    _save_persisted_cache(store, entry)
    return entry


def get_canonical_jpy_per_usd(store: TradingStore) -> Decimal:
    """Return the shared JPY-per-USD rate. No per-call provider access when cache is fresh."""
    now = datetime.now(timezone.utc)
    cached = _load_persisted_cache(store)
    if cached is not None and _cache_age(cached, now) < REFRESH_INTERVAL:
        return cached.rate

    with _process_refresh_lock:
        cached = _load_persisted_cache(store)
        if cached is not None and _cache_age(cached, now) < REFRESH_INTERVAL:
            return cached.rate

        if not _try_advisory_lock(store):
            if cached is not None:
                return cached.rate
            bootstrapped = _bootstrap_without_provider(store)
            return bootstrapped.rate

        try:
            cached = _load_persisted_cache(store)
            if cached is not None and _cache_age(cached, now) < REFRESH_INTERVAL:
                return cached.rate
            entry = _refresh_jpy_per_usd(store, previous=cached)
            return entry.rate
        finally:
            _release_advisory_lock(store)


def build_dashboard_fx_rates(store: TradingStore, quote_currencies: set[str]) -> "FxRateTable":
    """Read-only FX for dashboard analytics — persisted cache + DB candles only."""
    from quantara_engine.portfolio.currency import ACCOUNT_CURRENCY, FxRateTable

    rates: dict[str, Decimal] = {ACCOUNT_CURRENCY: Decimal("1")}
    needed = {c.upper() for c in quote_currencies if c.upper() != ACCOUNT_CURRENCY}
    if "JPY" in needed:
        cached = _load_persisted_cache(store)
        jpy_rate = cached.rate if cached is not None and cached.rate > 0 else None
        if jpy_rate is None:
            try:
                jpy_rate = _fetch_rate_from_db_candles(store)
            except Exception as exc:
                logger.debug("Dashboard FX DB candle lookup failed: %s", exc)
                jpy_rate = None
        if jpy_rate is not None and jpy_rate > 0:
            rates["JPY"] = jpy_rate
    return FxRateTable(quote_per_usd=rates)


def _bootstrap_without_provider(store: TradingStore) -> CachedFxRate:
    """Seed cache from DB candles or default — never calls external provider."""
    now = datetime.now(timezone.utc)
    try:
        rate = _fetch_rate_from_db_candles(store)
        if rate is not None and rate > 0:
            entry = CachedFxRate(rate=rate, updated_at=now, source="db_candle")
            _save_persisted_cache(store, entry)
            return entry
    except Exception as exc:
        logger.warning("USDJPY bootstrap DB lookup failed: %s", exc)

    entry = CachedFxRate(rate=DEFAULT_JPY_PER_USD, updated_at=now, source="fallback_default")
    _save_persisted_cache(store, entry)
    return entry
