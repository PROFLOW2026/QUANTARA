import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import DateTime, Integer, Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from quantara_engine.models.base import Base
from quantara_engine.models.enums import (
    DecisionType,
    Direction,
    EntryType,
    ExitReason,
    FillSide,
    OrderIntentStatus,
    OrderStatus,
    OrderType,
    PortfolioMode,
    PositionStatus,
    SignalAction,
)
from quantara_engine.models.types import pg_enum


class Signal(Base):
    __tablename__ = "signals"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    strategy_instance_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    strategy_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    instrument_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    candle_timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    action: Mapped[SignalAction] = mapped_column(
        pg_enum(SignalAction, "signal_action"), nullable=False
    )
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 4))
    suggested_sl: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 8))
    suggested_tp: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 8))
    metadata_: Mapped[Optional[dict[str, Any]]] = mapped_column("metadata", JSONB)
    mode: Mapped[PortfolioMode] = mapped_column(
        pg_enum(PortfolioMode, "portfolio_mode"), nullable=False
    )
    backtest_run_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Decision(Base):
    __tablename__ = "decisions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    strategy_instance_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    signal_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True))
    instrument_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    candle_timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    decision_type: Mapped[DecisionType] = mapped_column(
        pg_enum(DecisionType, "decision_type"), nullable=False
    )
    message: Mapped[str] = mapped_column(Text, nullable=False)
    metadata_: Mapped[Optional[dict[str, Any]]] = mapped_column("metadata", JSONB)
    mode: Mapped[PortfolioMode] = mapped_column(
        pg_enum(PortfolioMode, "portfolio_mode"), nullable=False
    )
    backtest_run_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class OrderIntent(Base):
    __tablename__ = "order_intents"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    signal_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    strategy_instance_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    portfolio_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    direction: Mapped[Direction] = mapped_column(
        pg_enum(Direction, "direction"), nullable=False
    )
    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)
    entry_type: Mapped[EntryType] = mapped_column(
        pg_enum(EntryType, "entry_type"), nullable=False
    )
    limit_price: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 8))
    stop_loss: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)
    take_profit: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 8))
    target_risk_amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    actual_risk_amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    signal_candle_timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    execution_candle_timestamp: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True)
    )
    risk_profile_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    status: Mapped[OrderIntentStatus] = mapped_column(
        pg_enum(OrderIntentStatus, "order_intent_status"),
        nullable=False,
        default=OrderIntentStatus.PENDING_EXECUTION,
    )
    rejection_reason: Mapped[Optional[str]] = mapped_column(Text)
    idempotency_key: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    mode: Mapped[PortfolioMode] = mapped_column(
        pg_enum(PortfolioMode, "portfolio_mode"), nullable=False
    )
    backtest_run_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    intent_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    portfolio_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    instrument_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    direction: Mapped[Direction] = mapped_column(
        pg_enum(Direction, "direction"), nullable=False
    )
    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)
    order_type: Mapped[OrderType] = mapped_column(
        pg_enum(OrderType, "order_type"), nullable=False
    )
    status: Mapped[OrderStatus] = mapped_column(
        pg_enum(OrderStatus, "order_status"),
        nullable=False,
        default=OrderStatus.PENDING,
    )
    broker_order_id: Mapped[Optional[str]] = mapped_column(String(100))
    submitted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    mode: Mapped[PortfolioMode] = mapped_column(
        pg_enum(PortfolioMode, "portfolio_mode"), nullable=False
    )
    backtest_run_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Position(Base):
    __tablename__ = "positions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    portfolio_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    strategy_instance_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    instrument_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    direction: Mapped[Direction] = mapped_column(
        pg_enum(Direction, "direction"), nullable=False
    )
    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)
    entry_price: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)
    current_price: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)
    stop_loss: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)
    take_profit: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 8))
    unrealized_pnl: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    status: Mapped[PositionStatus] = mapped_column(
        pg_enum(PositionStatus, "position_status"),
        nullable=False,
        default=PositionStatus.OPEN,
    )
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    closed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    mode: Mapped[PortfolioMode] = mapped_column(
        pg_enum(PortfolioMode, "portfolio_mode"), nullable=False
    )
    backtest_run_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Fill(Base):
    __tablename__ = "fills"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    order_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    position_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True))
    fill_price: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)
    fill_quantity: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)
    fees: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False, default=Decimal("0"))
    slippage: Mapped[Decimal] = mapped_column(
        Numeric(18, 8), nullable=False, default=Decimal("0")
    )
    spread_cost: Mapped[Decimal] = mapped_column(
        Numeric(18, 4), nullable=False, default=Decimal("0")
    )
    base_price: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 8))
    side: Mapped[FillSide] = mapped_column(
        pg_enum(FillSide, "fill_side"), nullable=False
    )
    filled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    mode: Mapped[PortfolioMode] = mapped_column(
        pg_enum(PortfolioMode, "portfolio_mode"), nullable=False
    )
    backtest_run_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Trade(Base):
    __tablename__ = "trades"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    position_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    portfolio_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    strategy_instance_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    strategy_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    instrument_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    direction: Mapped[Direction] = mapped_column(
        pg_enum(Direction, "direction"), nullable=False
    )
    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)
    entry_price: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)
    exit_price: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)
    gross_pnl: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    realized_pnl: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    fees_total: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    slippage_total: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)
    spread_total: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    target_risk_amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    actual_risk_amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    exit_reason: Mapped[ExitReason] = mapped_column(
        pg_enum(ExitReason, "exit_reason"), nullable=False
    )
    duration_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    closed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    mode: Mapped[PortfolioMode] = mapped_column(
        pg_enum(PortfolioMode, "portfolio_mode"), nullable=False
    )
    backtest_run_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
