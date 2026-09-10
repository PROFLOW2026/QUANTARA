"""Balance/equity reconciliation from canonical trade ledger."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from quantara_engine.domain.types import Portfolio

_TOLERANCE = Decimal("0.01")


@dataclass(frozen=True)
class BalanceReconciliationResult:
    portfolio_id: str
    initial_capital: Decimal
    stored_balance: Decimal
    canonical_balance: Decimal
    balance_difference: Decimal
    stored_equity: Decimal
    canonical_equity: Decimal
    equity_difference: Decimal
    realized_from_trades: Decimal
    unrealized_pnl: Decimal


def compute_canonical_balance(
    initial_capital: Decimal,
    realized_pnl_sum: Decimal,
) -> Decimal:
    return (initial_capital + realized_pnl_sum).quantize(_TOLERANCE)


def compute_canonical_equity(
    canonical_balance: Decimal,
    unrealized_pnl: Decimal,
) -> Decimal:
    return (canonical_balance + unrealized_pnl).quantize(_TOLERANCE)


def reconcile_portfolio_balance(
    portfolio: Portfolio,
    realized_pnl_sum: Decimal,
) -> BalanceReconciliationResult:
    canonical_balance = compute_canonical_balance(portfolio.initial_capital, realized_pnl_sum)
    canonical_equity = compute_canonical_equity(canonical_balance, portfolio.unrealized_pnl)
    return BalanceReconciliationResult(
        portfolio_id=portfolio.id,
        initial_capital=portfolio.initial_capital,
        stored_balance=portfolio.balance,
        canonical_balance=canonical_balance,
        balance_difference=(portfolio.balance - canonical_balance).quantize(_TOLERANCE),
        stored_equity=portfolio.equity,
        canonical_equity=canonical_equity,
        equity_difference=(portfolio.equity - canonical_equity).quantize(_TOLERANCE),
        realized_from_trades=realized_pnl_sum.quantize(_TOLERANCE),
        unrealized_pnl=portfolio.unrealized_pnl.quantize(_TOLERANCE),
    )


def apply_canonical_balance(portfolio: Portfolio, realized_pnl_sum: Decimal) -> Portfolio:
    """Mutate portfolio balance/equity to match trade ledger + current unrealized."""
    portfolio.balance = compute_canonical_balance(portfolio.initial_capital, realized_pnl_sum)
    portfolio.equity = compute_canonical_equity(portfolio.balance, portfolio.unrealized_pnl)
    return portfolio
