"""Coinbase Exchange public market data (no API key required)."""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from quantara_engine.domain.types import Candle
from quantara_engine.market_data.polling import (
    BOOTSTRAP_OUTPUT_SIZE,
    PROVIDER_TIMEFRAME,
    is_bar_complete,
    timeframe_minutes,
)
from quantara_engine.market_data.provider_budgets import FetchPriority, can_request, record_request
from quantara_engine.market_data.registry import AssetDefinition
from quantara_engine.persistence.store import TradingStore

logger = logging.getLogger(__name__)

COINBASE_BASE = "https://api.exchange.coinbase.com"
GRANULARITY_MAP = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600}
_COINBASE_HEADERS = {
    "Accept": "application/json",
    "User-Agent": "QUANTARA-Engine/1.0 (+https://github.com/PROFLOW2026/QUANTARA)",
}


class CoinbaseError(Exception):
    pass


class CoinbaseMarketDataProvider:
    source = "coinbase"

    def __init__(
        self,
        *,
        store: TradingStore | None = None,
        caller: str = "coinbase",
        priority: FetchPriority = FetchPriority.SCHEDULED,
        asset: AssetDefinition | None = None,
    ) -> None:
        self._store = store
        self._caller = caller
        self._priority = priority
        self._asset = asset

    def _product_id(self) -> str:
        if not self._asset:
            raise CoinbaseError("asset required for Coinbase provider")
        sym = self._asset.provider_symbols.get("coinbase")
        if sym:
            return sym
        canonical = self._asset.canonical_symbol.replace("/", "-")
        return canonical

    def fetch_candles(
        self,
        instrument_id: str,
        timeframe: str,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int = BOOTSTRAP_OUTPUT_SIZE,
    ) -> list[Candle]:
        if self._store and not can_request(self._store, "coinbase", count=1):
            raise CoinbaseError("Coinbase budget/cooldown blocked")

        granularity = GRANULARITY_MAP.get(timeframe)
        if not granularity:
            raise CoinbaseError(f"Unsupported timeframe: {timeframe}")

        product = self._product_id()
        params: dict[str, str] = {"granularity": str(granularity)}
        if start:
            params["start"] = start.isoformat()
        if end:
            params["end"] = end.isoformat()

        url = f"{COINBASE_BASE}/products/{urllib.parse.quote(product)}/candles?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(url, headers=_COINBASE_HEADERS)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = json.loads(resp.read().decode())
        except urllib.error.HTTPError as exc:
            body = exc.read().decode() if exc.fp else ""
            if self._store:
                from quantara_engine.market_data.provider_budgets import record_provider_error

                record_provider_error(self._store, "coinbase", f"HTTP {exc.code}: {body[:200]}")
            raise CoinbaseError(f"HTTP {exc.code}: {body}") from exc

        if self._store:
            record_request(
                self._store,
                "coinbase",
                symbol=product,
                caller=self._caller,
                success=True,
            )

        now = datetime.now(timezone.utc)
        candles: list[Candle] = []
        for row in raw:
            # [ time, low, high, open, close, volume ]
            ts = datetime.fromtimestamp(row[0], tz=timezone.utc)
            if not is_bar_complete(ts, timeframe, now):
                continue
            candles.append(
                Candle(
                    instrument_id=instrument_id,
                    timeframe=timeframe,
                    timestamp=ts,
                    open=Decimal(str(row[3])),
                    high=Decimal(str(row[2])),
                    low=Decimal(str(row[1])),
                    close=Decimal(str(row[4])),
                    volume=Decimal(str(row[5])) if row[5] else None,
                    source=self.source,
                    is_complete=True,
                )
            )
        candles.sort(key=lambda c: c.timestamp)
        if limit and len(candles) > limit:
            candles = candles[-limit:]
        return candles

    def fetch_latest(
        self,
        instrument_id: str,
        timeframe: str,
        since: datetime | None = None,
    ) -> list[Candle]:
        end = datetime.now(timezone.utc)
        if since is not None:
            if since.tzinfo is None:
                since = since.replace(tzinfo=timezone.utc)
            return self.fetch_candles(
                instrument_id,
                timeframe,
                start=since,
                end=end,
                limit=BOOTSTRAP_OUTPUT_SIZE,
            )
        return self.fetch_candles(instrument_id, timeframe, end=end, limit=30)

    def fetch_range(
        self,
        instrument_id: str,
        timeframe: str,
        start: datetime,
        end: datetime,
    ) -> list[Candle]:
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        if end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)
        return self.fetch_candles(instrument_id, timeframe, start=start, end=end)

    def fetch_bootstrap(
        self,
        instrument_id: str,
        timeframe: str,
        bars: int = BOOTSTRAP_OUTPUT_SIZE,
    ) -> list[Candle]:
        from quantara_engine.market_data.polling import BOOTSTRAP_MIN_5M_BARS, bootstrap_start

        pad = timeframe_minutes("1h") // timeframe_minutes("5m") if timeframe == PROVIDER_TIMEFRAME else 0
        target = max(bars, (BOOTSTRAP_MIN_5M_BARS + pad) if timeframe == PROVIDER_TIMEFRAME else bars)
        end = datetime.now(timezone.utc)
        collected: list[Candle] = []
        chunk_end = end
        while len(collected) < target:
            remaining = target - len(collected)
            chunk_bars = min(300, remaining)
            chunk_start = bootstrap_start(timeframe, chunk_end, chunk_bars)
            batch = self.fetch_candles(
                instrument_id,
                timeframe,
                start=chunk_start,
                end=chunk_end,
                limit=chunk_bars,
            )
            if not batch:
                break
            seen = {c.timestamp for c in collected}
            for candle in batch:
                if candle.timestamp not in seen:
                    collected.append(candle)
                    seen.add(candle.timestamp)
            collected.sort(key=lambda c: c.timestamp)
            oldest = batch[0].timestamp
            if oldest <= chunk_start:
                break
            chunk_end = oldest - timedelta(minutes=timeframe_minutes(timeframe))
            if chunk_end >= oldest:
                break
        if len(collected) > target:
            collected = collected[-target:]
        return collected
