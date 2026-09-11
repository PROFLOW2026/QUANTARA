"""Fetch market data via provider registry — live ingest vs bounded bulk work."""

from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime, timezone

from quantara_engine.db.session import session_scope
from quantara_engine.market_data.adapters.alpaca import AlpacaError
from quantara_engine.market_data.adapters.tiingo import TiingoError
from quantara_engine.market_data.adapters.twelvedata import TwelveDataError
from quantara_engine.market_data.aggregation import (
    DERIVED_FROM_5M,
    aggregate_from_5m,
    derivation_source_limit,
    incremental_derive_from_5m,
    incremental_source_limit,
)
from quantara_engine.market_data.credits import (
    FetchPriority,
    is_blocked as twelve_data_blocked,
    status_payload as twelve_status,
)
from quantara_engine.market_data.factory import get_provider_for_asset
from quantara_engine.market_data.provider_resolver import (
    dedupe_complete_candles,
    fetch_with_failover,
    has_eligible_provider,
)
from quantara_engine.market_data.polling import (
    PROVIDER_TIMEFRAME,
    STRATEGY_MIN_CANDLES,
    is_market_data_fresh,
    should_fetch_timeframe,
)
from quantara_engine.market_data.provider_budgets import all_provider_status, can_request, record_request
from quantara_engine.market_data.registry import AssetClass, ProviderName, get_asset, list_target_assets
from quantara_engine.market_data.tiingo_fallback_scheduler import (
    TiingoFallbackPlan,
    build_tiingo_fallback_plan,
    defer_reason_for_asset,
)
from quantara_engine.market_data.sessions import is_us_equity_rth
from quantara_engine.market_data.spot_price import update_spot_from_latest_5m
from quantara_engine.market_data.validation import validate_candle
from quantara_engine.persistence.store import TradingStore

logger = logging.getLogger(__name__)

TIINGO_POLL_INTERVAL_MINUTES = 12
LAST_FETCH_KEY = "provider_budget:tiingo:last_fetch"
ALPACA_LIVE_LAST_FETCH_KEY = "provider_budget:alpaca:last_live_fetch"

BTC_CATCHUP_FRESH_MINUTES = 30
BTC_CATCHUP_MAX_EXTRA_PASSES = 2
BULK_BOOTSTRAP_KEY = "fetch_bulk:last_bootstrap"
BOOTSTRAP_PERSIST_CHUNK = 100
DERIVED_PERSIST_CHUNK = 20
GAP_FILL_CHUNK_THRESHOLD = 50


def _tiingo_last_fetch(store: TradingStore, db_symbol: str) -> datetime | None:
    raw = store.get_settings_dict().get(f"{LAST_FETCH_KEY}:{db_symbol}")
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw))
    except ValueError:
        return None


def _mark_tiingo_fetch(store: TradingStore, db_symbol: str, when: datetime) -> None:
    store.update_settings(
        f"{LAST_FETCH_KEY}:{db_symbol}",
        when.isoformat(),
        description=f"Tiingo last poll for {db_symbol}",
    )


def _mark_alpaca_live_fetch(store: TradingStore, db_symbol: str, when: datetime) -> None:
    store.update_settings(
        f"{ALPACA_LIVE_LAST_FETCH_KEY}:{db_symbol}",
        when.isoformat(),
        description=f"Alpaca live 5m poll for {db_symbol}",
    )


def _uses_alpaca_live_equity(asset, now: datetime) -> bool:
    """US equities with Alpaca primary during RTH (status / timing hints)."""
    return (
        asset.primary_provider == ProviderName.ALPACA
        and asset.asset_class in (AssetClass.STOCK, AssetClass.INDEX)
        and is_us_equity_rth(now)
    )


def _aggregation_mode(asset) -> str:
    if asset.asset_class in (AssetClass.STOCK, AssetClass.INDEX):
        return "us_rth"
    return "utc"


def _should_poll_asset(
    store: TradingStore,
    asset,
    *,
    now: datetime,
    stored: int,
    force_bootstrap: bool,
    live: bool,
    last_ts: datetime | None = None,
    plan: TiingoFallbackPlan | None = None,
) -> tuple[bool, str | None]:
    # Bootstrap-eligible assets must still ingest market data on the live path.
    # Strategy `insufficient_history` gates evaluation only — never fetch ingestion.
    if force_bootstrap and not live:
        return True, None

    if live and last_ts is not None and stored >= STRATEGY_MIN_CANDLES:
        if not should_fetch_timeframe(PROVIDER_TIMEFRAME, last_ts, now) and is_market_data_fresh(
            last_ts, PROVIDER_TIMEFRAME, now
        ):
            return False, "deferred (candle sufficient for cycle)"

    if plan is not None and plan.fallback_active:
        deferred = defer_reason_for_asset(plan, asset.db_symbol)
        if deferred:
            return False, deferred

    if asset.primary_provider == ProviderName.TWELVE_DATA:
        from quantara_engine.market_data.credits import can_fetch

        primary_ok = not twelve_data_blocked(store) and can_fetch(store, FetchPriority.SCHEDULED)
        if not primary_ok and not has_eligible_provider(store, asset, priority=FetchPriority.SCHEDULED):
            if twelve_data_blocked(store):
                return False, "deferred (Twelve Data blocked; no fallback available)"
            return False, "deferred (Twelve Data credit guard; no fallback available)"

    if asset.primary_provider == ProviderName.TIINGO:
        if not is_us_equity_rth(now):
            if stored >= STRATEGY_MIN_CANDLES:
                return False, "deferred (US market closed — last session data retained)"
        last_poll = _tiingo_last_fetch(store, asset.db_symbol)
        if last_poll:
            elapsed = (now - last_poll).total_seconds() / 60
            if elapsed < TIINGO_POLL_INTERVAL_MINUTES:
                return False, f"deferred (Tiingo poll interval {TIINGO_POLL_INTERVAL_MINUTES}m)"
        from quantara_engine.market_data.provider_budgets import can_request

        from quantara_engine.market_data.provider_budgets import can_request_tiingo_candle

        if not can_request_tiingo_candle(store):
            return False, "deferred (Tiingo hourly budget)"

    if asset.primary_provider == ProviderName.ALPACA:
        if asset.asset_class in (AssetClass.STOCK, AssetClass.INDEX):
            if not is_us_equity_rth(now) and stored >= STRATEGY_MIN_CANDLES:
                return False, "deferred (US market closed — last session data retained)"
        if not has_eligible_provider(store, asset, priority=FetchPriority.SCHEDULED):
            return False, "deferred (no eligible provider — cooldown or budget)"

    return True, None


def _derive_full(
    store: TradingStore,
    instrument_id: str,
    *,
    session_mode: str,
    timeframes: tuple[str, ...] = DERIVED_FROM_5M,
) -> int:
    derived_count = 0
    stored_5m = store.count_candles(instrument_id, PROVIDER_TIMEFRAME)
    # US RTH drops many 5m bars; the UTC-minimum window (~2400 bars) yields ~185 1h buckets.
    # Full rebuild must scan all stored 5m history to reach 200+ completed 1h candles.
    lookback = stored_5m if session_mode == "us_rth" else derivation_source_limit(stored_5m)
    base_rows = store.list_recent_candles(instrument_id, PROVIDER_TIMEFRAME, limit=lookback)
    if not base_rows:
        return 0

    derived_candles: list = []
    for target_tf in timeframes:
        for candle in aggregate_from_5m(base_rows, target_tf, session_mode=session_mode):
            try:
                validate_candle(candle)
            except Exception as exc:
                logger.warning("Invalid derived candle skipped: %s", exc)
                continue
            derived_candles.append(candle)

    if len(derived_candles) >= GAP_FILL_CHUNK_THRESHOLD:
        return _persist_candles_chunked(derived_candles, chunk_size=DERIVED_PERSIST_CHUNK)

    for candle in derived_candles:
        store.upsert_candle(candle)
        derived_count += 1
    return derived_count


def _derive_incremental(
    store: TradingStore,
    instrument_id: str,
    new_timestamps: list[datetime],
    *,
    session_mode: str,
) -> int:
    if not new_timestamps:
        return 0
    lookback = incremental_source_limit(len(new_timestamps))
    base_rows = store.list_recent_candles(instrument_id, PROVIDER_TIMEFRAME, limit=lookback)
    derived_count = 0
    for candle in incremental_derive_from_5m(
        base_rows,
        new_timestamps,
        session_mode=session_mode,
    ):
        try:
            validate_candle(candle)
        except Exception as exc:
            logger.warning("Invalid incremental derived candle skipped: %s", exc)
            continue
        store.upsert_candle(candle)
        derived_count += 1
    return derived_count


def _fetch_tiingo_crypto_batch(
    store: TradingStore,
    plan: TiingoFallbackPlan,
    now: datetime,
    timings: dict[str, float],
) -> dict[str, tuple[int, int]]:
    """One Tiingo HTTP request for BTC+ETH when both are scheduled this cycle."""
    from quantara_engine.market_data.adapters.tiingo import TiingoMarketDataProvider
    from quantara_engine.market_data.provider_resolver import is_provider_eligible

    if not plan.crypto_batch:
        return {}

    if not is_provider_eligible(store, ProviderName.TIINGO, priority=FetchPriority.SCHEDULED, purpose="candles"):
        return {}

    items: list[tuple] = []
    for sym in plan.crypto_batch:
        asset = get_asset(sym)
        if asset is None:
            continue
        instrument = store.get_instrument_by_symbol(sym)
        if not instrument:
            continue
        last_ts = store.latest_candle_timestamp(instrument.id, PROVIDER_TIMEFRAME)
        items.append((asset, str(instrument.id), last_ts))

    if len(items) < 2:
        return {}

    t0 = time.perf_counter()
    provider = TiingoMarketDataProvider(
        store=store,
        caller="fetch_live_job:crypto_batch",
        priority=FetchPriority.SCHEDULED,
    )
    try:
        batch = provider.fetch_latest_crypto_batch(items, PROVIDER_TIMEFRAME)
    except TiingoError as exc:
        logger.warning("Tiingo crypto batch failed: %s", exc)
        return {}
    timings["tiingo"] = timings.get("tiingo", 0.0) + (time.perf_counter() - t0) * 1000

    results: dict[str, tuple[int, int]] = {}
    for asset, instrument_id, _since in items:
        candles = dedupe_complete_candles(batch.get(instrument_id, []))
        if not candles:
            continue
        count = 0
        new_timestamps: list[datetime] = []
        for candle in candles:
            try:
                validate_candle(candle)
            except Exception as exc:
                logger.warning("Invalid batch candle skipped (%s): %s", asset.db_symbol, exc)
                continue
            store.upsert_candle(candle)
            count += 1
            new_timestamps.append(candle.timestamp)
        derived = 0
        if new_timestamps:
            derived = _derive_incremental(
                store,
                instrument_id,
                new_timestamps,
                session_mode=_aggregation_mode(asset),
            )
            _mark_tiingo_fetch(store, asset.db_symbol, now)
        if count:
            results[asset.db_symbol] = (count, derived)
    return results


def _fetch_asset_live(
    store: TradingStore,
    instrument,
    asset,
    now: datetime,
    timings: dict[str, float],
    *,
    plan: TiingoFallbackPlan | None = None,
    skip_fetch: bool = False,
) -> tuple[int, int, str | None]:
    provider_key = asset.primary_provider.value
    t0 = time.perf_counter()

    timeframe = PROVIDER_TIMEFRAME
    last_ts = store.latest_candle_timestamp(instrument.id, timeframe)
    stored = store.count_candles(instrument.id, timeframe)
    force_bootstrap = stored < STRATEGY_MIN_CANDLES

    should_poll, defer_reason = _should_poll_asset(
        store,
        asset,
        now=now,
        stored=stored,
        force_bootstrap=force_bootstrap,
        live=True,
        last_ts=last_ts,
        plan=plan,
    )
    if not should_poll:
        timings[provider_key] = timings.get(provider_key, 0.0) + (time.perf_counter() - t0) * 1000
        return 0, 0, defer_reason

    if skip_fetch:
        return 0, 0, None

    new_timestamps: list[datetime] = []
    count = 0
    error: str | None = None
    used_provider: ProviderName | None = None

    gap_fill = (
        last_ts is not None
        and stored >= STRATEGY_MIN_CANDLES
        and not is_market_data_fresh(last_ts, timeframe, now)
    )

    def _live_fetch(provider):
        if gap_fill and hasattr(provider, "fetch_gap_fill"):
            if hasattr(provider, "bind_context"):
                provider.bind_context(  # type: ignore[attr-defined]
                    store=store,
                    caller="fetch_live_job:gap_fill",
                    asset=asset,
                    priority=FetchPriority.CATCH_UP,
                )
            return provider.fetch_gap_fill(instrument.id, timeframe, last_ts)  # type: ignore[attr-defined]
        if should_fetch_timeframe(timeframe, last_ts, now):
            return provider.fetch_latest(instrument.id, timeframe, since=last_ts)
        return []

    outcome = fetch_with_failover(
        store,
        asset,
        caller="fetch_live_job",
        priority=FetchPriority.SCHEDULED,
        fetch_fn=_live_fetch,
    )
    candles = dedupe_complete_candles(outcome.candles)
    used_provider = outcome.provider
    if outcome.error and not candles:
        error = f"{asset.db_symbol}: {outcome.error}"

    if used_provider is not None:
        provider_key = used_provider.value

    timings[provider_key] = timings.get(provider_key, 0.0) + (time.perf_counter() - t0) * 1000

    persist_t0 = time.perf_counter()
    validated: list = []
    for candle in candles:
        try:
            validate_candle(candle)
        except Exception as exc:
            logger.warning("Invalid candle skipped (%s): %s", asset.db_symbol, exc)
            continue
        validated.append(candle)
        new_timestamps.append(candle.timestamp)

    derived = 0
    if len(validated) >= GAP_FILL_CHUNK_THRESHOLD:
        count = _persist_candles_chunked(validated)
        if count:
            with session_scope() as derive_session:
                derive_store = TradingStore(derive_session)
                derived = _derive_full(
                    derive_store,
                    instrument.id,
                    session_mode=_aggregation_mode(asset),
                )
                if asset.db_symbol == "XAUUSD":
                    update_spot_from_latest_5m(derive_store, instrument.id)
            if used_provider == ProviderName.ALPACA:
                with session_scope() as mark_session:
                    _mark_alpaca_live_fetch(TradingStore(mark_session), asset.db_symbol, now)
            elif used_provider == ProviderName.TIINGO:
                with session_scope() as mark_session:
                    _mark_tiingo_fetch(TradingStore(mark_session), asset.db_symbol, now)
            if gap_fill and used_provider == ProviderName.ALPACA:
                from quantara_engine.market_data.registry import provider_symbol

                _record_budget_best_effort(
                    ProviderName.ALPACA.value,
                    symbol=provider_symbol(asset, ProviderName.ALPACA),
                    caller="fetch_live_job:gap_fill",
                )
            timings["derive_ms"] = timings.get("derive_ms", 0.0) + (time.perf_counter() - persist_t0) * 1000
            timings["persist_5m_ms"] = timings.get("persist_5m_ms", 0.0) + (time.perf_counter() - persist_t0) * 1000
            return count, derived, error
    else:
        for candle in validated:
            store.upsert_candle(candle)
            count += 1
    timings["persist_5m_ms"] = timings.get("persist_5m_ms", 0.0) + (time.perf_counter() - persist_t0) * 1000

    if count and used_provider == ProviderName.ALPACA:
        _mark_alpaca_live_fetch(store, asset.db_symbol, now)
    elif count and used_provider == ProviderName.TIINGO:
        _mark_tiingo_fetch(store, asset.db_symbol, now)

    derive_t0 = time.perf_counter()
    if new_timestamps:
        derived = _derive_incremental(
            store,
            instrument.id,
            new_timestamps,
            session_mode=_aggregation_mode(asset),
        )
    timings["derive_ms"] = timings.get("derive_ms", 0.0) + (time.perf_counter() - derive_t0) * 1000

    if asset.db_symbol == "XAUUSD" and count:
        update_spot_from_latest_5m(store, instrument.id)

    return count, derived, error


def _record_budget_best_effort(
    provider: str,
    *,
    symbol: str,
    caller: str,
    count: int = 1,
) -> None:
    """Record provider usage outside long ingest transactions (best-effort)."""
    try:
        with session_scope() as session:
            record_request(
                TradingStore(session),
                provider,
                symbol=symbol,
                caller=caller,
                count=count,
                success=True,
            )
    except Exception as exc:
        logger.warning("Provider budget record skipped (%s/%s): %s", provider, symbol, exc)


def _persist_candles_chunked(candles: list, *, chunk_size: int = BOOTSTRAP_PERSIST_CHUNK) -> int:
    """Upsert bootstrap candles in short commits (remote DB statement timeout safe)."""
    count = 0
    offset = 0
    while offset < len(candles):
        chunk = candles[offset : offset + chunk_size]
        try:
            with session_scope() as session:
                store = TradingStore(session)
                count += store.upsert_candles_batch(chunk)
            offset += len(chunk)
        except Exception as exc:
            if chunk_size <= 10:
                raise
            chunk_size = max(10, chunk_size // 2)
            logger.warning(
                "Bootstrap persist retry with chunk_size=%d after: %s",
                chunk_size,
                exc,
            )
            time.sleep(1.0)
    return count


def _deepen_history_for_1h(asset) -> tuple[int, int]:
    """Pull additional provider history when derived 1h bars are below EMA200 minimum."""
    derived_only = 0
    with session_scope() as session:
        store = TradingStore(session)
        instrument = store.get_instrument_by_symbol(asset.db_symbol)
        if not instrument:
            return 0, 0
        instrument_id = instrument.id
        if store.count_candles(instrument_id, "1h") >= STRATEGY_MIN_CANDLES:
            return 0, 0
        derived_only = _derive_full(
            store,
            instrument_id,
            session_mode=_aggregation_mode(asset),
            timeframes=("1h",),
        )
        if store.count_candles(instrument_id, "1h") >= STRATEGY_MIN_CANDLES:
            return 0, derived_only

    try:
        provider = get_provider_for_asset(asset)
    except ValueError:
        return 0, derived_only

    if hasattr(provider, "bind_context"):
        provider.bind_context(  # type: ignore[attr-defined]
            store=None,
            caller="fetch_bulk_job:deepen_1h",
            asset=asset,
            priority=FetchPriority.CATCH_UP,
        )

    try:
        if hasattr(provider, "fetch_bootstrap"):
            candles = provider.fetch_bootstrap(instrument_id, PROVIDER_TIMEFRAME)  # type: ignore[attr-defined]
        else:
            candles = provider.fetch_latest(instrument_id, PROVIDER_TIMEFRAME, since=None)
    except (TwelveDataError, AlpacaError, TiingoError) as exc:
        logger.warning("1h deepen fetch failed for %s — %s", asset.db_symbol, exc)
        return 0, derived_only

    validated: list = []
    for candle in candles:
        try:
            validate_candle(candle)
        except Exception as exc:
            logger.warning("Invalid deepen candle skipped (%s): %s", asset.db_symbol, exc)
            continue
        validated.append(candle)

    count = _persist_candles_chunked(validated) if validated else 0
    derived = derived_only
    if count:
        with session_scope() as derive_session:
            derive_store = TradingStore(derive_session)
            derived += _derive_full(
                derive_store,
                instrument_id,
                session_mode=_aggregation_mode(asset),
                timeframes=("1h",),
            )
        from quantara_engine.market_data.registry import provider_symbol

        _record_budget_best_effort(
            asset.primary_provider.value,
            symbol=provider_symbol(asset, asset.primary_provider),
            caller="fetch_bulk_job:deepen_1h",
        )
    return count, derived


def _bootstrap_asset_isolated(asset) -> tuple[int, int, str | None]:
    """Bootstrap one asset with chunked commits (avoids long locks during bulk ingest)."""
    with session_scope() as session:
        store = TradingStore(session)
        instrument = store.get_instrument_by_symbol(asset.db_symbol)
        if not instrument:
            return 0, 0, f"{asset.db_symbol}: instrument missing — run seed_8_assets.py"
        now = datetime.now(timezone.utc)
        return _fetch_asset_bulk(store, instrument, asset, now)


def _fetch_asset_bulk(
    store: TradingStore,
    instrument,
    asset,
    now: datetime,
) -> tuple[int, int, str | None]:
    timeframe = PROVIDER_TIMEFRAME
    stored = store.count_candles(instrument.id, timeframe)
    if stored >= STRATEGY_MIN_CANDLES:
        return 0, 0, None

    if asset.primary_provider == ProviderName.ALPACA and not can_request(store, "alpaca"):
        return 0, 0, "deferred (Alpaca budget)"
    if asset.primary_provider == ProviderName.TIINGO and not can_request(store, "tiingo"):
        return 0, 0, "deferred (Tiingo budget)"
    if asset.primary_provider == ProviderName.TWELVE_DATA:
        from quantara_engine.market_data.credits import can_fetch

        primary_ok = not twelve_data_blocked(store) and can_fetch(store, FetchPriority.CATCH_UP)
        if not primary_ok and not has_eligible_provider(store, asset, priority=FetchPriority.CATCH_UP):
            if twelve_data_blocked(store):
                return 0, 0, "deferred (Twelve Data blocked; no fallback available)"
            return 0, 0, "deferred (Twelve Data credit guard; no fallback available)"

    count = 0
    error: str | None = None
    primary_provider_key = asset.primary_provider.value
    secondary_provider_key: str | None = None

    def _bootstrap_fetch(provider):
        if hasattr(provider, "fetch_bootstrap"):
            return provider.fetch_bootstrap(instrument.id, timeframe)  # type: ignore[attr-defined]
        return provider.fetch_latest(instrument.id, timeframe, since=None)

    outcome = fetch_with_failover(
        store,
        asset,
        caller="fetch_bulk_job:bootstrap",
        priority=FetchPriority.CATCH_UP,
        fetch_fn=_bootstrap_fetch,
    )
    if outcome.error and not outcome.candles:
        error = f"{asset.db_symbol}: {outcome.error}"
        logger.warning("Bulk bootstrap failed for %s — %s", asset.db_symbol, outcome.error)

    validated_primary: list = []
    for candle in dedupe_complete_candles(outcome.candles):
        try:
            validate_candle(candle)
        except Exception as exc:
            logger.warning("Invalid bootstrap candle skipped (%s): %s", asset.db_symbol, exc)
            continue
        validated_primary.append(candle)

    if outcome.provider:
        primary_provider_key = outcome.provider.value
        if outcome.provider != asset.primary_provider:
            secondary_provider_key = outcome.provider.value

    count = _persist_candles_chunked(validated_primary)

    with session_scope() as count_session:
        stored_after = TradingStore(count_session).count_candles(instrument.id, timeframe)
    if stored_after < STRATEGY_MIN_CANDLES and outcome.provider != asset.secondary_provider:
        # Chain may have stopped early — retry remaining providers if history still thin.
        remaining = [
            p
            for p in (asset.secondary_provider,)
            if p is not None and p != outcome.provider
        ]
        for fallback in remaining:
            try:
                secondary = get_provider_for_asset(asset, role="secondary")
                secondary_provider_key = fallback.value
                if hasattr(secondary, "bind_context"):
                    secondary.bind_context(  # type: ignore[attr-defined]
                        store=None,
                        caller="fetch_bulk_job:bootstrap_secondary",
                        asset=asset,
                        priority=FetchPriority.CATCH_UP,
                    )
                extra = secondary.fetch_bootstrap(instrument.id, timeframe)  # type: ignore[attr-defined]
                validated_secondary: list = []
                for candle in dedupe_complete_candles(extra):
                    try:
                        validate_candle(candle)
                    except Exception as exc:
                        logger.warning("Invalid secondary bootstrap candle skipped (%s): %s", asset.db_symbol, exc)
                        continue
                    validated_secondary.append(candle)
                count += _persist_candles_chunked(validated_secondary)
            except (TwelveDataError, AlpacaError, TiingoError) as exc:
                logger.warning("Secondary bootstrap failed for %s — %s", asset.db_symbol, exc)

    derived = 0
    if count:
        with session_scope() as derive_session:
            derive_store = TradingStore(derive_session)
            derived = _derive_full(
                derive_store, instrument.id, session_mode=_aggregation_mode(asset)
            )
            if asset.db_symbol == "XAUUSD":
                update_spot_from_latest_5m(derive_store, instrument.id)
        from quantara_engine.market_data.registry import provider_symbol

        if asset.primary_provider == ProviderName.TWELVE_DATA:
            try:
                with session_scope() as credit_session:
                    from quantara_engine.market_data.credits import record_usage

                    record_usage(
                        TradingStore(credit_session),
                        endpoint="time_series",
                        symbol=provider_symbol(asset, asset.primary_provider),
                        interval="5min",
                        caller="fetch_bulk_job:bootstrap",
                        credits=2,
                    )
            except Exception as exc:
                logger.warning("Twelve Data credit record skipped (%s): %s", asset.db_symbol, exc)
        else:
            _record_budget_best_effort(
                primary_provider_key,
                symbol=provider_symbol(asset, asset.primary_provider),
                caller="fetch_bulk_job:bootstrap",
            )
        if secondary_provider_key and asset.secondary_provider is not None:
            _record_budget_best_effort(
                secondary_provider_key,
                symbol=provider_symbol(asset, asset.secondary_provider),
                caller="fetch_bulk_job:bootstrap_secondary",
            )

    return count, derived, error


def _accelerate_btc_catchup(
    store: TradingStore,
    instrument,
    asset,
    now: datetime,
) -> tuple[int, int]:
    if asset.db_symbol != "BTCUSD":
        return 0, 0

    from quantara_engine.market_data.provider_budgets import can_request

    total_count = 0
    total_derived = 0
    new_timestamps: list[datetime] = []

    for pass_num in range(BTC_CATCHUP_MAX_EXTRA_PASSES):
        last_ts = store.latest_candle_timestamp(instrument.id, PROVIDER_TIMEFRAME)
        if last_ts and (now - last_ts).total_seconds() <= BTC_CATCHUP_FRESH_MINUTES * 60:
            break
        if not can_request(store, "alpaca"):
            logger.info("BTC catch-up pass %d skipped — Alpaca budget", pass_num + 1)
            break

        try:
            provider = get_provider_for_asset(asset)
        except ValueError:
            break
        if hasattr(provider, "bind_context"):
            provider.bind_context(  # type: ignore[attr-defined]
                store=store,
                caller="fetch_bulk_job:btc_catchup",
                asset=asset,
                priority=FetchPriority.CATCH_UP,
            )

        try:
            candles = provider.fetch_latest(instrument.id, PROVIDER_TIMEFRAME, since=last_ts)
        except (AlpacaError, TiingoError) as exc:
            logger.warning("BTC catch-up pass %d failed — %s", pass_num + 1, exc)
            break

        pass_count = 0
        for candle in candles:
            try:
                validate_candle(candle)
            except Exception as exc:
                logger.warning("Invalid BTC catch-up candle skipped: %s", exc)
                continue
            store.upsert_candle(candle)
            new_timestamps.append(candle.timestamp)
            pass_count += 1

        if pass_count == 0:
            break

        total_count += pass_count
        derived = _derive_incremental(
            store,
            instrument.id,
            new_timestamps,
            session_mode=_aggregation_mode(asset),
        )
        total_derived += derived
        logger.info("BTC catch-up pass %d upserted %d 5m bars", pass_num + 1, pass_count)

    return total_count, total_derived


def _finalize_worker_run(
    store: TradingStore,
    *,
    started_at: datetime,
    job_name: str,
    total_count: int,
    total_derived: int,
    errors: list[str],
    asset_status: dict[str, dict],
    timings: dict[str, float] | None = None,
    phase: str,
    tiingo_fallback: dict | None = None,
) -> None:
    finished_at = datetime.now(timezone.utc)
    duration_ms = (finished_at - started_at).total_seconds() * 1000
    health_t0 = time.perf_counter()
    credit_status = twelve_status(store)
    provider_status = all_provider_status(store)
    timings = dict(timings or {})
    timings["health_ms"] = (time.perf_counter() - health_t0) * 1000
    timings["total_ms"] = duration_ms

    status = "healthy" if not errors else ("degraded" if (total_count or total_derived) else "error")
    worker_payload: dict = {
        "status": status,
        "phase": phase,
        "last_run": started_at.isoformat(),
        "finished_at": finished_at.isoformat(),
        "duration_ms": round(duration_ms, 1),
        "timings_ms": {k: round(v, 1) for k, v in timings.items()},
        "candles_upserted": total_count,
        "derived_candles_upserted": total_derived,
        "provider": "multi",
        "provider_base_timeframe": PROVIDER_TIMEFRAME,
        "errors": errors or None,
        "credits": credit_status,
        "providers": provider_status,
        "assets": asset_status,
    }
    if tiingo_fallback:
        worker_payload["tiingo_fallback"] = tiingo_fallback
    store.update_worker_status("data_fetcher", worker_payload)
    store.save_worker_run(
        run_id=str(uuid.uuid4()),
        worker_name="data_fetcher",
        started_at=started_at,
        jobs_processed=total_count + total_derived,
    )
    logger.info(
        "%s completed base=%d derived=%d errors=%d duration_ms=%.0f",
        phase,
        total_count,
        total_derived,
        len(errors),
        duration_ms,
    )


def _fetch_live_asset_isolated(
    asset,
    now: datetime,
    timings: dict[str, float],
    *,
    plan: TiingoFallbackPlan | None = None,
    batch_result: tuple[int, int] | None = None,
) -> tuple[int, int, str | None, dict]:
    """One asset per transaction — avoids long locks blocking other ingest jobs."""
    with session_scope() as session:
        store = TradingStore(session)
        instrument = store.get_instrument_by_symbol(asset.db_symbol)
        if not instrument:
            return 0, 0, f"{asset.db_symbol}: instrument missing", {
                "status": "error",
                "error": "missing instrument",
            }
        stored_before = store.count_candles(instrument.id, PROVIDER_TIMEFRAME)

    bootstrap_count = 0
    bootstrap_derived = 0
    bootstrap_error: str | None = None
    if stored_before < STRATEGY_MIN_CANDLES:
        bootstrap_count, bootstrap_derived, bootstrap_error = _bootstrap_asset_isolated(asset)

    with session_scope() as session:
        store = TradingStore(session)
        instrument = store.get_instrument_by_symbol(asset.db_symbol)
        if not instrument:
            return bootstrap_count, bootstrap_derived, bootstrap_error, {"status": "error"}
        if batch_result is not None:
            count, derived = batch_result
            error = None
        else:
            skip = bool(plan and plan.crypto_batch and asset.db_symbol in plan.crypto_batch)
            count, derived, error = _fetch_asset_live(
                store,
                instrument,
                asset,
                now,
                timings,
                plan=plan,
                skip_fetch=skip,
            )
        latest = store.latest_candle_timestamp(instrument.id, PROVIDER_TIMEFRAME)
        stored = store.count_candles(instrument.id, PROVIDER_TIMEFRAME)

    count += bootstrap_count
    derived += bootstrap_derived
    if bootstrap_error and error is None:
        error = bootstrap_error

    use_alpaca_live = _uses_alpaca_live_equity(asset, now)
    live_provider = ProviderName.ALPACA.value if use_alpaca_live else asset.primary_provider.value

    if error and str(error).startswith("deferred"):
        status = {
            "status": "deferred",
            "provider": live_provider,
            "last_candle": latest.isoformat() if latest else None,
            "note": error,
        }
    elif error:
        status = {"status": "error", "provider": live_provider, "error": error}
    elif stored < STRATEGY_MIN_CANDLES:
        status = {
            "status": "bootstrapping",
            "provider": live_provider,
            "last_candle": latest.isoformat() if latest else None,
            "stored_5m": stored,
            "note": f"bootstrap in progress ({stored}/{STRATEGY_MIN_CANDLES} 5m bars)",
        }
    else:
        status = {
            "status": "healthy" if latest and is_market_data_fresh(latest, PROVIDER_TIMEFRAME, now) else "stale",
            "provider": live_provider,
            "last_candle": latest.isoformat() if latest else None,
            "candles_upserted": count,
        }
    return count, derived, error, status


def fetch_live_job(store: TradingStore | None = None) -> None:
    """Fast path: latest 5m ingest + incremental derivation only."""
    started_at = datetime.now(timezone.utc)
    timings: dict[str, float] = {}

    def _run_all() -> None:
        now = datetime.now(timezone.utc)
        total_count = 0
        total_derived = 0
        errors: list[str] = []
        asset_status: dict[str, dict] = {}

        with session_scope() as plan_session:
            plan = build_tiingo_fallback_plan(TradingStore(plan_session), now)

        batch_results: dict[str, tuple[int, int]] = {}
        if plan.crypto_batch:
            with session_scope() as batch_session:
                batch_results = _fetch_tiingo_crypto_batch(
                    TradingStore(batch_session), plan, now, timings
                )

        for asset in list_target_assets():
            if store is not None:
                instrument = store.get_instrument_by_symbol(asset.db_symbol)
                if not instrument:
                    errors.append(f"{asset.db_symbol}: instrument missing — run seed_8_assets.py")
                    asset_status[asset.db_symbol] = {"status": "error", "error": "missing instrument"}
                    continue
                batch_result = batch_results.get(asset.db_symbol)
                count, derived, error = _fetch_asset_live(
                    store,
                    instrument,
                    asset,
                    now,
                    timings,
                    plan=plan,
                    skip_fetch=batch_result is not None,
                )
                if batch_result is not None:
                    count, derived = batch_result
                latest = store.latest_candle_timestamp(instrument.id, PROVIDER_TIMEFRAME)
                stored = store.count_candles(instrument.id, PROVIDER_TIMEFRAME)
                use_alpaca_live = _uses_alpaca_live_equity(asset, now)
                live_provider = (
                    ProviderName.ALPACA.value if use_alpaca_live else asset.primary_provider.value
                )
                status = {
                    "status": "healthy" if latest else "stale",
                    "provider": live_provider,
                    "last_candle": latest.isoformat() if latest else None,
                    "candles_upserted": count,
                }
            else:
                count, derived, error, status = _fetch_live_asset_isolated(
                    asset,
                    now,
                    timings,
                    plan=plan,
                    batch_result=batch_results.get(asset.db_symbol),
                )

            total_count += count
            total_derived += derived
            if plan.fallback_active and defer_reason_for_asset(plan, asset.db_symbol):
                status["note"] = defer_reason_for_asset(plan, asset.db_symbol)
            if plan.fallback_active:
                status["tiingo_budget_mode"] = plan.budget_mode
            asset_status[asset.db_symbol] = status
            if error and not str(error).startswith("deferred"):
                errors.append(error)

        with session_scope() as broker_session:
            from quantara_engine.broker.integration import refresh_broker_marks_from_latest_closes

            refresh_broker_marks_from_latest_closes(TradingStore(broker_session))

        with session_scope() as status_session:
            status_store = TradingStore(status_session)
            _finalize_worker_run(
                status_store,
                started_at=started_at,
                job_name="fetch_live",
                total_count=total_count,
                total_derived=total_derived,
                errors=errors,
                asset_status=asset_status,
                timings=timings,
                phase="fetch_live",
                tiingo_fallback=(
                    {
                        "active": True,
                        "budget_mode": plan.budget_mode,
                        "safe_calls": plan.safe_calls,
                        "allowed_symbols": sorted(plan.allowed_symbols),
                        "crypto_batch": list(plan.crypto_batch),
                        "budget": plan.budget,
                    }
                    if plan.fallback_active
                    else None
                ),
            )

    _run_all()


def fetch_bulk_job(store: TradingStore | None = None) -> None:
    """Bounded bulk: bootstrap gaps, BTC catch-up, full derive repair."""
    started_at = datetime.now(timezone.utc)

    def _run(s: TradingStore | None) -> None:
        now = datetime.now(timezone.utc)
        total_count = 0
        total_derived = 0
        errors: list[str] = []
        asset_status: dict[str, dict] = {}

        for asset in list_target_assets():
            if s is not None:
                instrument = s.get_instrument_by_symbol(asset.db_symbol)
                if not instrument:
                    errors.append(f"{asset.db_symbol}: instrument missing — run seed_8_assets.py")
                    asset_status[asset.db_symbol] = {"status": "error", "error": "missing instrument"}
                    continue
                stored_before = s.count_candles(instrument.id, PROVIDER_TIMEFRAME)
            else:
                with session_scope() as check_session:
                    check_store = TradingStore(check_session)
                    instrument = check_store.get_instrument_by_symbol(asset.db_symbol)
                    if not instrument:
                        errors.append(f"{asset.db_symbol}: instrument missing — run seed_8_assets.py")
                        asset_status[asset.db_symbol] = {"status": "error", "error": "missing instrument"}
                        continue
                    stored_before = check_store.count_candles(instrument.id, PROVIDER_TIMEFRAME)

            if stored_before >= STRATEGY_MIN_CANDLES:
                continue

            if s is not None:
                count, derived, error = _fetch_asset_bulk(s, instrument, asset, now)
            else:
                count, derived, error = _bootstrap_asset_isolated(asset)
            if count or derived:
                total_count += count
                total_derived += derived
                asset_status[asset.db_symbol] = {
                    "status": "bootstrapped",
                    "candles_upserted": count,
                    "derived_upserted": derived,
                }
            elif error:
                asset_status[asset.db_symbol] = {"status": "error", "error": error}
            if error:
                errors.append(error)

        repair_timings: dict[str, float] = {}
        for asset in list_target_assets():
            with session_scope() as check_session:
                check_store = TradingStore(check_session)
                instrument = check_store.get_instrument_by_symbol(asset.db_symbol)
                if not instrument:
                    continue
                stored = check_store.count_candles(instrument.id, PROVIDER_TIMEFRAME)
                latest = check_store.latest_candle_timestamp(instrument.id, PROVIDER_TIMEFRAME)
                h1 = check_store.count_candles(instrument.id, "1h")

            if (
                stored >= STRATEGY_MIN_CANDLES
                and latest
                and not is_market_data_fresh(latest, PROVIDER_TIMEFRAME, now)
            ):
                gap_count, gap_derived, gap_error, gap_status = _fetch_live_asset_isolated(
                    asset, now, repair_timings
                )
                if gap_count or gap_derived:
                    total_count += gap_count
                    total_derived += gap_derived
                    asset_status[asset.db_symbol] = gap_status
                if gap_error and not str(gap_error).startswith("deferred"):
                    errors.append(gap_error)

            if h1 < STRATEGY_MIN_CANDLES and stored >= STRATEGY_MIN_CANDLES:
                deepen_count, deepen_derived = _deepen_history_for_1h(asset)
                if deepen_count or deepen_derived:
                    total_count += deepen_count
                    total_derived += deepen_derived
                    with session_scope() as count_session:
                        cs = TradingStore(count_session)
                        inst = cs.get_instrument_by_symbol(asset.db_symbol)
                        if inst:
                            asset_status[asset.db_symbol] = {
                                "status": "1h_repair",
                                "stored_1h": cs.count_candles(inst.id, "1h"),
                                "candles_upserted": deepen_count,
                                "derived_upserted": deepen_derived,
                            }

        with session_scope() as btc_session:
            btc_store = TradingStore(btc_session)
            btc = btc_store.get_instrument_by_symbol("BTCUSD")
            btc_asset = next((a for a in list_target_assets() if a.db_symbol == "BTCUSD"), None)
            if btc and btc_asset:
                extra_count, extra_derived = _accelerate_btc_catchup(btc_store, btc, btc_asset, now)
                if extra_count:
                    total_count += extra_count
                    total_derived += extra_derived
                    latest_btc = btc_store.latest_candle_timestamp(btc.id, PROVIDER_TIMEFRAME)
                    asset_status["BTCUSD"] = {
                        "status": "healthy",
                        "last_candle": latest_btc.isoformat() if latest_btc else None,
                        "candles_upserted": extra_count,
                        "catchup_passes": BTC_CATCHUP_MAX_EXTRA_PASSES,
                    }

        with session_scope() as status_session:
            status_store = TradingStore(status_session)
            status_store.update_settings(
                BULK_BOOTSTRAP_KEY, now.isoformat(), description="Last bulk fetch run"
            )
            _finalize_worker_run(
                status_store,
                started_at=started_at,
                job_name="fetch_bulk",
                total_count=total_count,
                total_derived=total_derived,
                errors=errors,
                asset_status=asset_status,
                timings={"bulk_ms": (datetime.now(timezone.utc) - started_at).total_seconds() * 1000},
                phase="fetch_bulk",
            )

    if store is not None:
        _run(store)
    else:
        _run(None)


def fetch_data_job(store: TradingStore | None = None) -> None:
    """Backward-compatible alias — runs live ingest only."""
    fetch_live_job(store)
