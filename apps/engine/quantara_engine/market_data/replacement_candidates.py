"""Inactive replacement-asset candidates — not seeded until owner selects one.

To activate a candidate after owner approval:
1. Add AssetDefinition to TARGET_ASSETS in registry.py
2. Optionally add to ROBOT_A_TRADABLE_DB_SYMBOLS or ORB_ASSETS
3. Run seed script for the new instrument row
"""

from __future__ import annotations

from dataclasses import dataclass

from quantara_engine.market_data.registry import AssetClass, ProviderName


@dataclass(frozen=True)
class ReplacementAssetCandidate:
    db_symbol: str
    display_symbol: str
    primary_provider: ProviderName
    secondary_provider: ProviderName | None
    trading_sessions: tuple[str, ...]
    orb_compatible: bool
    notes: str


REPLACEMENT_ASSET_CANDIDATES: tuple[ReplacementAssetCandidate, ...] = (
    ReplacementAssetCandidate(
        db_symbol="TSLA",
        display_symbol="TSLA",
        primary_provider=ProviderName.TIINGO,
        secondary_provider=ProviderName.ALPACA,
        trading_sessions=("us_equity_rth",),
        orb_compatible=True,
        notes="High liquidity US equity; Alpaca live 5m during RTH; ORB eligible.",
    ),
    ReplacementAssetCandidate(
        db_symbol="AMD",
        display_symbol="AMD",
        primary_provider=ProviderName.TIINGO,
        secondary_provider=ProviderName.ALPACA,
        trading_sessions=("us_equity_rth",),
        orb_compatible=True,
        notes="High activity semiconductor; same provider stack as existing ORB equities.",
    ),
    ReplacementAssetCandidate(
        db_symbol="META",
        display_symbol="META",
        primary_provider=ProviderName.TIINGO,
        secondary_provider=ProviderName.ALPACA,
        trading_sessions=("us_equity_rth",),
        orb_compatible=True,
        notes="High liquidity mega-cap; 5m/15m/1h via Tiingo bulk + Alpaca live RTH.",
    ),
)
