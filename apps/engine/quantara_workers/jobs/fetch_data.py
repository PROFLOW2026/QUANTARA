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
from quantara_engine.market_data.polling import PROVIDER_TIMEFRAME, STRATEGY_MIN_CANDLES, should_fetch_timeframe
from quantara_engine.market_data.provider_budgets import all_provider_status
from quantara_engine.market_data.registry import AssetClass, ProviderName, list_target_assets
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
    """US equities use Alpaca for timely 5m live bars during RTH."""
    return (
        asset.secondary_provider == ProviderName.ALPACA
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
) -> tuple[bool, str | None]:
    if force_bootstrap:
        if live:
            return False, "deferred (bootstrap scheduled in bulk job)"
        return True, None

    if asset.primary_provider == ProviderName.TWELVE_DATA:
        if twelve_data_blocked(store):
            return False, "deferred (Twelve Data blocked until credit reset)"
        from quantara_engine.market_data.credits import can_fetch

        if not can_fetch(store, FetchPriority.SCHEDULED):
            return False, "deferred (Twelve Data credit guard)"

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

        if not can_request(store, "tiingo"):
            return False, "deferred (Tiingo hourly budget)"

    return True, None


def _derive_full(
    store: TradingStore,
    instrument_id: str,
    *,
    session_mode: str,
) -> int:
    derived_count = 0
    stored_5m = store.count_candles(instrument_id, PROVIDER_TIMEFRAME)
    lookback = derivation_source_limit(stored_5m)
    base_rows = store.list_recent_candles(instrument_id, PROVIDER_TIMEFRAME, limit=lookback)
    if not base_rows:
        return 0

    for target_tf in DERIVED_FROM_5M:
        for candle in aggregate_from_5m(base_rows, target_tf, session_mode=session_mode):
            try:
                validate_candle(candle)
            except Exception as exc:
                logger.warning("Invalid derived candle skipped: %s", exc)
                continue
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


def _fetch_asset_live(
    store: TradingStore,
    instrument,
    asset,
    now: datetime,
    timings: dict[str, float],
) -> tuple[int, int, str | None]:
    use_alpaca_live = _uses_alpaca_live_equity(asset, now)
    provider_key = ProviderName.ALPACA.value if use_alpaca_live else asset.primary_provider.value
    t0 = time.perf_counter()

    timeframe = PROVIDER_TIMEFRAME
    last_ts = store.latest_candle_timestamp(instrument.id, timeframe)
    stored = store.count_candles(instrument.id, timeframe)
    force_bootstrap = stored < STRATEGY_MIN_CANDLES

    if use_alpaca_live:
        if not is_us_equity_rth(now) and stored >= STRATEGY_MIN_CANDLES:
            timings[provider_key] = timings.get(provider_key, 0.0) + (time.perf_counter() - t0) * 1000
            return 0, 0, "deferred (US market closed — last session data retained)"
        from quantara_engine.market_data.provider_budgets import can_request

        if not can_request(store, "alpaca"):
            timings[provider_key] = timings.get(provider_key, 0.0) + (time.perf_counter() - t0) * 1000
            return 0, 0, "deferred (Alpaca budget)"
        should_poll = True
        defer_reason = None
    else:
        should_poll, defer_reason = _should_poll_asset(
            store, asset, now=now, stored=stored, force_bootstrap=force_bootstrap, live=True
        )
    if not should_poll:
        timings[provider_key] = timings.get(provider_key, 0.0) + (time.perf_counter() - t0) * 1000
        return 0, 0, defer_reason

    try:
        provider = (
            get_provider_for_asset(asset, role="secondary")
            if use_alpaca_live
            else get_provider_for_asset(asset)
        )
    except ValueError as exc:
        timings[provider_key] = timings.get(provider_key, 0.0) + (time.perf_counter() - t0) * 1000
        return 0, 0, str(exc)

    if hasattr(provider, "bind_context"):
        provider.bind_context(  # type: ignore[attr-defined]
            store=store,
            caller="fetch_live_job",
            asset=asset,
            priority=FetchPriority.SCHEDULED,
        )

    new_timestamps: list[datetime] = []
    count = 0
    error: str | None = None

    try:
        if should_fetch_timeframe(timeframe, last_ts, now):
            candles = provider.fetch_latest(instrument.id, timeframe, since=last_ts)
        else:
            candles = []
    except (TwelveDataError, AlpacaError, TiingoError) as exc:
        error = f"{asset.db_symbol}: {exc}"
        logger.error("Live fetch failed for %s — %s", asset.db_symbol, exc)
        candles = []
    except Exception as exc:
        error = f"{asset.db_symbol}: {exc}"
        logger.exception("Live fetch failed for %s", asset.db_symbol)
        candles = []

    timings[provider_key] = timings.get(provider_key, 0.0) + (time.perf_counter() - t0) * 1000

    persist_t0 = time.perf_counter()
    for candle in candles:
        try:
            validate_candle(candle)
        except Exception as exc:
            logger.warning("Invalid candle skipped (%s): %s", asset.db_symbol, exc)
            continue
        store.upsert_candle(candle)
        new_timestamps.append(candle.timestamp)
        count += 1
    timings["persist_5m_ms"] = timings.get("persist_5m_ms", 0.0) + (time.perf_counter() - persist_t0) * 1000

    if use_alpaca_live and count:
        _mark_alpaca_live_fetch(store, asset.db_symbol, now)
    elif asset.primary_provider == ProviderName.TIINGO and count:
        _mark_tiingo_fetch(store, asset.db_symbol, now)

    derive_t0 = time.perf_counter()
    derived = 0
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

    try:
        provider = get_provider_for_asset(asset)
    except ValueError as exc:
        return 0, 0, str(exc)

    if hasattr(provider, "bind_context"):
        provider.bind_context(  # type: ignore[attr-defined]
            store=store,
            caller="fetch_bulk_job:bootstrap",
            asset=asset,
            priority=FetchPriority.CATCH_UP,
        )

    count = 0
    error: str | None = None
    try:
        if hasattr(provider, "fetch_bootstrap"):
            candles = provider.fetch_bootstrap(instrument.id, timeframe)  # type: ignore[attr-defined]
        else:
            candles = provider.fetch_latest(instrument.id, timeframe, since=None)
    except (TwelveDataError, AlpacaError, TiingoError) as exc:
        error = f"{asset.db_symbol}: {exc}"
        logger.warning("Bulk bootstrap failed for %s — %s", asset.db_symbol, exc)
        candles = []

    for candle in candles:
        try:
            validate_candle(candle)
        except Exception as exc:
            logger.warning("Invalid bootstrap candle skipped (%s): %s", asset.db_symbol, exc)
            continue
        store.upsert_candle(candle)
        count += 1

    stored_after = store.count_candles(instrument.id, timeframe)
    if (
        stored_after < STRATEGY_MIN_CANDLES
        and asset.secondary_provider is not None
    ):
        try:
            secondary = get_provider_for_asset(asset, role="secondary")
            if hasattr(secondary, "bind_context"):
                secondary.bind_context(  # type: ignore[attr-defined]
                    store=store,
                    caller="fetch_bulk_job:bootstrap_secondary",
                    asset=asset,
                    priority=FetchPriority.CATCH_UP,
                )
            extra = secondary.fetch_bootstrap(instrument.id, timeframe)  # type: ignore[attr-defined]
            for candle in extra:
                try:
                    validate_candle(candle)
                except Exception as exc:
                    logger.warning("Invalid secondary bootstrap candle skipped (%s): %s", asset.db_symbol, exc)
                    continue
                store.upsert_candle(candle)
                count += 1
        except (TwelveDataError, AlpacaError, TiingoError) as exc:
            logger.warning("Secondary bootstrap failed for %s — %s", asset.db_symbol, exc)

    derived = 0
    if count:
        derived = _derive_full(store, instrument.id, session_mode=_aggregation_mode(asset))
        if asset.db_symbol == "XAUUSD":
            update_spot_from_latest_5m(store, instrument.id)

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


def fetch_live_job(store: TradingStore | None = None) -> None:
    """Fast path: latest 5m ingest + incremental derivation only."""
    started_at = datetime.now(timezone.utc)
    timings: dict[str, float] = {}

    def _run(s: TradingStore) -> None:
        now = datetime.now(timezone.utc)
        total_count = 0
        total_derived = 0
        errors: list[str] = []
        asset_status: dict[str, dict] = {}

        for asset in list_target_assets():
            instrument = s.get_instrument_by_symbol(asset.db_symbol)
            if not instrument:
                errors.append(f"{asset.db_symbol}: instrument missing — run seed_8_assets.py")
                asset_status[asset.db_symbol] = {"status": "error", "error": "missing instrument"}
                continue

            count, derived, error = _fetch_asset_live(s, instrument, asset, now, timings)
            total_count += count
            total_derived += derived
            latest = s.latest_candle_timestamp(instrument.id, PROVIDER_TIMEFRAME)
            stored = s.count_candles(instrument.id, PROVIDER_TIMEFRAME)

            if error and str(error).startswith("deferred"):
                asset_status[asset.db_symbol] = {
                    "status": "deferred",
                    "provider": asset.primary_provider.value,
                    "last_candle": latest.isoformat() if latest else None,
                    "note": error,
                }
            elif error:
                errors.append(error)
                asset_status[asset.db_symbol] = {
                    "status": "error",
                    "provider": asset.primary_provider.value,
                    "error": error,
                }
            elif stored < STRATEGY_MIN_CANDLES:
                asset_status[asset.db_symbol] = {
                    "status": "bootstrapping",
                    "provider": asset.primary_provider.value,
                    "last_candle": latest.isoformat() if latest else None,
                    "note": "awaiting bulk bootstrap",
                }
            else:
                asset_status[asset.db_symbol] = {
                    "status": "healthy" if latest else "stale",
                    "provider": asset.primary_provider.value,
                    "last_candle": latest.isoformat() if latest else None,
                    "candles_upserted": count,
                }

        _finalize_worker_run(
            s,
            started_at=started_at,
            job_name="fetch_live",
            total_count=total_count,
            total_derived=total_derived,
            errors=errors,
            asset_status=asset_status,
            timings=timings,
            phase="fetch_live",
        )

    if store is not None:
        _run(store)
    else:
        with session_scope() as session:
            _run(TradingStore(session))


def fetch_bulk_job(store: TradingStore | None = None) -> None:
    """Bounded bulk: bootstrap gaps, BTC catch-up, full derive repair."""
    started_at = datetime.now(timezone.utc)

    def _run(s: TradingStore) -> None:
        now = datetime.now(timezone.utc)
        total_count = 0
        total_derived = 0
        errors: list[str] = []
        asset_status: dict[str, dict] = {}

        for asset in list_target_assets():
            instrument = s.get_instrument_by_symbol(asset.db_symbol)
            if not instrument:
                continue

            count, derived, error = _fetch_asset_bulk(s, instrument, asset, now)
            if count or derived:
                total_count += count
                total_derived += derived
                asset_status[asset.db_symbol] = {
                    "status": "bootstrapped",
                    "candles_upserted": count,
                    "derived_upserted": derived,
                }
            if error:
                errors.append(error)

        btc = s.get_instrument_by_symbol("BTCUSD")
        btc_asset = next((a for a in list_target_assets() if a.db_symbol == "BTCUSD"), None)
        if btc and btc_asset:
            extra_count, extra_derived = _accelerate_btc_catchup(s, btc, btc_asset, now)
            if extra_count:
                total_count += extra_count
                total_derived += extra_derived
                latest_btc = s.latest_candle_timestamp(btc.id, PROVIDER_TIMEFRAME)
                asset_status["BTCUSD"] = {
                    "status": "healthy",
                    "last_candle": latest_btc.isoformat() if latest_btc else None,
                    "candles_upserted": extra_count,
                    "catchup_passes": BTC_CATCHUP_MAX_EXTRA_PASSES,
                }

        s.update_settings(BULK_BOOTSTRAP_KEY, now.isoformat(), description="Last bulk fetch run")

        _finalize_worker_run(
            s,
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
        with session_scope() as session:
            _run(TradingStore(session))


def fetch_data_job(store: TradingStore | None = None) -> None:
    """Backward-compatible alias — runs live ingest only."""
    fetch_live_job(store)
