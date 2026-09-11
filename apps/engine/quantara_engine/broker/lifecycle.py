"""Strategy shadow legs vs physical broker attribution lifecycle."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from quantara_engine.broker.attribution import attributed_remaining_quantity
from quantara_engine.broker.execution_service import PAPER_ACCOUNT_SLUG
from quantara_engine.domain.types import ExitReason, Position, new_id
from quantara_engine.execution.fill_calculator import FillResult
from quantara_engine.persistence.store import TradingStore
from quantara_engine.portfolio.currency import CurrencyContext


@dataclass(frozen=True)
class ShadowCloseResult:
    closed: bool
    trade: object | None = None


def _broker_account_id(store: TradingStore) -> str | None:
    from sqlalchemy import text

    row = store.session.execute(
        text("SELECT id::text FROM broker_accounts WHERE slug = :slug LIMIT 1"),
        {"slug": PAPER_ACCOUNT_SLUG},
    ).scalar()
    return str(row) if row else None


def physical_attributed_qty(
    store: TradingStore,
    *,
    strategy_position_id: str,
    symbol: str,
) -> Decimal:
    account_id = _broker_account_id(store)
    if not account_id:
        return Decimal("0")
    return attributed_remaining_quantity(
        store,
        broker_account_id=account_id,
        symbol=symbol,
        strategy_position_id=strategy_position_id,
    )


def is_shadow_only_position(store: TradingStore, position: Position, symbol: str) -> bool:
    return physical_attributed_qty(store, strategy_position_id=position.id, symbol=symbol) <= 0


def close_shadow_position(
    state,
    position: Position,
    *,
    exit_reason: ExitReason,
    closed_at,
    currency: CurrencyContext | None = None,
    exit_price: Decimal | None = None,
):
    """Close strategy shadow leg with no broker order."""
    price = exit_price or position.current_price or position.entry_price
    fill = FillResult(
        fill_price=price,
        base_price=price,
        spread_cost=Decimal("0"),
        slippage=Decimal("0"),
        fees=Decimal("0"),
    )
    return state.close_position(position, fill, exit_reason, closed_at, currency)


def open_strategy_leg_from_broker_fill(
    state,
    *,
    intent,
    fill: FillResult,
    order,
    strategy_version_id: str,
    instrument_id: str,
    filled_at,
    physical_opened_qty: Decimal,
) -> Position:
    """Open strategy leg; shadow qty = intent qty, physical = broker opened portion."""
    position = Position(
        id=new_id(),
        portfolio_id=state.portfolio.id,
        strategy_instance_id=intent.strategy_instance_id,
        instrument_id=instrument_id,
        direction=intent.direction,
        quantity=intent.quantity,
        entry_price=fill.fill_price,
        stop_loss=intent.stop_loss,
        take_profit=intent.take_profit,
        current_price=fill.fill_price,
        opened_at=filled_at,
        entry_fees=fill.fees,
        entry_slippage=fill.slippage,
        entry_spread=fill.spread_cost,
        target_risk_amount=intent.target_risk_amount,
        actual_risk_amount=intent.actual_risk_amount,
        strategy_version_id=strategy_version_id,
        shadow_quantity=intent.quantity,
        physical_attributed_qty=physical_opened_qty,
    )
    state.positions.append(position)
    return position
