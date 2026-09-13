"""Asset-envelope risk gates — isolated capital cannot cross assets."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from quantara_engine.owner_portfolio.asset_allocation import asset_equity, get_asset_allocation_row
from quantara_engine.persistence.store import TradingStore


@dataclass(frozen=True)
class AssetRiskVerdict:
    allowed: bool
    reason: str | None = None
    layer: str = "asset_envelope"


def evaluate_asset_envelope_risk(
    store: TradingStore,
    *,
    owner_slug: str,
    canonical_symbol: str,
    incremental_sl_risk_usd: Decimal,
    incremental_notional_usd: Decimal = Decimal("0"),
    required_cash_usd: Decimal = Decimal("0"),
) -> AssetRiskVerdict:
    """Reject when trade would exceed this asset's isolated envelope."""
    from quantara_engine.owner_portfolio.asset_allocation import is_equal_asset_mode_active

    if not is_equal_asset_mode_active(store, owner_slug):
        return AssetRiskVerdict(allowed=True, reason="equal_asset_mode_inactive", layer="asset_envelope")

    sym = canonical_symbol.upper().replace("/", "")
    row = get_asset_allocation_row(store, owner_slug=owner_slug, canonical_symbol=sym)
    if not row:
        return AssetRiskVerdict(allowed=False, reason="asset_allocation_missing")

    if not row.get("enabled"):
        return AssetRiskVerdict(allowed=False, reason="asset_allocation_disabled")

    equity = asset_equity(row)
    if equity <= 0:
        return AssetRiskVerdict(allowed=False, reason="asset_equity_non_positive")

    cash = Decimal(str(row.get("current_cash") or 0))
    if required_cash_usd > cash:
        return AssetRiskVerdict(allowed=False, reason="asset_cash_insufficient")

    open_sl = Decimal(str(row.get("open_sl_risk_usd") or 0))
    projected_sl = open_sl + incremental_sl_risk_usd
    if projected_sl > equity:
        return AssetRiskVerdict(allowed=False, reason="asset_sl_risk_exceeds_envelope")

    starting = Decimal(str(row.get("starting_allocated_capital") or 0))
    if starting > 0:
        exposure = Decimal(str(row.get("gross_exposure") or 0)) + incremental_notional_usd
        if exposure > starting * Decimal("10"):
            return AssetRiskVerdict(allowed=False, reason="asset_leverage_cap_exceeded")

    return AssetRiskVerdict(allowed=True)
