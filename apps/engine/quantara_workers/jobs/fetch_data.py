"""Fetch market data via configured provider."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from quantara_engine.core.config import settings
from quantara_engine.db.session import session_scope
from quantara_engine.market_data.adapters.twelvedata import TwelveDataError
from quantara_engine.market_data.aggregation import (
    DERIVED_FROM_5M,
    aggregate_from_5m,
    aggregation_lookback_bars,
)
from quantara_engine.market_data.credits import FetchPriority, status_payload
from quantara_engine.market_data.factory import get_market_data_provider
from quantara_engine.market_data.polling import (
    PROVIDER_TIMEFRAME,
    STRATEGY_MIN_CANDLES,
    should_fetch_timeframe,
)
from quantara_engine.market_data.spot_price import update_spot_from_latest_5m
from quantara_engine.market_data.validation import validate_candle
from quantara_engine.persistence.store import TradingStore

logger = logging.getLogger(__name__)


def _derive_and_store_higher_timeframes(
    store: TradingStore,
    instrument_id: str,
) -> int:
    """Rebuild recent derived candles from persisted 5m bars."""
    derived_count = 0
    lookback = max(aggregation_lookback_bars(tf) for tf in DERIVED_FROM_5M)
    base_rows = store.list_recent_candles(instrument_id, PROVIDER_TIMEFRAME, limit=lookback)
    if not base_rows:
        return 0

    for target_tf in DERIVED_FROM_5M:
        for candle in aggregate_from_5m(base_rows, target_tf):
            try:
                validate_candle(candle)
            except Exception as exc:
                logger.warning("Invalid derived candle skipped: %s", exc)
                continue
            store.upsert_candle(candle)
            derived_count += 1
    return derived_count


def fetch_data_job(store: TradingStore | None = None) -> None:
    started_at = datetime.now(timezone.utc)

    def _run(s: TradingStore) -> None:
        try:
            provider = get_market_data_provider()
        except ValueError as exc:
            logger.error("Market data provider misconfigured: %s", exc)
            s.update_worker_status(
                "data_fetcher",
                {"status": "error", "last_run": started_at.isoformat(), "error": str(exc)},
            )
            return

        if hasattr(provider, "bind_context"):
            provider.bind_context(  # type: ignore[attr-defined]
                store=s,
                caller="fetch_data_job",
                priority=FetchPriority.SCHEDULED,
            )

        instrument = s.get_instrument_by_symbol("XAUUSD")
        if not instrument:
            logger.warning("XAUUSD instrument not found in DB — run seed first")
            return

        count = 0
        derived_count = 0
        errors: list[str] = []
        now = datetime.now(timezone.utc)
        timeframe = PROVIDER_TIMEFRAME

        last_ts = s.latest_candle_timestamp(instrument.id, timeframe)
        stored = s.count_candles(instrument.id, timeframe)

        try:
            if stored < STRATEGY_MIN_CANDLES and hasattr(provider, "fetch_bootstrap"):
                candles = provider.fetch_bootstrap(instrument.id, timeframe)  # type: ignore[attr-defined]
            elif should_fetch_timeframe(timeframe, last_ts, now):
                candles = provider.fetch_latest(
                    instrument.id, timeframe, since=last_ts
                )
            else:
                candles = []
        except TwelveDataError as exc:
            msg = f"{timeframe}: {exc}"
            logger.error("Twelve Data fetch failed — %s", msg)
            errors.append(msg)
            candles = []
        except Exception as exc:
            msg = f"{timeframe}: {exc}"
            logger.exception("Market data fetch failed — %s", msg)
            errors.append(msg)
            candles = []

        for candle in candles:
            try:
                validate_candle(candle)
            except Exception as exc:
                logger.warning("Invalid candle skipped: %s", exc)
                continue
            s.upsert_candle(candle)
            count += 1

        if count or stored >= STRATEGY_MIN_CANDLES:
            derived_count = _derive_and_store_higher_timeframes(s, instrument.id)

        update_spot_from_latest_5m(s, instrument.id)

        credit_status = status_payload(s)
        status = "healthy" if not errors else ("degraded" if (count or derived_count) else "error")
        worker_payload: dict = {
            "status": status,
            "last_run": started_at.isoformat(),
            "candles_upserted": count,
            "derived_candles_upserted": derived_count,
            "provider": settings.market_data_provider,
            "provider_base_timeframe": PROVIDER_TIMEFRAME,
            "errors": errors or None,
            "credits": credit_status,
        }
        s.update_worker_status("data_fetcher", worker_payload)
        s.save_worker_run(
            run_id=str(uuid.uuid4()),
            worker_name="data_fetcher",
            started_at=started_at,
            jobs_processed=count + derived_count,
        )
        logger.info(
            "fetch_data completed provider=%s base_upserted=%d derived_upserted=%d errors=%d credits_used=%s",
            settings.market_data_provider,
            count,
            derived_count,
            len(errors),
            credit_status.get("used_today"),
        )

    if store is not None:
        _run(store)
    else:
        with session_scope() as session:
            _run(TradingStore(session))
