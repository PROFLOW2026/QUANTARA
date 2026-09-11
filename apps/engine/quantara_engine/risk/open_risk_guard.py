"""Aggregate open-risk limits before approving new entries."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING

from quantara_engine.domain.types import Instrument, Portfolio, Position, StrategyInstance

if TYPE_CHECKING:
    from quantara_engine.persistence.store import TradingStore


@dataclass(frozen=True)
class OpenRiskLimits:
    max_portfolio_open_risk_pct: Decimal
    max_asset_open_risk_pct: Decimal
    max_strategy_asset_open_risk_pct: Decimal
    max_global_asset_open_risk_pct: Decimal | None = None


DEFAULT_OPEN_RISK_LIMITS = OpenRiskLimits(
    max_portfolio_open_risk_pct=Decimal("10"),
    max_asset_open_risk_pct=Decimal("6"),
    max_strategy_asset_open_risk_pct=Decimal("4"),
    max_global_asset_open_risk_pct=Decimal("2"),
)


def sum_open_risk_usd(positions: list[Position]) -> Decimal:
    return sum((p.actual_risk_amount for p in positions), Decimal("0")).quantize(Decimal("0.01"))


def evaluate_open_risk_guard(
    *,
    portfolio: Portfolio,
    open_positions: list[Position],
    instrument: Instrument,
    strategy_instance: StrategyInstance,
    incremental_risk_usd: Decimal,
    limits: OpenRiskLimits = DEFAULT_OPEN_RISK_LIMITS,
) -> tuple[bool, str | None]:
    """
    Per-portfolio guards — all percentages are relative to THIS portfolio's equity.

    Scope:
    - portfolio: all open positions in this portfolio
    - asset: open positions on this instrument inside this portfolio
    - strategy+asset: open positions on this instrument for this strategy instance
    """
    if portfolio.equity <= 0:
        return False, "INVALID_EQUITY"

    portfolio_risk = sum_open_risk_usd(open_positions) + incremental_risk_usd
    portfolio_pct = portfolio_risk / portfolio.equity * Decimal("100")
    if portfolio_pct > limits.max_portfolio_open_risk_pct:
        return False, (
            f"MAX_PORTFOLIO_OPEN_RISK ({portfolio_pct:.2f}% > "
            f"{limits.max_portfolio_open_risk_pct}%)"
        )

    asset_positions = [p for p in open_positions if p.instrument_id == instrument.id]
    asset_risk = sum_open_risk_usd(asset_positions) + incremental_risk_usd
    asset_pct = asset_risk / portfolio.equity * Decimal("100")
    if asset_pct > limits.max_asset_open_risk_pct:
        return False, (
            f"MAX_ASSET_OPEN_RISK ({asset_pct:.2f}% > {limits.max_asset_open_risk_pct}%)"
        )

    strat_asset = [
        p
        for p in open_positions
        if p.instrument_id == instrument.id
        and p.strategy_instance_id == strategy_instance.id
    ]
    strat_risk = sum_open_risk_usd(strat_asset) + incremental_risk_usd
    strat_pct = strat_risk / portfolio.equity * Decimal("100")
    if strat_pct > limits.max_strategy_asset_open_risk_pct:
        return False, (
            f"MAX_STRATEGY_ASSET_OPEN_RISK ({strat_pct:.2f}% > "
            f"{limits.max_strategy_asset_open_risk_pct}%)"
        )

    return True, None


def evaluate_global_asset_open_risk(
    *,
    global_open_positions_on_asset: list[Position],
    incremental_risk_usd: Decimal,
    asset_allocated_equity_usd: Decimal,
    limits: OpenRiskLimits = DEFAULT_OPEN_RISK_LIMITS,
) -> tuple[bool, str | None]:
    """
    System-wide asset guard across competition portfolios allocated to this asset.

    Percentage = sum(expected open risk at SL) / asset-allocated equity.
    """
    if limits.max_global_asset_open_risk_pct is None:
        return True, None
    if asset_allocated_equity_usd <= 0:
        return False, "INVALID_ASSET_ALLOCATED_EQUITY"

    global_risk = sum_open_risk_usd(global_open_positions_on_asset) + incremental_risk_usd
    global_pct = global_risk / asset_allocated_equity_usd * Decimal("100")
    if global_pct > limits.max_global_asset_open_risk_pct:
        return False, (
            f"MAX_GLOBAL_ASSET_OPEN_RISK ({global_pct:.2f}% > "
            f"{limits.max_global_asset_open_risk_pct}%)"
        )
    return True, None


def load_global_risk_context(
    store: TradingStore,
    instrument_id: str,
    db_symbol: str,
) -> tuple[list[Position], Decimal]:
    """Canonical OPEN positions on instrument + asset-allocated competition equity."""
    from quantara_engine.competition.asset_equity import (
        nominal_asset_allocated_equity,
        portfolio_ids_for_symbol,
    )
    from quantara_engine.models.enums import PositionStatus as OrmPositionStatus
    from quantara_engine.models.portfolio import Portfolio as OrmPortfolio
    from quantara_engine.models.trading import Position as OrmPosition
    from sqlalchemy import func, select

    allocated_ids = portfolio_ids_for_symbol(db_symbol)
    if not allocated_ids:
        return [], nominal_asset_allocated_equity(db_symbol)

    rows = store.session.scalars(
        select(OrmPosition).where(
            OrmPosition.instrument_id == instrument_id,
            OrmPosition.status == OrmPositionStatus.OPEN,
            OrmPosition.portfolio_id.in_(allocated_ids),
        )
    ).all()
    positions = [store._position_to_domain(row) for row in rows]
    store._hydrate_position_strategy_versions(positions)
    store.hydrate_position_risk_from_intents(positions)

    total_equity = store.session.scalar(
        select(func.coalesce(func.sum(OrmPortfolio.equity), 0)).where(
            OrmPortfolio.id.in_(allocated_ids)
        )
    )
    equity = Decimal(str(total_equity or 0))
    if equity <= 0:
        equity = nominal_asset_allocated_equity(db_symbol)
    return positions, equity


def evaluate_all_open_risk_guards(
    *,
    store: TradingStore | None,
    portfolio: Portfolio,
    open_positions: list[Position],
    instrument: Instrument,
    strategy_instance: StrategyInstance,
    incremental_risk_usd: Decimal,
    limits: OpenRiskLimits = DEFAULT_OPEN_RISK_LIMITS,
) -> tuple[bool, str | None]:
    ok, reason = evaluate_open_risk_guard(
        portfolio=portfolio,
        open_positions=open_positions,
        instrument=instrument,
        strategy_instance=strategy_instance,
        incremental_risk_usd=incremental_risk_usd,
        limits=limits,
    )
    if not ok:
        return ok, reason

    if store is None:
        return True, None

    global_positions, asset_equity = load_global_risk_context(
        store, instrument.id, instrument.symbol
    )
    return evaluate_global_asset_open_risk(
        global_open_positions_on_asset=global_positions,
        incremental_risk_usd=incremental_risk_usd,
        asset_allocated_equity_usd=asset_equity,
        limits=limits,
    )
