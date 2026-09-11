"""Coinbase Exchange public market data (no API key required)."""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
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
GRANULARITY_MAP = {"5m": 300, "15m": 900, "1h": 3600}


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
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
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
