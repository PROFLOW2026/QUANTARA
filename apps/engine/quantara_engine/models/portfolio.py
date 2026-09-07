import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import Boolean, DateTime, Integer, Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from quantara_engine.models.base import Base
from quantara_engine.models.enums import (
    BacktestStatus,
    ExperimentStatus,
    PortfolioMode,
    PortfolioStatus,
    RiskProfileSlug,
    Timeframe,
)
from quantara_engine.models.types import pg_enum


class RiskProfile(Base):
    __tablename__ = "risk_profiles"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(50), nullable=False)
    slug: Mapped[RiskProfileSlug] = mapped_column(
        pg_enum(RiskProfileSlug, "risk_profile_slug"), unique=True, nullable=False
    )
    risk_per_trade_pct: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    max_open_positions: Mapped[int] = mapped_column(Integer, nullable=False)
    max_total_exposure_pct: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    daily_loss_limit_pct: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    max_drawdown_pct: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    volatility_limit_atr_mult: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    parameters: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Portfolio(Base):
    __tablename__ = "portfolios"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    owner_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    mode: Mapped[PortfolioMode] = mapped_column(
        pg_enum(PortfolioMode, "portfolio_mode"), nullable=False
    )
    initial_capital: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    balance: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    unrealized_pnl: Mapped[Decimal] = mapped_column(
        Numeric(18, 2), nullable=False, default=Decimal("0")
    )
    equity: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    exposure_notional: Mapped[Decimal] = mapped_column(
        Numeric(18, 2), nullable=False, default=Decimal("0")
    )
    reserved_capital: Mapped[Decimal] = mapped_column(
        Numeric(18, 2), nullable=False, default=Decimal("0")
    )
    currency: Mapped[str] = mapped_column(String(10), nullable=False, default="USD")
    status: Mapped[PortfolioStatus] = mapped_column(
        pg_enum(PortfolioStatus, "portfolio_status"),
        nullable=False,
        default=PortfolioStatus.ACTIVE,
    )
    halt_reason: Mapped[Optional[str]] = mapped_column(Text)
    peak_equity: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Experiment(Base):
    __tablename__ = "experiments"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)
    instrument_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    status: Mapped[ExperimentStatus] = mapped_column(
        pg_enum(ExperimentStatus, "experiment_status"),
        nullable=False,
        default=ExperimentStatus.DRAFT,
    )
    start_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_date: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class StrategyInstance(Base):
    __tablename__ = "strategy_instances"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    portfolio_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    strategy_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    instrument_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    timeframe: Mapped[Timeframe] = mapped_column(
        pg_enum(Timeframe, "timeframe"), nullable=False
    )
    risk_profile_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    parameter_overrides: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    experiment_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class PortfolioSnapshot(Base):
    __tablename__ = "portfolio_snapshots"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    portfolio_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    balance: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    equity: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    exposure_notional: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    reserved_capital: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    unrealized_pnl: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    drawdown_pct: Mapped[Decimal] = mapped_column(Numeric(8, 4), nullable=False)
    open_positions_count: Mapped[int] = mapped_column(Integer, nullable=False)
    metadata_: Mapped[Optional[dict[str, Any]]] = mapped_column("metadata", JSONB)
    mode: Mapped[PortfolioMode] = mapped_column(
        pg_enum(PortfolioMode, "portfolio_mode"), nullable=False
    )
    backtest_run_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class BacktestRun(Base):
    __tablename__ = "backtest_runs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    portfolio_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    strategy_instance_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    strategy_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    instrument_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    timeframe: Mapped[Timeframe] = mapped_column(
        pg_enum(Timeframe, "timeframe"), nullable=False
    )
    start_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    initial_capital: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    final_capital: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 2))
    status: Mapped[BacktestStatus] = mapped_column(
        pg_enum(BacktestStatus, "backtest_status"),
        nullable=False,
        default=BacktestStatus.PENDING,
    )
    parameters: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    execution_assumptions: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    dataset_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    metrics: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    error_message: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
