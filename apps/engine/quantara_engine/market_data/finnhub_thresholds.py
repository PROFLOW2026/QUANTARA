"""Configurable Finnhub validation divergence thresholds."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from quantara_engine.market_data.registry import AssetClass, AssetDefinition

SETTINGS_KEY = "finnhub_validation_thresholds"


@dataclass(frozen=True)
class FinnhubValidationThresholds:
    crypto_pct: Decimal
    equity_pct: Decimal
    equity_min_abs_usd: Decimal
    fx_pips: Decimal
    xau_abs_usd: Decimal
    xau_pct: Decimal
    max_quote_age_seconds: int
    max_canonical_age_seconds: int


DEFAULT_THRESHOLDS = FinnhubValidationThresholds(
    crypto_pct=Decimal("0.005"),
    equity_pct=Decimal("0.01"),
    equity_min_abs_usd=Decimal("0.05"),
    fx_pips=Decimal("5"),
    xau_abs_usd=Decimal("2.0"),
    xau_pct=Decimal("0.001"),
    max_quote_age_seconds=300,
    max_canonical_age_seconds=600,
)


def load_thresholds(settings_dict: dict[str, Any] | None = None) -> FinnhubValidationThresholds:
    raw = (settings_dict or {}).get(SETTINGS_KEY)
    if not isinstance(raw, dict):
        return DEFAULT_THRESHOLDS
    return FinnhubValidationThresholds(
        crypto_pct=Decimal(str(raw.get("crypto_pct", DEFAULT_THRESHOLDS.crypto_pct))),
        equity_pct=Decimal(str(raw.get("equity_pct", DEFAULT_THRESHOLDS.equity_pct))),
        equity_min_abs_usd=Decimal(
            str(raw.get("equity_min_abs_usd", DEFAULT_THRESHOLDS.equity_min_abs_usd))
        ),
        fx_pips=Decimal(str(raw.get("fx_pips", DEFAULT_THRESHOLDS.fx_pips))),
        xau_abs_usd=Decimal(str(raw.get("xau_abs_usd", DEFAULT_THRESHOLDS.xau_abs_usd))),
        xau_pct=Decimal(str(raw.get("xau_pct", DEFAULT_THRESHOLDS.xau_pct))),
        max_quote_age_seconds=int(
            raw.get("max_quote_age_seconds", DEFAULT_THRESHOLDS.max_quote_age_seconds)
        ),
        max_canonical_age_seconds=int(
            raw.get("max_canonical_age_seconds", DEFAULT_THRESHOLDS.max_canonical_age_seconds)
        ),
    )


def price_within_tolerance(
    canonical: Decimal,
    validation: Decimal,
    asset: AssetDefinition,
    thresholds: FinnhubValidationThresholds,
) -> bool:
    if canonical <= 0 or validation <= 0:
        return False
    diff = abs(canonical - validation)
    if asset.asset_class == AssetClass.CRYPTO:
        return diff / canonical <= thresholds.crypto_pct
    if asset.asset_class == AssetClass.STOCK:
        pct_ok = diff / canonical <= thresholds.equity_pct
        abs_ok = diff <= thresholds.equity_min_abs_usd
        return pct_ok and abs_ok
    if asset.asset_class == AssetClass.FOREX:
        pip = Decimal(asset.pip_size or "0.01")
        return diff / pip <= thresholds.fx_pips
    if asset.asset_class == AssetClass.COMMODITY:
        abs_ok = diff <= thresholds.xau_abs_usd
        pct_ok = diff / canonical <= thresholds.xau_pct
        return abs_ok or pct_ok
    return diff / canonical <= thresholds.equity_pct
