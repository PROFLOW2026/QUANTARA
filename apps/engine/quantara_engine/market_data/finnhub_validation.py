"""Finnhub watchdog — compare primary canonical prices without overwriting them."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from quantara_engine.execution.crypto_mark_valuation import get_fast_canonical_mark
from quantara_engine.market_data.adapters.finnhub import (
    FinnhubError,
    FinnhubMarketDataProvider,
    FinnhubObservation,
    finnhub_configured,
)
from quantara_engine.market_data.finnhub_capabilities import (
    SETTINGS_KEY as CAPABILITIES_KEY,
    VALIDATION_INTERVAL_MINUTES,
    asset_quote_capable,
    load_capabilities,
)
from quantara_engine.market_data.finnhub_thresholds import (
    load_thresholds,
    price_within_tolerance,
)
from quantara_engine.market_data.provider_budgets import can_request
from quantara_engine.market_data.registry import AssetDefinition, list_target_assets
from quantara_engine.persistence.store import TradingStore

logger = logging.getLogger(__name__)

VALIDATION_STATUS_KEY = "finnhub_validation_status"
OBSERVATIONS_KEY = "finnhub_observations"

STATUS_OK = "ok"
STATUS_DIVERGENCE = "divergence"
STATUS_STALE_BACKUP = "stale_backup"
STATUS_BACKUP_UNAVAILABLE = "backup_unavailable"
STATUS_CANONICAL_STALE = "canonical_stale"
STATUS_SKIPPED = "skipped"

STATUS_HE: dict[str, str] = {
    STATUS_OK: "תקין",
    STATUS_DIVERGENCE: "סטיית מחיר בין ספקים",
    STATUS_STALE_BACKUP: "נתון גיבוי מיושן",
    STATUS_BACKUP_UNAVAILABLE: "ספק גיבוי לא זמין",
    STATUS_CANONICAL_STALE: "נתון ראשי מיושן",
    STATUS_SKIPPED: "דילוג",
}


def load_validation_status(settings_dict: dict[str, Any] | None = None) -> dict[str, Any]:
    raw = (settings_dict or {}).get(VALIDATION_STATUS_KEY)
    return dict(raw) if isinstance(raw, dict) else {}


def resolve_canonical_for_validation(
    store: TradingStore,
    asset: AssetDefinition,
) -> tuple[Decimal, datetime, str] | None:
    mark = get_fast_canonical_mark(store, asset.db_symbol)
    if mark:
        return mark[0], mark[1], asset.primary_provider.value

    inst = store.get_instrument_by_symbol(asset.db_symbol)
    if not inst:
        return None
    for tf in ("1m", "5m", "15m", "1h"):
        ts = store.latest_candle_timestamp(inst.id, tf)
        if not ts:
            continue
        candles = store.list_candles(inst.id, tf, limit=1)
        if candles:
            c = candles[-1]
            return c.close, c.timestamp, asset.primary_provider.value
    return None


def _observation_payload(obs: FinnhubObservation) -> dict[str, Any]:
    return {
        "provider": obs.provider,
        "asset": obs.asset,
        "provider_symbol": obs.provider_symbol,
        "timestamp": obs.timestamp.isoformat(),
        "received_at": obs.received_at.isoformat(),
        "data_type": obs.data_type,
        "freshness_seconds": round(obs.freshness_seconds, 1),
        "price": str(obs.price),
    }


def compare_quotes(
    *,
    asset: AssetDefinition,
    canonical_price: Decimal,
    canonical_at: datetime,
    canonical_provider: str,
    finnhub_obs: FinnhubObservation,
    thresholds,
    now: datetime,
) -> dict[str, Any]:
    canonical_age = (now - canonical_at).total_seconds()
    if canonical_age > thresholds.max_canonical_age_seconds:
        return _status_row(
            asset,
            STATUS_CANONICAL_STALE,
            canonical_provider=canonical_provider,
            canonical_price=canonical_price,
            canonical_at=canonical_at,
            finnhub_obs=finnhub_obs,
            checked_at=now,
            detail=f"canonical_age={int(canonical_age)}s",
        )

    if finnhub_obs.freshness_seconds > thresholds.max_quote_age_seconds:
        return _status_row(
            asset,
            STATUS_STALE_BACKUP,
            canonical_provider=canonical_provider,
            canonical_price=canonical_price,
            canonical_at=canonical_at,
            finnhub_obs=finnhub_obs,
            checked_at=now,
            detail=f"finnhub_age={int(finnhub_obs.freshness_seconds)}s",
        )

    if price_within_tolerance(canonical_price, finnhub_obs.price, asset, thresholds):
        return _status_row(
            asset,
            STATUS_OK,
            canonical_provider=canonical_provider,
            canonical_price=canonical_price,
            canonical_at=canonical_at,
            finnhub_obs=finnhub_obs,
            checked_at=now,
        )

    pct_diff = float(abs(canonical_price - finnhub_obs.price) / canonical_price * 100)
    logger.warning(
        "Finnhub validation divergence %s canonical=%s finnhub=%s diff_pct=%.4f",
        asset.db_symbol,
        canonical_price,
        finnhub_obs.price,
        pct_diff,
    )
    return _status_row(
        asset,
        STATUS_DIVERGENCE,
        canonical_provider=canonical_provider,
        canonical_price=canonical_price,
        canonical_at=canonical_at,
        finnhub_obs=finnhub_obs,
        checked_at=now,
        detail=f"diff_pct={pct_diff:.4f}",
    )


def _status_row(
    asset: AssetDefinition,
    status: str,
    *,
    canonical_provider: str,
    canonical_price: Decimal,
    canonical_at: datetime,
    finnhub_obs: FinnhubObservation | None,
    checked_at: datetime,
    detail: str | None = None,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "asset": asset.db_symbol,
        "display_symbol": asset.display_symbol,
        "primary_provider": canonical_provider,
        "validation_provider": "finnhub",
        "status": status,
        "status_he": STATUS_HE.get(status, status),
        "canonical_price": str(canonical_price),
        "canonical_at": canonical_at.isoformat(),
        "checked_at": checked_at.isoformat(),
        "validation_interval_minutes": VALIDATION_INTERVAL_MINUTES,
    }
    if finnhub_obs:
        row["validation_price"] = str(finnhub_obs.price)
        row["validation_at"] = finnhub_obs.timestamp.isoformat()
        row["validation_freshness_seconds"] = round(finnhub_obs.freshness_seconds, 1)
        row["provider_symbol"] = finnhub_obs.provider_symbol
        row["observation"] = _observation_payload(finnhub_obs)
    if detail:
        row["detail"] = detail
    return row


def run_finnhub_validation(store: TradingStore, *, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    if not finnhub_configured():
        return {
            "status": STATUS_SKIPPED,
            "reason": "FINNHUB_API_KEY not configured",
            "assets": {},
        }

    if not can_request(store, "finnhub", purpose="validation"):
        return {
            "status": STATUS_SKIPPED,
            "reason": "finnhub budget exhausted",
            "assets": load_validation_status(store.get_settings_dict()),
        }

    settings_dict = store.get_settings_dict()
    capabilities = load_capabilities(settings_dict)
    thresholds = load_thresholds(settings_dict)
    provider = FinnhubMarketDataProvider(store=store, caller="finnhub_validation")

    assets_status: dict[str, Any] = {}
    observations: dict[str, Any] = dict(settings_dict.get(OBSERVATIONS_KEY) or {})
    per_asset_caps: dict[str, dict[str, Any]] = dict(capabilities.get("per_asset") or {})
    caps_dirty = False

    for asset in list_target_assets():
        if capabilities.get("probed_at") and not asset_quote_capable(capabilities, asset.db_symbol):
            assets_status[asset.db_symbol] = {
                "asset": asset.db_symbol,
                "display_symbol": asset.display_symbol,
                "primary_provider": asset.primary_provider.value,
                "validation_provider": "finnhub",
                "status": STATUS_BACKUP_UNAVAILABLE,
                "status_he": STATUS_HE[STATUS_BACKUP_UNAVAILABLE],
                "checked_at": now.isoformat(),
                "detail": "quote not verified on this account",
            }
            continue

        canonical = resolve_canonical_for_validation(store, asset)
        if not canonical:
            assets_status[asset.db_symbol] = {
                "asset": asset.db_symbol,
                "display_symbol": asset.display_symbol,
                "primary_provider": asset.primary_provider.value,
                "validation_provider": "finnhub",
                "status": STATUS_SKIPPED,
                "status_he": STATUS_HE[STATUS_SKIPPED],
                "checked_at": now.isoformat(),
                "detail": "no canonical reference",
            }
            continue

        canonical_price, canonical_at, canonical_provider = canonical
        try:
            obs = provider.fetch_quote(asset)
        except FinnhubError as exc:
            per_asset_caps[asset.db_symbol] = {
                "quote": False,
                "candle_1m": False,
                "real_time": False,
                "error": str(exc)[:120],
            }
            caps_dirty = True
            assets_status[asset.db_symbol] = {
                "asset": asset.db_symbol,
                "display_symbol": asset.display_symbol,
                "primary_provider": canonical_provider,
                "validation_provider": "finnhub",
                "status": STATUS_BACKUP_UNAVAILABLE,
                "status_he": STATUS_HE[STATUS_BACKUP_UNAVAILABLE],
                "checked_at": now.isoformat(),
                "detail": str(exc)[:120],
            }
            continue

        if obs is None:
            per_asset_caps[asset.db_symbol] = {
                "quote": False,
                "candle_1m": False,
                "real_time": False,
                "error": "empty quote",
            }
            caps_dirty = True
            assets_status[asset.db_symbol] = {
                "asset": asset.db_symbol,
                "display_symbol": asset.display_symbol,
                "primary_provider": canonical_provider,
                "validation_provider": "finnhub",
                "status": STATUS_BACKUP_UNAVAILABLE,
                "status_he": STATUS_HE[STATUS_BACKUP_UNAVAILABLE],
                "checked_at": now.isoformat(),
                "detail": "empty quote",
            }
            continue

        per_asset_caps[asset.db_symbol] = {
            "quote": True,
            "candle_1m": False,
            "real_time": True,
        }
        caps_dirty = True

        row = compare_quotes(
            asset=asset,
            canonical_price=canonical_price,
            canonical_at=canonical_at,
            canonical_provider=canonical_provider,
            finnhub_obs=obs,
            thresholds=thresholds,
            now=now,
        )
        assets_status[asset.db_symbol] = row
        observations[asset.db_symbol] = row.get("observation")

    if caps_dirty:
        capabilities["per_asset"] = per_asset_caps
        capabilities["probed_at"] = now.isoformat()
        capabilities["stock_quote"] = any(
            per_asset_caps.get(s, {}).get("quote") for s in ("NVDA", "TSLA", "AMD", "COIN")
        )
        capabilities["crypto_quote"] = any(
            per_asset_caps.get(s, {}).get("quote") for s in ("BTCUSD", "ETHUSD")
        )
        capabilities["forex_quote"] = bool(per_asset_caps.get("GBPJPY", {}).get("quote"))
        capabilities["xau_quote"] = bool(per_asset_caps.get("XAUUSD", {}).get("quote"))
        store.update_settings(CAPABILITIES_KEY, capabilities, description="Finnhub live capability probe")

    store.update_settings(VALIDATION_STATUS_KEY, assets_status, description="Finnhub validation watchdog")
    store.update_settings(OBSERVATIONS_KEY, observations, description="Finnhub validation observations")

    divergences = sum(1 for r in assets_status.values() if r.get("status") == STATUS_DIVERGENCE)
    return {
        "status": "success",
        "checked": len(assets_status),
        "divergences": divergences,
        "assets": assets_status,
        "capabilities": capabilities,
    }
