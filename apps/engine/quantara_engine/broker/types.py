"""Broker layer domain types."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum


class PositionMode(str, Enum):
    NETTING = "netting"
    HEDGING = "hedging"


class AccountState(str, Enum):
    ACTIVE = "active"
    MARGIN_WARNING = "margin_warning"
    MARGIN_CALL = "margin_call"
    LIQUIDATION = "liquidation"
    LIQUIDATION_PENDING = "liquidation_pending"
    PAUSED = "paused"


class BrokerOrderStatus(str, Enum):
    CREATED = "created"
    VALIDATING = "validating"
    REJECTED = "rejected"
    ACCEPTED = "accepted"
    SUBMITTED = "submitted"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class BrokerRejectionReason(str, Enum):
    INSUFFICIENT_BUYING_POWER = "insufficient_buying_power"
    INSUFFICIENT_MARGIN = "insufficient_margin"
    MAX_LEVERAGE = "max_leverage"
    MAX_GROSS_LEVERAGE = "max_gross_leverage"
    MAX_ASSET_EXPOSURE = "max_asset_exposure"
    MAX_ORDER_NOTIONAL = "max_order_notional"
    INVALID_QUANTITY = "invalid_quantity"
    SHORT_NOT_ALLOWED = "short_not_allowed"
    MARKET_CLOSED = "market_closed"
    STALE_MARKET_DATA = "stale_market_data"
    RISK_LIMIT = "risk_limit"
    DUPLICATE_OPPORTUNITY = "duplicate_opportunity"
    ACCOUNT_PAUSED = "account_paused"
    MARGIN_CALL = "margin_call"
    LIQUIDATION = "liquidation"


@dataclass(frozen=True)
class AssetClassRules:
    """Per asset-class margin and trading constraints."""

    initial_margin_pct: Decimal
    maintenance_margin_pct: Decimal
    max_leverage: Decimal
    max_order_notional: Decimal | None = None
    max_position_notional: Decimal | None = None
    shorting_allowed: bool = True
    fractional_allowed: bool = False
    overnight_margin_multiplier: Decimal = Decimal("1")


@dataclass(frozen=True)
class BrokerProfile:
    """Configurable broker simulation profile — not a named live broker."""

    slug: str
    name: str
    account_currency: str
    position_mode: PositionMode
    starting_cash: Decimal
    max_gross_leverage: Decimal
    max_net_leverage: Decimal
    margin_warning_level_pct: Decimal
    margin_call_level_pct: Decimal
    liquidation_level_pct: Decimal
    allow_broker_downsize: bool = False
    asset_rules: dict[str, AssetClassRules] = field(default_factory=dict)

    def rules_for(self, asset_class: str) -> AssetClassRules:
        key = asset_class.lower()
        if key not in self.asset_rules:
            raise KeyError(f"No broker rules for asset class: {asset_class}")
        return self.asset_rules[key]


@dataclass(frozen=True)
class InstrumentSpec:
    """Canonical instrument constraints — single source for sizing/margin/execution."""

    symbol: str
    asset_class: str
    base_currency: str
    quote_currency: str
    pip_size: Decimal
    tick_size: Decimal
    contract_size: Decimal
    min_quantity: Decimal
    quantity_step: Decimal
    min_notional: Decimal
    shortable: bool
    fractional: bool
    session_key: str  # us_equity_rth | 24x7 | 24x5


@dataclass
class BrokerPosition:
    """Canonical account-level position (may net many strategy legs)."""

    symbol: str
    net_quantity: Decimal  # signed: + long, - short
    average_price: Decimal
    mark_price: Decimal
    unrealized_pnl: Decimal = Decimal("0")


@dataclass
class BrokerAccountSnapshot:
    """In-memory broker account state for pre-trade simulation."""

    profile_slug: str
    cash: Decimal
    balance: Decimal
    equity: Decimal
    realized_pnl: Decimal
    unrealized_pnl: Decimal
    gross_exposure: Decimal
    net_exposure: Decimal
    initial_margin_used: Decimal
    maintenance_margin_required: Decimal
    free_margin: Decimal
    available_margin: Decimal
    spot_crypto_cash: Decimal
    buying_power: Decimal  # alias: available_margin for backward compat
    margin_level_pct: Decimal | None
    gross_leverage: Decimal
    net_leverage: Decimal
    account_state: AccountState
    positions: dict[str, BrokerPosition] = field(default_factory=dict)


@dataclass(frozen=True)
class BrokerOrderRequest:
    """Order submitted to broker pre-trade check."""

    symbol: str
    asset_class: str
    direction: str  # long | short
    quantity: Decimal
    mark_price: Decimal
    is_close: bool = False
    strategy_portfolio_id: str = ""
    opportunity_key: str | None = None
    signal_timestamp: datetime | None = None
    market_open: bool = True
    data_fresh: bool = True
    is_liquidation: bool = False


@dataclass
class BrokerOrderDecision:
    """Result of evaluate_broker_order."""

    accepted: bool
    accepted_quantity: Decimal
    rejection_reason: BrokerRejectionReason | None = None
    rejection_detail: str = ""
    required_initial_margin: Decimal = Decimal("0")
    buying_power_before: Decimal = Decimal("0")
    buying_power_after: Decimal = Decimal("0")
    gross_leverage_before: Decimal = Decimal("0")
    gross_leverage_after: Decimal = Decimal("0")
    asset_exposure_before: Decimal = Decimal("0")
    asset_exposure_after: Decimal = Decimal("0")
    diagnostics: dict = field(default_factory=dict)
