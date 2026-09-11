"""QUANTARA_STANDARD_PAPER — conservative default broker profile."""

from __future__ import annotations

from decimal import Decimal

from quantara_engine.broker.types import AssetClassRules, BrokerProfile, PositionMode

# PAPER ASSUMPTION: conservative simulation defaults, not a named broker.
# Values are explicit and centralized for future RealBrokerAdapter override.

_STANDARD_ASSET_RULES: dict[str, AssetClassRules] = {
    "stock": AssetClassRules(
        initial_margin_pct=Decimal("50"),  # 2:1 Reg-T-like
        maintenance_margin_pct=Decimal("25"),
        max_leverage=Decimal("2"),
        max_order_notional=Decimal("100000"),
        max_position_notional=Decimal("150000"),
        shorting_allowed=True,
        fractional_allowed=False,
        overnight_margin_multiplier=Decimal("1"),
    ),
    "crypto": AssetClassRules(
        initial_margin_pct=Decimal("100"),  # spot-style: full cash
        maintenance_margin_pct=Decimal("100"),
        max_leverage=Decimal("1"),
        max_order_notional=Decimal("50000"),
        max_position_notional=Decimal("100000"),
        shorting_allowed=False,  # spot default
        fractional_allowed=True,
    ),
    "forex": AssetClassRules(
        initial_margin_pct=Decimal("5"),  # 20:1 max
        maintenance_margin_pct=Decimal("2.5"),
        max_leverage=Decimal("20"),
        max_order_notional=Decimal("500000"),
        max_position_notional=Decimal("1000000"),
        shorting_allowed=True,
        fractional_allowed=False,
    ),
    "commodity": AssetClassRules(
        initial_margin_pct=Decimal("10"),  # 10:1
        maintenance_margin_pct=Decimal("5"),
        max_leverage=Decimal("10"),
        max_order_notional=Decimal("200000"),
        max_position_notional=Decimal("400000"),
        shorting_allowed=True,
        fractional_allowed=True,
    ),
}

QUANTARA_STANDARD_PAPER = BrokerProfile(
    slug="quantara_standard_paper",
    name="QUANTARA Standard Paper",
    account_currency="USD",
    position_mode=PositionMode.NETTING,
    starting_cash=Decimal("320000"),
    max_gross_leverage=Decimal("2.0"),
    max_net_leverage=Decimal("2.0"),
    margin_warning_level_pct=Decimal("150"),
    margin_call_level_pct=Decimal("100"),
    liquidation_level_pct=Decimal("50"),
    allow_broker_downsize=False,
    asset_rules=_STANDARD_ASSET_RULES,
)
