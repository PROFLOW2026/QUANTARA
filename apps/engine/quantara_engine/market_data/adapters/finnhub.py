"""Finnhub market data adapter — validation/backup role only in this release."""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from quantara_engine.core.config import settings
from quantara_engine.market_data.finnhub_capabilities import (
    INTERNAL_HOURLY_LIMIT,
    INTERNAL_MINUTE_LIMIT,
    VERIFIED_RATE_LIMIT_PER_MINUTE,
)
from quantara_engine.market_data.provider_budgets import can_request, record_request
from quantara_engine.market_data.registry import AssetDefinition, ProviderName, provider_symbol
from quantara_engine.persistence.store import TradingStore

logger = logging.getLogger(__name__)

BASE_URL = "https://finnhub.io/api/v1"

_PLACEHOLDER_SECRETS = frozenset(
    {"", "local-dev-placeholder", "dev", "test", "changeme", "your", "placeholder"}
)


class FinnhubError(Exception):
    def __init__(self, message: str, *, code: int | None = None) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class FinnhubObservation:
    provider: str
    asset: str
    provider_symbol: str
    price: Decimal
    timestamp: datetime
    received_at: datetime
    data_type: str
    freshness_seconds: float


def finnhub_configured() -> bool:
    key = (settings.finnhub_api_key or "").strip()
    return bool(key) and key.lower() not in _PLACEHOLDER_SECRETS


def quota_snapshot() -> dict[str, Any]:
    assets = 8
    from quantara_engine.market_data.finnhub_capabilities import (
        expected_validation_calls_per_day,
        expected_validation_calls_per_hour,
    )

    return {
        "verified_limit_per_minute": VERIFIED_RATE_LIMIT_PER_MINUTE,
        "configured_internal_minute_limit": INTERNAL_MINUTE_LIMIT,
        "configured_internal_hourly_limit": INTERNAL_HOURLY_LIMIT,
        "expected_validation_calls_per_hour": expected_validation_calls_per_hour(assets),
        "expected_validation_calls_per_day": expected_validation_calls_per_day(assets),
    }


class FinnhubMarketDataProvider:
    """Quote adapter for watchdog validation — does not supply strategy candles."""

    source = "finnhub"

    def __init__(
        self,
        *,
        store: TradingStore | None = None,
        caller: str = "finnhub",
        asset: AssetDefinition | None = None,
    ) -> None:
        self.api_key = (settings.finnhub_api_key or "").strip()
        if not finnhub_configured():
            raise FinnhubError("FINNHUB_API_KEY is not configured")
        self._store = store
        self._caller = caller
        self._asset = asset

    def bind_context(
        self,
        *,
        store: TradingStore | None,
        caller: str,
        asset: AssetDefinition | None = None,
    ) -> None:
        self._store = store
        self._caller = caller
        self._asset = asset

    def _request(
        self,
        path: str,
        params: dict[str, str],
        *,
        symbol: str,
        purpose: str = "quote",
    ) -> dict[str, Any]:
        if not can_request(self._store, self.source, purpose=purpose):
            raise FinnhubError("Finnhub budget blocked request", code=429)
        query = urllib.parse.urlencode({**params, "token": self.api_key})
        url = f"{BASE_URL}{path}?{query}"
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=25) as resp:
                payload = json.loads(resp.read().decode())
            if isinstance(payload, dict) and payload.get("error"):
                raise FinnhubError(str(payload["error"]))
            record_request(
                self._store,
                self.source,
                symbol=symbol,
                caller=self._caller,
                success=True,
            )
            return payload if isinstance(payload, dict) else {"data": payload}
        except urllib.error.HTTPError as exc:
            body = exc.read().decode(errors="replace")[:300]
            record_request(
                self._store,
                self.source,
                symbol=symbol,
                caller=self._caller,
                success=False,
                error=body,
            )
            raise FinnhubError(body or f"HTTP {exc.code}", code=exc.code) from exc
        except urllib.error.URLError as exc:
            record_request(
                self._store,
                self.source,
                symbol=symbol,
                caller=self._caller,
                success=False,
                error=str(exc.reason),
            )
            raise FinnhubError(f"network error: {exc.reason}") from exc

    @staticmethod
    def _parse_quote_payload(
        payload: dict[str, Any],
        *,
        asset: str,
        provider_symbol: str,
        received_at: datetime,
    ) -> FinnhubObservation | None:
        price_raw = payload.get("c")
        ts_raw = payload.get("t")
        if price_raw in (None, 0) or not ts_raw:
            return None
        try:
            price = Decimal(str(price_raw))
        except (InvalidOperation, ValueError):
            return None
        if price <= 0:
            return None
        ts = datetime.fromtimestamp(int(ts_raw), tz=timezone.utc)
        freshness = max(0.0, (received_at - ts).total_seconds())
        return FinnhubObservation(
            provider="finnhub",
            asset=asset,
            provider_symbol=provider_symbol,
            price=price,
            timestamp=ts,
            received_at=received_at,
            data_type="quote",
            freshness_seconds=freshness,
        )

    def fetch_quote(self, asset: AssetDefinition) -> FinnhubObservation | None:
        sym = provider_symbol(asset, ProviderName.FINNHUB)
        received_at = datetime.now(timezone.utc)
        payload = self._request("/quote", {"symbol": sym}, symbol=asset.db_symbol)
        return self._parse_quote_payload(
            payload,
            asset=asset.db_symbol,
            provider_symbol=sym,
            received_at=received_at,
        )

    def probe_asset_quote(self, asset: AssetDefinition) -> tuple[bool, str | None]:
        """Lightweight capability probe — one quote attempt."""
        try:
            obs = self.fetch_quote(asset)
        except FinnhubError as exc:
            return False, str(exc)
        if obs is None:
            return False, "empty quote"
        return True, None

    def probe_capabilities(self, assets: tuple[AssetDefinition, ...]) -> dict[str, Any]:
        """Live probe per asset; updates capability flags truthfully."""
        from quantara_engine.market_data.finnhub_capabilities import DEFAULT_PROFILE

        profile = dict(DEFAULT_PROFILE)
        profile["probed_at"] = datetime.now(timezone.utc).isoformat()
        per_asset: dict[str, dict[str, bool]] = {}

        stock_ok = False
        crypto_ok = False
        forex_ok = False
        xau_ok = False

        for asset in assets:
            ok, err = self.probe_asset_quote(asset)
            per_asset[asset.db_symbol] = {
                "quote": ok,
                "candle_1m": False,
                "real_time": ok,
            }
            if asset.db_symbol in {"NVDA", "TSLA", "AMD", "COIN"}:
                stock_ok = stock_ok or ok
            elif asset.db_symbol in {"BTCUSD", "ETHUSD"}:
                crypto_ok = crypto_ok or ok
            elif asset.db_symbol == "GBPJPY":
                forex_ok = ok
            elif asset.db_symbol == "XAUUSD":
                xau_ok = ok
            if err and not ok:
                per_asset[asset.db_symbol]["error"] = err[:120]

        profile["stock_quote"] = stock_ok
        profile["crypto_quote"] = crypto_ok
        profile["forex_quote"] = forex_ok
        profile["xau_quote"] = xau_ok
        profile["per_asset"] = per_asset
        profile["stock_candle_1m"] = False
        profile["crypto_candle_1m"] = False
        profile["forex_candle_1m"] = False
        return profile
