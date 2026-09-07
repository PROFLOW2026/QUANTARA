"""Fetch market data via configured provider."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from quantara_engine.core.config import settings
from quantara_engine.db.session import session_scope
from quantara_engine.market_data.adapters.twelvedata import TwelveDataError
from quantara_engine.market_data.factory import get_market_data_provider
from quantara_engine.market_data.polling import (
    STRATEGY_MIN_CANDLES,
    TIMEFRAMES,
    should_fetch_timeframe,
)
from quantara_engine.market_data.validation import validate_candle
from quantara_engine.persistence.store import TradingStore

logger = logging.getLogger(__name__)


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

        instrument = s.get_instrument_by_symbol("XAUUSD")
        if not instrument:
            logger.warning("XAUUSD instrument not found in DB — run seed first")
            return

        count = 0
        errors: list[str] = []
        now = datetime.now(timezone.utc)

        for timeframe in TIMEFRAMES:
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
                    continue
            except TwelveDataError as exc:
                msg = f"{timeframe}: {exc}"
                logger.error("Twelve Data fetch failed — %s", msg)
                errors.append(msg)
                continue
            except Exception as exc:
                msg = f"{timeframe}: {exc}"
                logger.exception("Market data fetch failed — %s", msg)
                errors.append(msg)
                continue

            for candle in candles:
                try:
                    validate_candle(candle)
                except Exception as exc:
                    logger.warning("Invalid candle skipped: %s", exc)
                    continue
                s.upsert_candle(candle)
                count += 1

        status = "healthy" if not errors else ("degraded" if count else "error")
        s.update_worker_status(
            "data_fetcher",
            {
                "status": status,
                "last_run": started_at.isoformat(),
                "candles_upserted": count,
                "provider": settings.market_data_provider,
                "errors": errors or None,
            },
        )
        s.save_worker_run(
            run_id=str(uuid.uuid4()),
            worker_name="data_fetcher",
            started_at=started_at,
            jobs_processed=count,
        )
        logger.info(
            "fetch_data completed provider=%s upserted=%d errors=%d",
            settings.market_data_provider,
            count,
            len(errors),
        )

    if store is not None:
        _run(store)
    else:
        with session_scope() as session:
            _run(TradingStore(session))