"""Twelve Data market data adapter (REST time series)."""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from quantara_engine.core.config import settings
from quantara_engine.domain.types import Candle
from quantara_engine.market_data.credits import (
    FetchPriority,
    can_fetch,
    credits_for_endpoint,
    record_usage,
    sync_provider_usage,
)
from quantara_engine.market_data.polling import (
    BOOTSTRAP_OUTPUT_SIZE,
    PROVIDER_TIMEFRAME,
    is_bar_complete,
    timeframe_minutes,
)
from quantara_engine.market_data.symbols import TWELVEDATA_XAUUSD
from quantara_engine.market_data.registry import AssetDefinition, provider_symbol, ProviderName
from quantara_engine.persistence.store import TradingStore

logger = logging.getLogger(__name__)

BASE_URL = "https://api.twelvedata.com"

TIMEFRAME_TO_INTERVAL: dict[str, str] = {
    "5m": "5min",
    "15m": "15min",
    "1h": "1h",
}


class TwelveDataError(Exception):
    """Twelve Data API error."""

    def __init__(self, message: str, *, code: int | None = None) -> None:
        super().__init__(message)
        self.code = code


class TwelveDataMarketDataProvider:
    """Fetch OHLCV from Twelve Data REST API; maps provider symbol to canonical XAU/USD."""

    source = "twelvedata"
    provider_symbol = TWELVEDATA_XAUUSD

    def __init__(
        self,
        api_key: str | None = None,
        *,
        store: TradingStore | None = None,
        caller: str = "twelvedata",
        priority: FetchPriority = FetchPriority.SCHEDULED,
        allow_non_canonical_timeframes: bool = False,
        asset: AssetDefinition | None = None,
    ) -> None:
        self.api_key = (api_key or settings.market_data_api_key).strip()
        if not self.api_key:
            raise TwelveDataError("MARKET_DATA_API_KEY is not configured")
        self._store = store
        self._caller = caller
        self._priority = priority
        self._allow_non_canonical_timeframes = allow_non_canonical_timeframes
        self._asset = asset
        if asset is not None:
            self.provider_symbol = provider_symbol(asset, ProviderName.TWELVE_DATA)
        else:
            self.provider_symbol = TWELVEDATA_XAUUSD

    def bind_context(
        self,
        *,
        store: TradingStore | None,
        caller: str,
        asset: AssetDefinition | None = None,
        priority: FetchPriority = FetchPriority.SCHEDULED,
    ) -> None:
        self._store = store
        self._caller = caller
        self._priority = priority
        if asset is not None:
            self._asset = asset
            self.provider_symbol = provider_symbol(asset, ProviderName.TWELVE_DATA)

    def _ensure_canonical_timeframe(self, timeframe: str) -> None:
        if timeframe != PROVIDER_TIMEFRAME and not self._allow_non_canonical_timeframes:
            raise TwelveDataError(
                f"Provider fetch blocked for {timeframe}; use canonical {PROVIDER_TIMEFRAME} + local aggregation"
            )

    def _interval(self, timeframe: str) -> str:
        interval = TIMEFRAME_TO_INTERVAL.get(timeframe)
        if not interval:
            raise TwelveDataError(f"Unsupported timeframe: {timeframe}")
        return interval

    def _request(self, endpoint: str, params: dict[str, Any]) -> dict[str, Any]:
        if endpoint != "api_usage" and not can_fetch(self._store, self._priority):
            raise TwelveDataError(
                f"Credit guard blocked {endpoint} for caller={self._caller} "
                f"(priority={self._priority.name})"
            )

        query = {**params, "apikey": self.api_key}
        url = f"{BASE_URL}/{endpoint}?" + urllib.parse.urlencode(query)
        req = urllib.request.Request(
            url,
            headers={
                "Accept": "application/json",
                "Authorization": f"apikey {self.api_key}",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                payload = json.loads(resp.read().decode())
        except urllib.error.HTTPError as exc:
            body = exc.read().decode(errors="replace")
            try:
                payload = json.loads(body)
                message = payload.get("message") or body[:200]
            except json.JSONDecodeError:
                message = body[:200] or str(exc)
            raise TwelveDataError(
                f"HTTP {exc.code}: {message}",
                code=exc.code,
            ) from exc
        except urllib.error.URLError as exc:
            raise TwelveDataError(f"Network error: {exc.reason}") from exc

        if payload.get("status") == "error":
            raise TwelveDataError(
                payload.get("message") or "Unknown Twelve Data error",
                code=payload.get("code"),
            )

        credits = credits_for_endpoint(endpoint)
        if credits:
            record_usage(
                self._store,
                endpoint=endpoint,
                symbol=str(params.get("symbol") or self.provider_symbol),
                interval=str(params.get("interval")) if params.get("interval") else None,
                caller=self._caller,
                credits=credits,
            )
        if endpoint == "api_usage":
            sync_provider_usage(self._store, payload)

        return payload

    def fetch_api_usage(self) -> dict[str, Any]:
        return self._request("api_usage", {})

    def fetch_quote(self) -> dict[str, Any]:
        """Latest quote for XAU/USD (1 API credit). Avoid in scheduled ingestion."""
        return self._request("quote", {"symbol": self.provider_symbol})

    def fetch_price(self) -> Decimal:
        """Latest price endpoint fallback (1 API credit)."""
        data = self._request("price", {"symbol": self.provider_symbol})
        return Decimal(str(data["price"]))

    def _time_series(
        self,
        timeframe: str,
        *,
        outputsize: int | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
    ) -> list[dict[str, Any]]:
        self._ensure_canonical_timeframe(timeframe)
        params: dict[str, Any] = {
            "symbol": self.provider_symbol,
            "interval": self._interval(timeframe),
            "timezone": "UTC",
            "order": "ASC",
        }
        if outputsize is not None:
            params["outputsize"] = outputsize
        if start_date is not None:
            params["start_date"] = start_date.strftime("%Y-%m-%d %H:%M:%S")
        if end_date is not None:
            params["end_date"] = end_date.strftime("%Y-%m-%d %H:%M:%S")

        data = self._request("time_series", params)
        return list(data.get("values") or [])

    def _parse_row(
        self,
        row: dict[str, Any],
        instrument_id: str,
        timeframe: str,
    ) -> Candle | None:
        try:
            ts = datetime.strptime(row["datetime"], "%Y-%m-%d %H:%M:%S").replace(
                tzinfo=timezone.utc
            )
            open_ = Decimal(str(row["open"]))
            high = Decimal(str(row["high"]))
            low = Decimal(str(row["low"]))
            close = Decimal(str(row["close"]))
            volume_raw = row.get("volume")
            volume = Decimal(str(volume_raw)) if volume_raw not in (None, "") else None
        except (KeyError, InvalidOperation, ValueError) as exc:
            logger.warning("Skipping malformed Twelve Data row: %s", exc)
            return None

        complete = is_bar_complete(ts, timeframe)
        return Candle(
            instrument_id=instrument_id,
            timeframe=timeframe,
            timestamp=ts,
            open=open_,
            high=high,
            low=low,
            close=close,
            volume=volume,
            source=self.source,
            is_complete=complete,
        )

    def _rows_to_candles(
        self,
        rows: list[dict[str, Any]],
        instrument_id: str,
        timeframe: str,
        *,
        closed_only: bool = True,
    ) -> list[Candle]:
        candles: list[Candle] = []
        for row in rows:
            candle = self._parse_row(row, instrument_id, timeframe)
            if candle is None:
                continue
            if closed_only and not candle.is_complete:
                continue
            candles.append(candle)
        candles.sort(key=lambda c: c.timestamp)
        return candles

    def fetch_latest(
        self,
        instrument_id: str,
        timeframe: str,
        since: datetime | None = None,
    ) -> list[Candle]:
        """Fetch recent closed bars; uses one API call per invocation."""
        self._ensure_canonical_timeframe(timeframe)
        if since is not None:
            if since.tzinfo is None:
                since = since.replace(tzinfo=timezone.utc)
            end = datetime.now(timezone.utc)
            rows = self._time_series(
                timeframe,
                start_date=since - timedelta(minutes=timeframe_minutes(timeframe)),
                end_date=end,
            )
        else:
            rows = self._time_series(timeframe, outputsize=min(30, BOOTSTRAP_OUTPUT_SIZE))

        candles = self._rows_to_candles(rows, instrument_id, timeframe, closed_only=True)
        if since is not None:
            candles = [c for c in candles if c.timestamp > since]
        return candles

    def fetch_range(
        self,
        instrument_id: str,
        timeframe: str,
        start: datetime,
        end: datetime,
    ) -> list[Candle]:
        self._ensure_canonical_timeframe(timeframe)
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        if end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)

        rows = self._time_series(timeframe, start_date=start, end_date=end)
        return self._rows_to_candles(rows, instrument_id, timeframe, closed_only=True)

    def generate_candles(
        self,
        instrument_id: str,
        timeframe: str,
        count: int,
        start: datetime | None = None,
    ) -> list[Candle]:
        raise NotImplementedError(
            "Twelve Data does not synthesize candles; use fetch_range or fetch_latest"
        )

    def fetch_bootstrap(
        self,
        instrument_id: str,
        timeframe: str,
        bars: int = BOOTSTRAP_OUTPUT_SIZE,
    ) -> list[Candle]:
        """Historical bootstrap via single time_series call."""
        self._ensure_canonical_timeframe(timeframe)
        rows = self._time_series(timeframe, outputsize=bars)
        return self._rows_to_candles(rows, instrument_id, timeframe, closed_only=True)
