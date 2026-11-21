"""API response and request schemas for QUANTARA Options (`/api/v1/options/...`).

Both backend FastAPI routes (`quantara_options/api/router.py`) and the Next.js
frontend (`apps/web/lib/options-types.ts` + `options-api.ts`) implement this exact contract.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Optional

from pydantic import BaseModel, Field

from quantara_options.contracts import (
    AIDecisionRecord,
    DataFeedTier,
    DecisionMode,
    OptionAccountSummary,
    OptionChainResponse,
    PayoffPoint,
    StrategyCandidate,
)


class OptionsLegView(BaseModel):
    id: str
    occ_symbol: Optional[str] = None
    underlying_symbol: str
    leg_role: str
    side: str
    right: Optional[str] = None
    strike: Optional[float] = None
    expiration_date: Optional[date] = None
    multiplier: int = 100
    quantity: int = 1
    entry_price: float
    current_mark: float
    bid: Optional[float] = None
    ask: Optional[float] = None
    unrealized_pnl: float
    realized_pnl: float = 0.0
    delta: Optional[float] = None
    gamma: Optional[float] = None
    theta: Optional[float] = None
    vega: Optional[float] = None
    implied_volatility: Optional[float] = None
    status: str = "open"


class OptionsStrategyGroupView(BaseModel):
    id: str
    account_slug: str
    strategy_type: str
    strategy_label_he: str
    underlying_symbol: str
    status: str
    mode: DecisionMode
    contracts_qty: int
    expiration_date: Optional[date] = None
    dte: Optional[int] = None
    is_credit: bool
    net_entry_cash_flow: float  # +credit received or -debit paid at open
    open_liability_usd: float   # cost to close short legs (always >= 0)
    long_value_usd: float       # market value of long legs
    collateral_locked_usd: float
    max_profit_usd: Optional[float] = None
    max_loss_usd: float
    unrealized_pnl: float
    realized_pnl: float
    fees_paid: float
    net_delta: float = 0.0
    net_theta: float = 0.0
    net_vega: float = 0.0
    pin_risk_warning: bool = False
    early_assignment_warning: bool = False
    wheel_state: Optional[str] = None
    opened_at: datetime
    closed_at: Optional[datetime] = None
    close_reason: Optional[str] = None
    ai_rationale: Optional[str] = None
    legs: list[OptionsLegView] = Field(default_factory=list)
    payoff_curve: list[PayoffPoint] = Field(default_factory=list)


class OptionsJournalEventView(BaseModel):
    id: str
    account_slug: str
    strategy_group_id: Optional[str] = None
    event_type: str  # OPEN, CLOSE, ROLL, EXPIRE, ASSIGN, EXERCISE, STOCK_DELIVERY, FEE
    underlying_symbol: str
    strategy_type: str
    cash_delta: float
    realized_pnl_delta: float
    collateral_delta: float
    fee_usd: float
    description: str
    created_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)


class OptionsExpirationItem(BaseModel):
    strategy_group_id: str
    account_slug: str
    underlying_symbol: str
    strategy_type: str
    expiration_date: date
    dte: int
    strikes: list[float] = Field(default_factory=list)
    underlying_price: Optional[float] = None
    itm_status: str = "OTM"  # OTM | ATM_PIN_RISK | ITM
    collateral_locked_usd: float
    unrealized_pnl: float
    warning_level: str = "normal"  # normal | watch | urgent


class OptionsEngineStatusView(BaseModel):
    enabled: bool
    running_cycle: bool = False
    mode: DecisionMode = DecisionMode.AI
    feed_tier: DataFeedTier = DataFeedTier.ALPACA_INDICATIVE
    is_indicative: bool = True
    is_delayed_trades: bool = True
    market_open: bool = False
    data_freshness_seconds: Optional[float] = None
    last_cycle_at: Optional[datetime] = None
    last_error: Optional[str] = None
    groq_available: bool = False
    groq_model: str = "openai/gpt-oss-120b"
    groq_calls_today: int = 0
    readiness_technical: bool = True
    readiness_market_data: bool = False
    readiness_forward_sim: bool = False
    universe: list[str] = Field(default_factory=list)


class OptionsStrategyComparisonRow(BaseModel):
    cohort_key: str
    label: str
    account_slug: str
    mode: DecisionMode
    strategy_scope: str
    starting_capital: float
    equity: float
    return_pct: float
    realized_pnl: float
    unrealized_pnl: float
    premium_collected_gross: float
    premium_realized_net: float
    open_liability_usd: float
    collateral_locked_usd: float
    max_drawdown_pct: float
    win_rate_pct: Optional[float] = None
    profit_factor: Optional[float] = None
    open_count: int
    closed_count: int


class OptionsLabOverviewResponse(BaseModel):
    engine_status: OptionsEngineStatusView
    accounts: list[OptionAccountSummary]
    selected_account: Optional[OptionAccountSummary] = None
    open_strategies: list[OptionsStrategyGroupView] = Field(default_factory=list)
    closed_strategies: list[OptionsStrategyGroupView] = Field(default_factory=list)
    opportunities: list[StrategyCandidate] = Field(default_factory=list)
    ai_decisions: list[AIDecisionRecord] = Field(default_factory=list)
    journal: list[OptionsJournalEventView] = Field(default_factory=list)
    expirations: list[OptionsExpirationItem] = Field(default_factory=list)
    comparison: list[OptionsStrategyComparisonRow] = Field(default_factory=list)
    chain_preview: Optional[OptionChainResponse] = None


class OptionsControlRequest(BaseModel):
    action: str  # "start" | "stop" | "run_once"
    use_fixtures: bool = False
    symbols: Optional[list[str]] = None
    account_slug: Optional[str] = None


class OptionsSimulateOrderRequest(BaseModel):
    account_slug: str
    candidate: StrategyCandidate
