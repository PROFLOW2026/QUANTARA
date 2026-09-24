"""Alpaca market data adapter (US equities + crypto)."""

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
    timeframe_minutes,
)
from quantara_engine.market_data.alpaca_bar_range import (
    exclusive_end_utc,
    format_alpaca_ts,
    resolve_bars_request_range,
    utc_for_alpaca,
)
from quantara_engine.market_data.provider_budgets import (
    FetchPriority,
    can_request,
    record_request,
)
from quantara_engine.market_data.registry import AssetClass, AssetDefinition, ProviderName, provider_symbol
from quantara_engine.persistence.store import TradingStore

logger = logging.getLogger(__name__)

TIMEFRAME_MAP = {
    "1m": "1Min",
    "5m": "5Min",
    "15m": "15Min",
    "1h": "1Hour",
}

FAST_PROTECTION_TIMEFRAME = "1m"


class AlpacaError(Exception):
    pass


class AlpacaMarketDataProvider:
    source = "alpaca"

    def __init__(
        self,
        *,
        store: TradingStore | None = None,
        caller: str = "alpaca",
        priority: FetchPriority = FetchPriority.SCHEDULED,
        asset: AssetDefinition | None = None,
    ) -> None:
        self.api_key = settings.alpaca_api_key_id.strip()
        self.api_secret = settings.alpaca_api_secret_key.strip()
        if not self.api_key or not self.api_secret:
            raise AlpacaError("Alpaca credentials are not configured")
        self.base_url = settings.alpaca_data_base_url.rstrip("/")
        self.feed = (settings.alpaca_data_feed or "iex").strip() or "iex"
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
            "APCA-API-KEY-ID": self.api_key,
            "APCA-API-SECRET-KEY": self.api_secret,
        }

    def _request(self, url: str, symbol: str) -> dict[str, Any]:
        if not can_request(self._store, self.source):
            raise AlpacaError(f"Alpaca budget blocked request for {symbol}")
        req = urllib.request.Request(url, headers=self._headers())
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                payload = json.loads(resp.read().decode())
            record_request(self._store, self.source, symbol=symbol, caller=self._caller, success=True)
            return payload
        except urllib.error.HTTPError as exc:
            body = exc.read().decode(errors="replace")[:200]
            record_request(
                self._store,
                self.source,
                symbol=symbol,
                caller=self._caller,
                success=False,
                error=f"HTTP {exc.code}: {body}",
            )
            raise AlpacaError(f"HTTP {exc.code}: {body}") from exc
        except urllib.error.URLError as exc:
            record_request(
                self._store,
                self.source,
                symbol=symbol,
                caller=self._caller,
                success=False,
                error=str(exc.reason),
            )
            raise AlpacaError(f"Network error: {exc.reason}") from exc

    def _provider_ticker(self) -> str:
        if self._asset is None:
            raise AlpacaError("Asset context required for Alpaca fetch")
        return provider_symbol(self._asset, ProviderName.ALPACA)

    @staticmethod
    def _valid_request_range(
        start: datetime | None,
        end: datetime | None = None,
        *,
        timeframe: str = FAST_PROTECTION_TIMEFRAME,
    ) -> bool:
        """Alpaca rejects start >= end; compare in UTC with completed-minute end."""
        if start is None:
            return True
        return (
            resolve_bars_request_range(start, end, timeframe=timeframe)
            is not None
        )

    def _fetch_bars(
        self,
        *,
        symbol: str,
        timeframe: str,
        start: datetime | None,
        limit: int,
        page_token: str | None = None,
        end: datetime | None = None,
    ) -> tuple[list[dict[str, Any]], str | None]:
        tf = TIMEFRAME_MAP.get(timeframe)
        if not tf:
            raise AlpacaError(f"Unsupported timeframe: {timeframe}")

        resolved: tuple[datetime, datetime] | None = None
        if page_token is None and start is not None:
            end_for_range = end if end is not None else datetime.now(timezone.utc)
            resolved = resolve_bars_request_range(
                start,
                end_for_range,
                timeframe=timeframe,
            )
            if resolved is None:
                logger.info(
                    "Skipping Alpaca bars — no completed range start=%s end=%s symbol=%s tf=%s",
                    utc_for_alpaca(start).isoformat(),
                    utc_for_alpaca(end_for_range).isoformat(),
                    symbol,
                    timeframe,
                )
                return [], None

        if self._asset and self._asset.asset_class == AssetClass.CRYPTO:
            params: dict[str, Any] = {
                "symbols": symbol,
                "timeframe": tf,
                "limit": min(limit, 10000),
            }
            if resolved:
                params["start"] = format_alpaca_ts(resolved[0])
                params["end"] = format_alpaca_ts(resolved[1])
            elif start:
                params["start"] = format_alpaca_ts(start)
            if end and not resolved:
                params["end"] = format_alpaca_ts(end)
            if page_token:
                params["page_token"] = page_token
            url = f"{self.base_url}/v1beta3/crypto/us/bars?" + urllib.parse.urlencode(params)
        else:
            params = {
                "timeframe": tf,
                "limit": min(limit, 10000),
                "feed": self.feed,
            }
            if resolved:
                params["start"] = format_alpaca_ts(resolved[0])
                params["end"] = format_alpaca_ts(resolved[1])
            elif start:
                params["start"] = format_alpaca_ts(start)
            if end and not resolved:
                params["end"] = format_alpaca_ts(end)
            if page_token:
                params["page_token"] = page_token
            url = f"{self.base_url}/v2/stocks/{symbol}/bars?" + urllib.parse.urlencode(params)

        payload = self._request(url, symbol)
        bars = payload.get("bars")
        if isinstance(bars, list):
            rows = bars
        elif isinstance(bars, dict):
            rows = list(bars.get(symbol) or [])
        else:
            rows = []
        return rows, payload.get("next_page_token")

    def _fetch_bars_paginated(
        self,
        *,
        symbol: str,
        timeframe: str,
        start: datetime | None,
        limit: int,
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        page_token: str | None = None
        while len(rows) < limit:
            batch, page_token = self._fetch_bars(
                symbol=symbol,
                timeframe=timeframe,
                start=start,
                limit=limit - len(rows),
                page_token=page_token,
            )
            if not batch:
                break
            rows.extend(batch)
            if not page_token:
                break
        return rows[:limit]

    def _parse_bar(self, row: dict[str, Any], instrument_id: str, timeframe: str) -> Candle | None:
        try:
            raw_ts = row["t"]
            ts = datetime.fromisoformat(raw_ts.replace("Z", "+00:00"))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            return Candle(
                instrument_id=instrument_id,
                timeframe=timeframe,
                timestamp=ts,
                open=Decimal(str(row["o"])),
                high=Decimal(str(row["h"])),
                low=Decimal(str(row["l"])),
                close=Decimal(str(row["c"])),
                volume=Decimal(str(row["v"])) if row.get("v") is not None else None,
                source=self.source,
                is_complete=is_bar_complete(ts, timeframe),
            )
        except (KeyError, InvalidOperation, ValueError) as exc:
            logger.warning("Skipping malformed Alpaca bar: %s", exc)
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
            candle = self._parse_bar(row, instrument_id, timeframe)
            if candle is None or not candle.is_complete:
                continue
            if since and candle.timestamp <= since:
                continue
            candles.append(candle)
        candles.sort(key=lambda c: c.timestamp)
        return candles

    def fetch_gap_fill(
        self,
        instrument_id: str,
        timeframe: str,
        since: datetime,
        *,
        end: datetime | None = None,
    ) -> list[Candle]:
        """Paginated fetch from last stored bar through end (default now)."""
        if timeframe != PROVIDER_TIMEFRAME:
            raise AlpacaError(f"Alpaca fetch blocked for {timeframe}; use {PROVIDER_TIMEFRAME}")
        symbol = self._provider_ticker()
        since = utc_for_alpaca(since)
        end = exclusive_end_utc(end or datetime.now(timezone.utc), timeframe)
        bar_step = timedelta(minutes=timeframe_minutes(timeframe))
        cursor = since + bar_step
        if cursor >= end:
            return []

        all_rows: list[dict[str, Any]] = []
        while cursor < end:
            page_token: str | None = None
            window_rows: list[dict[str, Any]] = []
            while True:
                batch, page_token = self._fetch_bars(
                    symbol=symbol,
                    timeframe=timeframe,
                    start=cursor if page_token is None else None,
                    limit=10000,
                    page_token=page_token,
                )
                if not batch:
                    break
                window_rows.extend(batch)
                if not page_token:
                    break
            if not window_rows:
                break
            all_rows.extend(window_rows)
            last_ts = datetime.fromisoformat(str(window_rows[-1]["t"]).replace("Z", "+00:00"))
            if last_ts.tzinfo is None:
                last_ts = last_ts.replace(tzinfo=timezone.utc)
            if last_ts >= end - bar_step:
                break
            cursor = last_ts + bar_step

        return self._to_candles(all_rows, instrument_id, timeframe, since=since)

    def _fetch_equity_bars_batch(
        self,
        *,
        symbols: list[str],
        timeframe: str,
        start: datetime | None,
        limit: int,
        end: datetime | None = None,
    ) -> dict[str, list[dict[str, Any]]]:
        """Multi-symbol equity bars (one HTTP request)."""
        if not symbols:
            return {}
        tf = TIMEFRAME_MAP.get(timeframe)
        if not tf:
            raise AlpacaError(f"Unsupported timeframe: {timeframe}")
        end_for_range = end if end is not None else datetime.now(timezone.utc)
        resolved = resolve_bars_request_range(start, end_for_range, timeframe=timeframe)
        if start is not None and resolved is None:
            logger.info(
                "Skipping Alpaca batch bars — no completed range start=%s end=%s symbols=%s",
                utc_for_alpaca(start).isoformat(),
                utc_for_alpaca(end_for_range).isoformat(),
                ",".join(symbols),
            )
            return {sym: [] for sym in symbols}
        params: dict[str, Any] = {
            "symbols": ",".join(symbols),
            "timeframe": tf,
            "limit": min(limit, 10000),
            "feed": self.feed,
        }
        if resolved:
            params["start"] = format_alpaca_ts(resolved[0])
            params["end"] = format_alpaca_ts(resolved[1])
        else:
            if start:
                params["start"] = format_alpaca_ts(start)
            if end:
                params["end"] = format_alpaca_ts(end)
        url = f"{self.base_url}/v2/stocks/bars?" + urllib.parse.urlencode(params)
        payload = self._request(url, ",".join(symbols))
        bars = payload.get("bars")
        if isinstance(bars, dict):
            return {sym: list(bars.get(sym) or []) for sym in symbols}
        if isinstance(bars, list) and len(symbols) == 1:
            return {symbols[0]: bars}
        return {sym: [] for sym in symbols}

    def fetch_equity_1m_batch(
        self,
        entries: list[tuple[str, str]],
        *,
        since: datetime,
    ) -> dict[str, list[Candle]]:
        """
        Position-only fast path: batched 1m equity bars.

        entries: list of (instrument_id, provider_ticker)
        """
        if not entries:
            return {}
        since = utc_for_alpaca(since)
        now = datetime.now(timezone.utc)
        symbol_to_iid = {ticker: iid for iid, ticker in entries}
        tickers = list(symbol_to_iid.keys())
        start = since - timedelta(minutes=15)
        if resolve_bars_request_range(start, now, timeframe=FAST_PROTECTION_TIMEFRAME) is None:
            logger.info(
                "Skipping Alpaca equity 1m batch — no completed range since=%s",
                since.isoformat(),
            )
            return {}
        rows_by_symbol = self._fetch_equity_bars_batch(
            symbols=tickers,
            timeframe=FAST_PROTECTION_TIMEFRAME,
            start=start,
            limit=100,
            end=now,
        )
        out: dict[str, list[Candle]] = {}
        for ticker, rows in rows_by_symbol.items():
            iid = symbol_to_iid.get(ticker)
            if not iid:
                continue
            out[iid] = self._to_candles(
                rows, iid, FAST_PROTECTION_TIMEFRAME, since=since
            )
        return out

    def fetch_latest(
        self,
        instrument_id: str,
        timeframe: str,
        since: datetime | None = None,
    ) -> list[Candle]:
        if timeframe not in (PROVIDER_TIMEFRAME, FAST_PROTECTION_TIMEFRAME):
            raise AlpacaError(
                f"Alpaca fetch blocked for {timeframe}; use {PROVIDER_TIMEFRAME} or {FAST_PROTECTION_TIMEFRAME}"
            )
        now = datetime.now(timezone.utc)
        if (
            timeframe == PROVIDER_TIMEFRAME
            and since is not None
            and (now - since).total_seconds() > 86400
        ):
            return self.fetch_gap_fill(instrument_id, timeframe, since, end=now)

        symbol = self._provider_ticker()
        if since is not None:
            since = utc_for_alpaca(since)
            # No completed bar can exist yet — skip HTTP without provider error.
            bar_step = timedelta(minutes=timeframe_minutes(timeframe))
            if since + bar_step >= exclusive_end_utc(now, timeframe):
                return []
            start = since - timedelta(minutes=15)
            if (now - since).total_seconds() > 3600:
                start = since - timedelta(hours=6)
            limit = 1000 if (now - since).total_seconds() > 86400 else 100
        else:
            start = now - timedelta(days=2)
            limit = 100
        if resolve_bars_request_range(start, now, timeframe=timeframe) is None:
            logger.info(
                "Skipping Alpaca fetch_latest — no completed range start=%s symbol=%s tf=%s",
                utc_for_alpaca(start).isoformat(),
                symbol,
                timeframe,
            )
            return []
        rows = self._fetch_bars_paginated(
            symbol=symbol, timeframe=timeframe, start=start, limit=limit
        )
        return self._to_candles(rows, instrument_id, timeframe, since=since)

    def fetch_bootstrap(self, instrument_id: str, timeframe: str, bars: int = BOOTSTRAP_OUTPUT_SIZE) -> list[Candle]:
        symbol = self._provider_ticker()
        target = min(max(bars, BOOTSTRAP_OUTPUT_SIZE), 10000)
        # Request recent window — Alpaca returns forward from start; avoid anchoring 90d in the past.
        start = datetime.now(timezone.utc) - timedelta(
            minutes=timeframe_minutes(timeframe) * target + timeframe_minutes(timeframe)
        )
        rows = self._fetch_bars_paginated(
            symbol=symbol, timeframe=timeframe, start=start, limit=target
        )
        return self._to_candles(rows, instrument_id, timeframe)

    def fetch_range(
        self,
        instrument_id: str,
        timeframe: str,
        start: datetime,
        end: datetime,
    ) -> list[Candle]:
        symbol = self._provider_ticker()
        rows = self._fetch_bars_paginated(
            symbol=symbol, timeframe=timeframe, start=start, limit=10000
        )
        candles = self._to_candles(rows, instrument_id, timeframe)
        return [c for c in candles if start <= c.timestamp <= end]

    def generate_candles(
        self,
        instrument_id: str,
        timeframe: str,
        count: int,
        start: datetime | None = None,
    ) -> list[Candle]:
        raise NotImplementedError("Alpaca does not synthesize candles")
