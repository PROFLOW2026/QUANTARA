"""Shared domain contracts and API DTOs for QUANTARA Options.

All 6 implementation modules depend on these exact types so Database, Market Data,
Simulation Engine, Strategies/AI, Web UI, and QA stay strictly aligned.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class OptionRight(str, Enum):
    CALL = "call"
    PUT = "put"


class OptionSide(str, Enum):
    LONG = "long"
    SHORT = "short"


class ExerciseStyle(str, Enum):
    AMERICAN = "american"
    EUROPEAN = "european"


class DataFeedTier(str, Enum):
    ALPACA_INDICATIVE = "alpaca_indicative"
    ALPACA_OPRA = "alpaca_opra"
    FIXTURE = "fixture"
    UNAVAILABLE = "unavailable"


class DecisionMode(str, Enum):
    RULES = "rules"
    AI = "ai"


class StrategyType(str, Enum):
    LONG_CALL = "long_call"
    LONG_PUT = "long_put"
    COVERED_CALL = "covered_call"
    CASH_SECURED_PUT = "cash_secured_put"
    WHEEL = "wheel"
    BULL_CALL_SPREAD = "bull_call_spread"
    BEAR_PUT_SPREAD = "bear_put_spread"
    BULL_PUT_CREDIT_SPREAD = "bull_put_credit_spread"
    BEAR_CALL_CREDIT_SPREAD = "bear_call_credit_spread"
    IRON_CONDOR = "iron_condor"


ALL_9_STRATEGY_LABELS_HE: dict[str, str] = {
    StrategyType.LONG_CALL.value: "Long CALL",
    StrategyType.LONG_PUT.value: "Long PUT",
    StrategyType.COVERED_CALL.value: "Covered Call",
    StrategyType.CASH_SECURED_PUT.value: "Cash-Secured PUT",
    StrategyType.WHEEL.value: "Wheel Strategy",
    StrategyType.BULL_CALL_SPREAD.value: "Bull Call Spread",
    StrategyType.BEAR_PUT_SPREAD.value: "Bear Put Spread",
    StrategyType.BULL_PUT_CREDIT_SPREAD.value: "Bull Put Credit Spread",
    StrategyType.BEAR_CALL_CREDIT_SPREAD.value: "Bear Call Credit Spread",
    StrategyType.IRON_CONDOR.value: "Iron Condor",
}


class StrategyStatus(str, Enum):
    OPEN = "open"
    CLOSED = "closed"
    EXPIRED = "expired"
    ASSIGNED = "assigned"
    ROLLED = "rolled"
    REJECTED = "rejected"


class WheelState(str, Enum):
    IDLE = "idle"
    SHORT_PUT = "short_put"
    HOLDING_SHARES = "holding_shares"
    SHORT_CALL = "short_call"


class LegRole(str, Enum):
    LONG_CALL = "long_call"
    SHORT_CALL = "short_call"
    LONG_PUT = "long_put"
    SHORT_PUT = "short_put"
    UNDERLYING_STOCK = "underlying_stock"


class Greeks(BaseModel):
    delta: Optional[float] = None
    gamma: Optional[float] = None
    theta: Optional[float] = None  # per calendar day in $ per share
    vega: Optional[float] = None   # per 1% vol change in $ per share
    rho: Optional[float] = None
    computed_locally: bool = False


class OptionContractSpec(BaseModel):
    occ_symbol: str
    underlying_symbol: str
    right: OptionRight
    strike: Decimal
    expiration_date: date
    multiplier: int = 100
    exercise_style: ExerciseStyle = ExerciseStyle.AMERICAN
    tradable: bool = True
    root_symbol: Optional[str] = None
    open_interest: int = 0
    close_price: Optional[Decimal] = None


class OptionQuoteSnapshot(BaseModel):
    contract: OptionContractSpec
    underlying_price: Decimal
    bid: Optional[Decimal] = None
    ask: Optional[Decimal] = None
    mid: Optional[Decimal] = None
    last_trade: Optional[Decimal] = None
    volume: int = 0
    open_interest: int = 0
    implied_volatility: Optional[float] = None
    greeks: Greeks = Field(default_factory=Greeks)
    quote_timestamp: Optional[datetime] = None
    snapshot_timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    feed_tier: DataFeedTier = DataFeedTier.ALPACA_INDICATIVE
    is_indicative: bool = True
    is_delayed_trades: bool = True
    is_stale: bool = False
    spread_pct: Optional[float] = None
    data_warnings: list[str] = Field(default_factory=list)


class OptionChainResponse(BaseModel):
    underlying_symbol: str
    underlying_price: Optional[Decimal] = None
    as_of: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    market_open: bool = False
    feed_tier: DataFeedTier = DataFeedTier.ALPACA_INDICATIVE
    is_indicative: bool = True
    is_delayed_trades: bool = True
    is_stale: bool = False
    expirations: list[date] = Field(default_factory=list)
    contracts: list[OptionQuoteSnapshot] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    provider_name: str = "alpaca"


class AccountRuleProfile(BaseModel):
    """Configurable per-account rules; never hardcoded as global simulation blockers."""

    profile_name: str = "sim_flexible"
    margin_enabled: bool = True
    options_approval_level: int = 3  # 1=CC/CSP, 2=+Long, 3=+Spreads/IC, 4=Naked
    enforce_pdt_rule: bool = False
    pdt_equity_threshold_usd: Decimal = Decimal("25000")
    max_risk_per_trade_pct: Decimal = Decimal("5.0")
    max_collateral_pct: Decimal = Decimal("60.0")
    max_open_strategies: int = 6
    max_daily_loss_pct: Decimal = Decimal("5.0")
    max_drawdown_pct: Decimal = Decimal("20.0")
    allow_earnings_short_premium: bool = False
    auto_close_itm_before_expiry_minutes: int = 30
    commission_per_contract_usd: Decimal = Decimal("0.65")
    clearing_fee_per_contract_usd: Decimal = Decimal("0.08")
    assignment_fee_usd: Decimal = Decimal("0.00")
    base_slippage_of_half_spread: Decimal = Decimal("0.25")
    allow_fractional_equity: bool = False


class LegPlan(BaseModel):
    occ_symbol: Optional[str] = None  # None for underlying stock leg
    underlying_symbol: str
    leg_role: LegRole
    side: OptionSide
    right: Optional[OptionRight] = None
    strike: Optional[Decimal] = None
    expiration_date: Optional[date] = None
    multiplier: int = 100
    ratio: int = 1
    exercise_style: ExerciseStyle = ExerciseStyle.AMERICAN
    bid: Optional[Decimal] = None
    ask: Optional[Decimal] = None
    mid: Optional[Decimal] = None
    greeks: Greeks = Field(default_factory=Greeks)
    implied_volatility: Optional[float] = None


class StrategyCandidate(BaseModel):
    candidate_id: str
    strategy_type: StrategyType
    underlying_symbol: str
    underlying_price: Decimal
    contracts_qty: int = 1
    legs: list[LegPlan]
    dte: int
    expiration_date: date
    is_credit: bool
    estimated_net_premium_per_unit: Decimal  # +credit or -debit per share
    estimated_net_premium_total: Decimal     # multiplied by contract multiplier & qty
    max_profit_usd: Optional[Decimal] = None # None = theoretically unlimited (Long Call)
    max_loss_usd: Decimal
    collateral_required_usd: Decimal
    break_even_prices: list[Decimal] = Field(default_factory=list)
    probability_of_profit: Optional[float] = None
    iv_rank: Optional[float] = None
    net_delta: float = 0.0
    net_theta: float = 0.0
    net_vega: float = 0.0
    liquidity_score: float = 0.0
    rules_score: float = 0.0
    feed_tier: DataFeedTier = DataFeedTier.ALPACA_INDICATIVE
    is_indicative: bool = True
    warnings: list[str] = Field(default_factory=list)
    rationale_summary: str = ""
    skills_evidence: dict[str, Any] = Field(default_factory=dict)


class AIAction(str, Enum):
    OPEN = "OPEN"
    HOLD = "HOLD"
    CLOSE = "CLOSE"
    ROLL = "ROLL"
    SKIP = "SKIP"


class AIDecisionRecord(BaseModel):
    decision_id: str
    account_slug: str
    mode: DecisionMode
    action: AIAction
    strategy_type: Optional[StrategyType] = None
    candidate_id: Optional[str] = None
    position_group_id: Optional[str] = None
    underlying_symbol: str
    contracts_qty: int = 1
    confidence: float = 0.0
    decided_by: str = "rules_engine"  # "groq_openai_gpt_oss_120b" | "rules_engine" | "skills_rules_fallback"
    model_name: Optional[str] = None
    rationale_he: str = ""
    rationale_en: str = ""
    risk_guard_passed: bool = False
    risk_guard_reasons: list[str] = Field(default_factory=list)
    market_context: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class OptionAccountSummary(BaseModel):
    id: str
    slug: str
    name: str
    mode: DecisionMode
    strategy_scope: Optional[str] = None  # None = multi-strategy, or single strategy_type slug
    starting_capital: float
    cash_balance: float
    cash_reserved: float
    free_cash: float
    margin_used: float
    long_options_market_value: float
    short_options_liability: float
    stock_holdings_value: float
    equity: float
    premium_collected_gross: float
    premium_realized_net: float
    unrealized_pnl: float
    realized_pnl: float
    fees_paid: float
    max_drawdown_usd: float
    max_drawdown_pct: float
    return_pct: float
    open_strategies_count: int
    closed_strategies_count: int
    win_rate_pct: Optional[float] = None
    profit_factor: Optional[float] = None
    rule_profile: AccountRuleProfile = Field(default_factory=AccountRuleProfile)
    is_active: bool = True
    experiment_group: Optional[str] = None
    wheel_states: dict[str, str] = Field(default_factory=dict)
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class PayoffPoint(BaseModel):
    underlying_price: float
    expiration_pnl: float
    t0_pnl: float
