"""Tiingo market data adapter (IEX US equities + crypto)."""

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
from quantara_engine.market_data.polling import (
    BOOTSTRAP_OUTPUT_SIZE,
    PROVIDER_TIMEFRAME,
    is_bar_complete,
)
from quantara_engine.market_data.provider_budgets import (
    FetchPriority,
    can_request,
    record_request,
)
from quantara_engine.market_data.registry import AssetClass, AssetDefinition, ProviderName, provider_symbol
from quantara_engine.market_data.sessions import is_us_equity_rth
from quantara_engine.persistence.store import TradingStore

logger = logging.getLogger(__name__)

RESAMPLE_MAP = {
    "5m": "5min",
    "15m": "15min",
    "1h": "1hour",
    "1m": "1min",
}


class TiingoError(Exception):
    pass


class TiingoMarketDataProvider:
    source = "tiingo"

    def __init__(
        self,
        *,
        store: TradingStore | None = None,
        caller: str = "tiingo",
        priority: FetchPriority = FetchPriority.SCHEDULED,
        asset: AssetDefinition | None = None,
    ) -> None:
        self.api_key = settings.tiingo_api_key.strip()
        if not self.api_key:
            raise TiingoError("TIINGO_API_KEY is not configured")
        self._store = store
        self._caller = caller
        self._priority = priority
        self._asset = asset

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
        self._asset = asset
        self._priority = priority

    def _headers(self) -> dict[str, str]:
        return {
            "Accept": "application/json",
            "Authorization": f"Token {self.api_key}",
            "Content-Type": "application/json",
        }

    def _request(
        self,
        url: str,
        symbol: str,
        *,
        purpose: str = "candles",
        count: int = 1,
    ) -> list[dict[str, Any]] | dict[str, Any]:
        if not can_request(self._store, self.source, count=count, purpose=purpose):
            raise TiingoError(f"Tiingo budget blocked request for {symbol}")
        req = urllib.request.Request(url, headers=self._headers())
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                payload = json.loads(resp.read().decode())
            record_request(
                self._store,
                self.source,
                symbol=symbol,
                caller=self._caller,
                count=count,
                success=True,
            )
            return payload
        except urllib.error.HTTPError as exc:
            body = exc.read().decode(errors="replace")[:200]
            record_request(
                self._store,
                self.source,
                symbol=symbol,
                caller=self._caller,
                count=count,
                success=False,
                error=f"HTTP {exc.code}: {body}",
            )
            raise TiingoError(f"HTTP {exc.code}: {body}") from exc
        except urllib.error.URLError as exc:
            record_request(
                self._store,
                self.source,
                symbol=symbol,
                caller=self._caller,
                count=count,
                success=False,
                error=str(exc.reason),
            )
            raise TiingoError(f"Network error: {exc.reason}") from exc

    def _provider_ticker(self) -> str:
        if self._asset is None:
            raise TiingoError("Asset context required for Tiingo fetch")
        return provider_symbol(self._asset, ProviderName.TIINGO)

    def _fetch_rows(self, timeframe: str, start: datetime, limit: int) -> list[dict[str, Any]]:
        symbol = self._provider_ticker()
        freq = RESAMPLE_MAP.get(timeframe)
        if not freq:
            raise TiingoError(f"Unsupported timeframe: {timeframe}")

        if self._asset and self._asset.asset_class == AssetClass.CRYPTO:
            params = {
                "tickers": symbol,
                "startDate": start.strftime("%Y-%m-%d"),
                "resampleFreq": freq,
            }
            url = "https://api.tiingo.com/tiingo/crypto/prices?" + urllib.parse.urlencode(params)
        elif self._asset and self._asset.asset_class in (AssetClass.FOREX, AssetClass.COMMODITY):
            params = {
                "startDate": start.strftime("%Y-%m-%d"),
                "resampleFreq": freq,
            }
            url = f"https://api.tiingo.com/tiingo/fx/{symbol.lower()}/prices?" + urllib.parse.urlencode(params)
        else:
            params = {
                "startDate": start.strftime("%Y-%m-%d"),
                "resampleFreq": freq,
            }
            url = f"https://api.tiingo.com/iex/{symbol}/prices?" + urllib.parse.urlencode(params)

        payload = self._request(url, symbol, purpose="candles")
        if isinstance(payload, list):
            if self._asset and self._asset.asset_class == AssetClass.CRYPTO:
                rows: list[dict[str, Any]] = []
                for item in payload:
                    if not isinstance(item, dict):
                        continue
                    nested = item.get("priceData")
                    if isinstance(nested, list):
                        rows.extend(n for n in nested if isinstance(n, dict))
                    elif "date" in item or "datetime" in item:
                        rows.append(item)
                return rows[-limit:]
            return payload[-limit:]
        return []

    def fetch_fx_top_quote(self, ticker: str | None = None) -> dict[str, Decimal] | None:
        """Latest FX bid/ask/mid from Tiingo /tiingo/fx/top (one request, cached by caller)."""
        symbol = (ticker or self._provider_ticker()).lower()
        url = f"https://api.tiingo.com/tiingo/fx/top?tickers={urllib.parse.quote(symbol)}"
        payload = self._request(url, symbol, purpose="fx_rate")
        rows = payload if isinstance(payload, list) else []
        if not rows:
            return None
        row = rows[0]
        try:
            bid = Decimal(str(row["bidPrice"]))
            ask = Decimal(str(row["askPrice"]))
            mid = Decimal(str(row.get("midPrice") or (bid + ask) / 2))
            return {"bid": bid, "ask": ask, "mid": mid}
        except (KeyError, InvalidOperation, TypeError, ValueError):
            return None

    def fetch_fx_latest_close(self, ticker: str) -> Decimal | None:
        """Latest completed FX close from intraday prices (USDJPY conversion helper)."""
        start = datetime.now(timezone.utc) - timedelta(days=2)
        params = {
            "startDate": start.strftime("%Y-%m-%d"),
            "resampleFreq": "5min",
        }
        url = f"https://api.tiingo.com/tiingo/fx/{ticker.lower()}/prices?" + urllib.parse.urlencode(params)
        rows = self._request(url, ticker, purpose="fx_rate")
        if not isinstance(rows, list) or not rows:
            return None
        try:
            return Decimal(str(rows[-1]["close"]))
        except (KeyError, InvalidOperation, TypeError, ValueError):
            return None

    def fetch_latest_crypto_batch(
        self,
        items: list[tuple[AssetDefinition, str, datetime | None]],
        timeframe: str,
    ) -> dict[str, list[Candle]]:
        """Fetch multiple crypto tickers in one Tiingo request when possible."""
        if timeframe != PROVIDER_TIMEFRAME:
            raise TiingoError(f"Tiingo fetch blocked for {timeframe}; use {PROVIDER_TIMEFRAME}")
        if not items:
            return {}

        tickers: list[str] = []
        ticker_to_instrument: dict[str, str] = {}
        since_map: dict[str, datetime | None] = {}
        for asset, instrument_id, since in items:
            ticker = provider_symbol(asset, ProviderName.TIINGO)
            tickers.append(ticker)
            ticker_to_instrument[ticker.lower()] = instrument_id
            since_map[ticker.lower()] = since

        earliest = datetime.now(timezone.utc) - timedelta(days=5)
        for since in since_map.values():
            if since is not None:
                candidate = since - timedelta(days=1)
                if candidate.tzinfo is None:
                    candidate = candidate.replace(tzinfo=timezone.utc)
                earliest = min(earliest, candidate)

        params = {
            "tickers": ",".join(tickers),
            "startDate": earliest.strftime("%Y-%m-%d"),
            "resampleFreq": RESAMPLE_MAP[timeframe],
        }
        url = "https://api.tiingo.com/tiingo/crypto/prices?" + urllib.parse.urlencode(params)
        batch_label = ",".join(tickers)
        payload = self._request(url, batch_label, purpose="candles", count=1)
        if not isinstance(payload, list):
            return {}

        out: dict[str, list[Candle]] = {}
        for item in payload:
            if not isinstance(item, dict):
                continue
            ticker = str(item.get("ticker") or "").lower()
            instrument_id = ticker_to_instrument.get(ticker)
            if not instrument_id:
                continue
            nested = item.get("priceData")
            rows = nested if isinstance(nested, list) else []
            candles = self._to_candles(
                [r for r in rows if isinstance(r, dict)],
                instrument_id,
                timeframe,
                since=since_map.get(ticker),
            )
            if candles:
                out[instrument_id] = candles
        return out

    def _parse_row(self, row: dict[str, Any], instrument_id: str, timeframe: str) -> Candle | None:
        try:
            raw_ts = row.get("date") or row.get("datetime")
            if raw_ts is None:
                return None
            ts = datetime.fromisoformat(str(raw_ts).replace("Z", "+00:00"))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            open_ = Decimal(str(row.get("open") or row.get("o")))
            high = Decimal(str(row.get("high") or row.get("h")))
            low = Decimal(str(row.get("low") or row.get("l")))
            close = Decimal(str(row.get("close") or row.get("c")))
            volume_raw = row.get("volume") or row.get("v")
            volume = Decimal(str(volume_raw)) if volume_raw not in (None, "") else None
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
                is_complete=is_bar_complete(ts, timeframe),
            )
        except (KeyError, InvalidOperation, ValueError, TypeError) as exc:
            logger.warning("Skipping malformed Tiingo row: %s", exc)
            return None

    def _to_candles(
        self,
        rows: list[dict[str, Any]],
        instrument_id: str,
        timeframe: str,
        *,
        since: datetime | None = None,
    ) -> list[Candle]:
        candles: list[Candle] = []
        for row in rows:
            candle = self._parse_row(row, instrument_id, timeframe)
            if candle is None or not candle.is_complete:
                continue
            if self._asset and self._asset.asset_class == AssetClass.STOCK:
                if not is_us_equity_rth(candle.timestamp):
                    continue
            if since and candle.timestamp <= since:
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
        if timeframe != PROVIDER_TIMEFRAME:
            raise TiingoError(f"Tiingo fetch blocked for {timeframe}; use {PROVIDER_TIMEFRAME}")
        start = (since or datetime.now(timezone.utc) - timedelta(days=5)).replace(tzinfo=timezone.utc)
        if since:
            start = since - timedelta(days=1)
        rows = self._fetch_rows(timeframe, start, limit=120)
        return self._to_candles(rows, instrument_id, timeframe, since=since)

    def fetch_bootstrap(self, instrument_id: str, timeframe: str, bars: int = BOOTSTRAP_OUTPUT_SIZE) -> list[Candle]:
        # IEX API returns full history from startDate; keep the most recent rows for 1h derivation.
        start = datetime.now(timezone.utc) - timedelta(days=365)
        rows = self._fetch_rows(timeframe, start, limit=max(bars, 20000))
        return self._to_candles(rows, instrument_id, timeframe)

    def fetch_range(
        self,
        instrument_id: str,
        timeframe: str,
        start: datetime,
        end: datetime,
    ) -> list[Candle]:
        rows = self._fetch_rows(timeframe, start, limit=5000)
        candles = self._to_candles(rows, instrument_id, timeframe)
        return [c for c in candles if start <= c.timestamp <= end]

    def generate_candles(
        self,
        instrument_id: str,
        timeframe: str,
        count: int,
        start: datetime | None = None,
    ) -> list[Candle]:
        raise NotImplementedError("Tiingo does not synthesize candles")
