"""PostgreSQL enum names used across models."""

import enum


class AssetClass(str, enum.Enum):
    COMMODITY = "commodity"
    FOREX = "forex"
    INDEX = "index"
    CRYPTO = "crypto"
    STOCK = "stock"


class Timeframe(str, enum.Enum):
    M5 = "5m"
    M15 = "15m"
    H1 = "1h"


class StrategyStatus(str, enum.Enum):
    DRAFT = "draft"
    ACTIVE = "active"
    DEPRECATED = "deprecated"
    ARCHIVED = "archived"


class RiskProfileSlug(str, enum.Enum):
    VERY_CONSERVATIVE = "very_conservative"
    CONSERVATIVE = "conservative"
    BALANCED = "balanced"
    AGGRESSIVE = "aggressive"
    VERY_AGGRESSIVE = "very_aggressive"


class PortfolioMode(str, enum.Enum):
    BACKTEST = "backtest"
    PAPER = "paper"
    LIVE_MANUAL = "live_manual"
    LIVE_AUTOMATED = "live_automated"


class PortfolioStatus(str, enum.Enum):
    ACTIVE = "active"
    HALTED = "halted"
    CLOSED = "closed"


class SignalAction(str, enum.Enum):
    BUY = "buy"
    SELL = "sell"
    HOLD = "hold"
    CLOSE = "close"
    MODIFY = "modify"


class DecisionType(str, enum.Enum):
    HOLD = "hold"
    BUY_SIGNAL = "buy_signal"
    SELL_SIGNAL = "sell_signal"
    CLOSE_SIGNAL = "close_signal"
    NO_SETUP = "no_setup"
    RISK_DENIED = "risk_denied"
    RISK_APPROVED = "risk_approved"
    POSITION_OPEN = "position_open"
    TRADING_HALTED = "trading_halted"
    EXECUTION_FAILED = "execution_failed"
    SL_TRIGGERED = "sl_triggered"
    TP_TRIGGERED = "tp_triggered"
    SYSTEM_ERROR = "system_error"


class Direction(str, enum.Enum):
    LONG = "long"
    SHORT = "short"


class EntryType(str, enum.Enum):
    MARKET = "market"
    LIMIT = "limit"


class OrderIntentStatus(str, enum.Enum):
    PENDING_EXECUTION = "pending_execution"
    EXECUTED = "executed"
    REJECTED = "rejected"
    EXPIRED = "expired"


class OrderType(str, enum.Enum):
    MARKET = "market"
    LIMIT = "limit"


class OrderStatus(str, enum.Enum):
    PENDING = "pending"
    SUBMITTED = "submitted"
    FILLED = "filled"
    PARTIAL = "partial"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


class FillSide(str, enum.Enum):
    ENTRY = "entry"
    EXIT = "exit"


class PositionStatus(str, enum.Enum):
    OPEN = "open"
    CLOSED = "closed"


class ExitReason(str, enum.Enum):
    SL = "sl"
    TP = "tp"
    STRATEGY = "strategy"
    MANUAL = "manual"
    RISK_HALT = "risk_halt"
    END_OF_BACKTEST = "end_of_backtest"


class BacktestStatus(str, enum.Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class ExperimentStatus(str, enum.Enum):
    DRAFT = "draft"
    RUNNING = "running"
    COMPLETED = "completed"


class JobType(str, enum.Enum):
    FETCH_DATA = "fetch_data"
    RUN_STRATEGY = "run_strategy"
    CHECK_SL_TP = "check_sl_tp"
    SNAPSHOT = "snapshot"
    BACKTEST = "backtest"


class WorkerJobStatus(str, enum.Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class WorkerRunStatus(str, enum.Enum):
    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"


class EventSeverity(str, enum.Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"
