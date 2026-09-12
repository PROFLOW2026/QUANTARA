"""Domain types aligned with QUANTARA database model."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Optional
from uuid import UUID, uuid4


class SignalAction(str, Enum):
    BUY = "buy"
    SELL = "sell"
    HOLD = "hold"
    CLOSE = "close"
    MODIFY = "modify"


class Direction(str, Enum):
    LONG = "long"
    SHORT = "short"


class DecisionType(str, Enum):
    HOLD = "hold"
    BUY_SIGNAL = "buy_signal"
    SELL_SIGNAL = "sell_signal"
    CLOSE_SIGNAL = "close_signal"
    NO_SETUP = "no_setup"
    RISK_DENIED = "risk_denied"
    BROKER_CAPABILITY_DENIED = "broker_capability_denied"
    BROKER_REJECTED = "broker_rejected"
    RISK_APPROVED = "risk_approved"
    POSITION_OPEN = "position_open"
    TRADING_HALTED = "trading_halted"
    EXECUTION_FAILED = "execution_failed"
    SL_TRIGGERED = "sl_triggered"
    TP_TRIGGERED = "tp_triggered"
    SYSTEM_ERROR = "system_error"


class ExitReason(str, Enum):
    SL = "sl"
    TP = "tp"
    STRATEGY = "strategy"
    MANUAL = "manual"
    RISK_HALT = "risk_halt"
    END_OF_BACKTEST = "end_of_backtest"


class OrderStatus(str, Enum):
    PENDING = "pending"
    SUBMITTED = "submitted"
    FILLED = "filled"
    PARTIAL = "partial"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


class PositionStatus(str, Enum):
    OPEN = "open"
    CLOSED = "closed"


class PortfolioStatus(str, Enum):
    ACTIVE = "active"
    HALTED = "halted"
    CLOSED = "closed"


class IntentStatus(str, Enum):
    PENDING_EXECUTION = "pending_execution"
    EXECUTED = "executed"
    REJECTED = "rejected"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class Mode(str, Enum):
    BACKTEST = "backtest"
    PAPER = "paper"
    LIVE_MANUAL = "live_manual"
    LIVE_AUTOMATED = "live_automated"


@dataclass(frozen=True)
class Candle:
    instrument_id: str
    timeframe: str
    timestamp: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Optional[Decimal] = None
    source: str = "mock"
    is_complete: bool = True


@dataclass(frozen=True)
class Signal:
    action: SignalAction
    reason: str
    confidence: Optional[Decimal] = None
    suggested_sl: Optional[Decimal] = None
    suggested_tp: Optional[Decimal] = None
    metadata: Optional[dict[str, Any]] = None


@dataclass(frozen=True)
class StrategyContext:
    instrument_id: str
    timeframe: str
    parameters: dict[str, Any]
    runtime: dict[str, Any] = field(default_factory=dict)


@dataclass
class Instrument:
    id: str
    symbol: str
    name: str
    asset_class: str = "commodity"
    base_currency: str = "XAU"
    quote_currency: str = "USD"
    pip_size: Decimal = Decimal("0.01")
    contract_size: Decimal = Decimal("100")
    price_tick_size: Decimal = Decimal("0.01")
    quantity_step: Decimal = Decimal("0.01")
    min_quantity: Decimal = Decimal("0.01")
    is_active: bool = True


@dataclass
class RiskProfile:
    id: str
    slug: str
    name: str
    risk_per_trade_pct: Decimal
    max_open_positions: int
    max_total_exposure_pct: Decimal
    daily_loss_limit_pct: Decimal
    max_drawdown_pct: Decimal
    parameters: dict[str, Any] = field(default_factory=dict)


@dataclass
class Portfolio:
    id: str
    name: str
    mode: Mode
    initial_capital: Decimal
    balance: Decimal
    unrealized_pnl: Decimal = Decimal("0")
    equity: Decimal = Decimal("0")
    exposure_notional: Decimal = Decimal("0")
    reserved_capital: Decimal = Decimal("0")
    currency: str = "USD"
    status: PortfolioStatus = PortfolioStatus.ACTIVE
    halt_reason: Optional[str] = None
    peak_equity: Decimal = Decimal("0")

    def __post_init__(self) -> None:
        if self.equity == Decimal("0"):
            self.equity = self.balance
        if self.peak_equity == Decimal("0"):
            self.peak_equity = self.equity


@dataclass
class StrategyInstance:
    id: str
    portfolio_id: str
    strategy_version_id: str
    strategy_slug: str
    strategy_version: str
    instrument_id: str
    timeframe: str
    risk_profile_id: str
    parameter_overrides: dict[str, Any] = field(default_factory=dict)
    is_active: bool = True


@dataclass
class OrderIntent:
    id: str
    signal_id: str
    strategy_instance_id: str
    portfolio_id: str
    direction: Direction
    quantity: Decimal
    stop_loss: Decimal
    take_profit: Optional[Decimal]
    target_risk_amount: Decimal
    actual_risk_amount: Decimal
    signal_candle_timestamp: datetime
    execution_candle_timestamp: Optional[datetime] = None
    risk_profile_id: str = ""
    status: IntentStatus = IntentStatus.PENDING_EXECUTION
    entry_type: str = "market"
    limit_price: Optional[Decimal] = None
    is_close: bool = False
    position_id: Optional[str] = None


@dataclass
class Order:
    id: str
    intent_id: str
    portfolio_id: str
    instrument_id: str
    direction: Direction
    quantity: Decimal
    status: OrderStatus = OrderStatus.PENDING
    submitted_at: Optional[datetime] = None
    broker_order_id: Optional[str] = None


@dataclass
class Fill:
    id: str
    order_id: str
    fill_price: Decimal
    fill_quantity: Decimal
    fees: Decimal = Decimal("0")
    slippage: Decimal = Decimal("0")
    spread_cost: Decimal = Decimal("0")
    base_price: Optional[Decimal] = None
    side: str = "entry"
    filled_at: Optional[datetime] = None
    position_id: Optional[str] = None


@dataclass
class Position:
    id: str
    portfolio_id: str
    strategy_instance_id: str
    instrument_id: str
    direction: Direction
    quantity: Decimal
    entry_price: Decimal
    stop_loss: Decimal
    take_profit: Optional[Decimal]
    current_price: Decimal
    unrealized_pnl: Decimal = Decimal("0")
    status: PositionStatus = PositionStatus.OPEN
    opened_at: Optional[datetime] = None
    closed_at: Optional[datetime] = None
    entry_fees: Decimal = Decimal("0")
    entry_slippage: Decimal = Decimal("0")
    entry_spread: Decimal = Decimal("0")
    target_risk_amount: Decimal = Decimal("0")
    actual_risk_amount: Decimal = Decimal("0")
    strategy_version_id: str = ""
    # Shadow vs physical: shadow_quantity = strategy experiment qty; physical_attributed_qty = broker FIFO lot
    shadow_quantity: Decimal | None = None
    physical_attributed_qty: Decimal | None = None


@dataclass
class Trade:
    id: str
    position_id: str
    portfolio_id: str
    strategy_instance_id: str
    strategy_version_id: str
    instrument_id: str
    direction: Direction
    quantity: Decimal
    entry_price: Decimal
    exit_price: Decimal
    gross_pnl: Decimal
    realized_pnl: Decimal
    fees_total: Decimal
    slippage_total: Decimal
    spread_total: Decimal
    target_risk_amount: Decimal
    actual_risk_amount: Decimal
    exit_reason: ExitReason
    duration_seconds: int
    opened_at: datetime
    closed_at: datetime


@dataclass
class DecisionLogEntry:
    id: str
    strategy_instance_id: str
    instrument_id: str
    candle_timestamp: datetime
    decision_type: DecisionType
    message: str
    signal_id: Optional[str] = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ExecutionAssumptions:
    spread: Decimal = Decimal("0.30")
    slippage_pct: Decimal = Decimal("0.0001")
    slippage_per_side: Decimal | None = None
    fee_rate: Decimal = Decimal("0")
    fill_timing: str = "next_open"


def new_id() -> str:
    return str(uuid4())
