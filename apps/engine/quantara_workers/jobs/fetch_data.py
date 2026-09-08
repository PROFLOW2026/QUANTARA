"""Fetch market data via provider registry for all target assets."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from quantara_engine.core.config import settings
from quantara_engine.db.session import session_scope
from quantara_engine.market_data.adapters.alpaca import AlpacaError
from quantara_engine.market_data.adapters.tiingo import TiingoError
from quantara_engine.market_data.adapters.twelvedata import TwelveDataError
from quantara_engine.market_data.aggregation import (
    DERIVED_FROM_5M,
    aggregate_from_5m,
    derivation_source_limit,
)
from quantara_engine.market_data.credits import FetchPriority, status_payload as twelve_status
from quantara_engine.market_data.factory import get_provider_for_asset
from quantara_engine.market_data.polling import PROVIDER_TIMEFRAME, STRATEGY_MIN_CANDLES, should_fetch_timeframe
from quantara_engine.market_data.provider_budgets import all_provider_status
from quantara_engine.market_data.registry import AssetClass, ProviderName, list_target_assets
from quantara_engine.market_data.spot_price import update_spot_from_latest_5m
from quantara_engine.market_data.validation import validate_candle
from quantara_engine.persistence.store import TradingStore

logger = logging.getLogger(__name__)

TIINGO_POLL_INTERVAL_MINUTES = 12
LAST_FETCH_KEY = "provider_budget:tiingo:last_fetch"


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


def _should_poll_asset(
    store: TradingStore,
    asset,
    *,
    now: datetime,
    stored: int,
    force_bootstrap: bool,
) -> tuple[bool, str | None]:
    if force_bootstrap:
        return True, None
    if asset.primary_provider != ProviderName.TIINGO:
        return True, None
    last_poll = _tiingo_last_fetch(store, asset.db_symbol)
    if last_poll:
        elapsed = (now - last_poll).total_seconds() / 60
        if elapsed < TIINGO_POLL_INTERVAL_MINUTES:
            return False, f"deferred (Tiingo poll interval {TIINGO_POLL_INTERVAL_MINUTES}m)"
    from quantara_engine.market_data.provider_budgets import can_request

    if not can_request(store, "tiingo"):
        return False, "deferred (Tiingo hourly budget)"
    return True, None


def _aggregation_mode(asset) -> str:
    if asset.asset_class in (AssetClass.STOCK, AssetClass.INDEX):
        return "us_rth"
    return "utc"


def _derive_and_store_higher_timeframes(
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


def _fetch_asset(
    store: TradingStore,
    instrument,
    asset,
    now: datetime,
) -> tuple[int, int, str | None]:
    try:
        provider = get_provider_for_asset(asset)
    except ValueError as exc:
        return 0, 0, str(exc)

    if hasattr(provider, "bind_context"):
        provider.bind_context(  # type: ignore[attr-defined]
            store=store,
            caller="fetch_data_job",
            asset=asset,
            priority=FetchPriority.SCHEDULED,
        )

    timeframe = PROVIDER_TIMEFRAME
    last_ts = store.latest_candle_timestamp(instrument.id, timeframe)
    stored = store.count_candles(instrument.id, timeframe)
    count = 0
    error: str | None = None
    force_bootstrap = stored < STRATEGY_MIN_CANDLES

    should_poll, defer_reason = _should_poll_asset(
        store, asset, now=now, stored=stored, force_bootstrap=force_bootstrap
    )
    if not should_poll:
        derived = 0
        if stored >= STRATEGY_MIN_CANDLES:
            derived = _derive_and_store_higher_timeframes(
                store,
                instrument.id,
                session_mode=_aggregation_mode(asset),
            )
        return 0, derived, defer_reason

    try:
        if force_bootstrap and hasattr(provider, "fetch_bootstrap"):
            candles = provider.fetch_bootstrap(instrument.id, timeframe)  # type: ignore[attr-defined]
        elif should_fetch_timeframe(timeframe, last_ts, now):
            candles = provider.fetch_latest(instrument.id, timeframe, since=last_ts)
        else:
            candles = []
    except (TwelveDataError, AlpacaError, TiingoError) as exc:
        error = f"{asset.db_symbol}: {exc}"
        logger.error("Fetch failed for %s — %s", asset.db_symbol, exc)
        candles = []
    except Exception as exc:
        error = f"{asset.db_symbol}: {exc}"
        logger.exception("Fetch failed for %s", asset.db_symbol)
        candles = []

    for candle in candles:
        try:
            validate_candle(candle)
        except Exception as exc:
            logger.warning("Invalid candle skipped (%s): %s", asset.db_symbol, exc)
            continue
        store.upsert_candle(candle)
        count += 1

    stored_after = store.count_candles(instrument.id, timeframe)
    if (
        force_bootstrap
        and stored_after < STRATEGY_MIN_CANDLES
        and asset.secondary_provider is not None
    ):
        try:
            secondary = get_provider_for_asset(asset, role="secondary")
            if hasattr(secondary, "bind_context"):
                secondary.bind_context(  # type: ignore[attr-defined]
                    store=store,
                    caller="fetch_data_job:bootstrap_secondary",
                    asset=asset,
                    priority=FetchPriority.CATCH_UP,
                )
            extra = secondary.fetch_bootstrap(instrument.id, timeframe)  # type: ignore[attr-defined]
            for candle in extra:
                try:
                    validate_candle(candle)
                except Exception as exc:
                    logger.warning("Invalid secondary candle skipped (%s): %s", asset.db_symbol, exc)
                    continue
                store.upsert_candle(candle)
                count += 1
        except (TwelveDataError, AlpacaError, TiingoError) as exc:
            logger.warning("Secondary bootstrap failed for %s — %s", asset.db_symbol, exc)

    if asset.primary_provider == ProviderName.TIINGO and count:
        _mark_tiingo_fetch(store, asset.db_symbol, now)

    derived = 0
    if count or stored >= STRATEGY_MIN_CANDLES:
        derived = _derive_and_store_higher_timeframes(
            store,
            instrument.id,
            session_mode=_aggregation_mode(asset),
        )
    if asset.db_symbol == "XAUUSD":
        update_spot_from_latest_5m(store, instrument.id)
    return count, derived, error


BTC_CATCHUP_FRESH_MINUTES = 30
BTC_CATCHUP_MAX_EXTRA_PASSES = 2


def _accelerate_btc_catchup(
    store: TradingStore,
    instrument,
    asset,
    now: datetime,
) -> tuple[int, int]:
    """Run extra Alpaca pagination passes when BTC 5m data is still stale."""
    if asset.db_symbol != "BTCUSD":
        return 0, 0

    from quantara_engine.market_data.provider_budgets import can_request

    total_count = 0
    total_derived = 0
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
                caller="fetch_data_job:btc_catchup",
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
            pass_count += 1

        if pass_count == 0:
            break

        total_count += pass_count
        derived = _derive_and_store_higher_timeframes(
            store,
            instrument.id,
            session_mode=_aggregation_mode(asset),
        )
        total_derived += derived
        logger.info("BTC catch-up pass %d upserted %d 5m bars", pass_num + 1, pass_count)

    return total_count, total_derived


def fetch_data_job(store: TradingStore | None = None) -> None:
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
                errors.append(f"{asset.db_symbol}: instrument missing — run seed_8_assets.py")
                asset_status[asset.db_symbol] = {"status": "error", "error": "missing instrument"}
                continue

            count, derived, error = _fetch_asset(s, instrument, asset, now)
            total_count += count
            total_derived += derived
            latest = s.latest_candle_timestamp(instrument.id, PROVIDER_TIMEFRAME)
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
            else:
                asset_status[asset.db_symbol] = {
                    "status": "healthy" if latest else "stale",
                    "provider": asset.primary_provider.value,
                    "last_candle": latest.isoformat() if latest else None,
                    "candles_upserted": count,
                }

        btc = s.get_instrument_by_symbol("BTCUSD")
        btc_asset = next((a for a in list_target_assets() if a.db_symbol == "BTCUSD"), None)
        if btc and btc_asset:
            extra_count, extra_derived = _accelerate_btc_catchup(s, btc, btc_asset, now)
            if extra_count:
                total_count += extra_count
                total_derived += extra_derived
                latest_btc = s.latest_candle_timestamp(btc.id, PROVIDER_TIMEFRAME)
                prev = asset_status.get("BTCUSD") or {}
                asset_status["BTCUSD"] = {
                    **prev,
                    "status": "healthy" if latest_btc and (now - latest_btc).total_seconds() < BTC_CATCHUP_FRESH_MINUTES * 60 else prev.get("status", "stale"),
                    "last_candle": latest_btc.isoformat() if latest_btc else prev.get("last_candle"),
                    "candles_upserted": int(prev.get("candles_upserted") or 0) + extra_count,
                    "catchup_passes": BTC_CATCHUP_MAX_EXTRA_PASSES,
                }

        credit_status = twelve_status(s)
        provider_status = all_provider_status(s)
        status = "healthy" if not errors else ("degraded" if (total_count or total_derived) else "error")
        worker_payload: dict = {
            "status": status,
            "last_run": started_at.isoformat(),
            "candles_upserted": total_count,
            "derived_candles_upserted": total_derived,
            "provider": "multi",
            "provider_base_timeframe": PROVIDER_TIMEFRAME,
            "errors": errors or None,
            "credits": credit_status,
            "providers": provider_status,
            "assets": asset_status,
        }
        s.update_worker_status("data_fetcher", worker_payload)
        s.save_worker_run(
            run_id=str(uuid.uuid4()),
            worker_name="data_fetcher",
            started_at=started_at,
            jobs_processed=total_count + total_derived,
        )
        logger.info(
            "fetch_data multi-provider completed base=%d derived=%d errors=%d",
            total_count,
            total_derived,
            len(errors),
        )

    if store is not None:
        _run(store)
    else:
        with session_scope() as session:
            _run(TradingStore(session))
