"""Backtest metrics computation."""

from __future__ import annotations

from decimal import Decimal

from quantara_engine.domain.types import Trade
from quantara_engine.portfolio.service import PortfolioSnapshot


def compute_backtest_metrics(
    initial_capital: Decimal,
    final_equity: Decimal,
    trades: list[Trade],
    snapshots: list[PortfolioSnapshot],
    strategy_version: str,
    parameters: dict,
    dataset_fingerprint: str,
    execution_assumptions: dict,
) -> dict:
    total_trades = len(trades)
    wins = [t for t in trades if t.realized_pnl > 0]
    losses = [t for t in trades if t.realized_pnl <= 0]
    winning_trades = len(wins)
    losing_trades = len(losses)
    win_rate = (winning_trades / total_trades * 100) if total_trades else 0.0

    avg_win = float(sum(t.realized_pnl for t in wins) / len(wins)) if wins else 0.0
    avg_loss = float(sum(t.realized_pnl for t in losses) / len(losses)) if losses else 0.0

    sum_wins = sum(t.realized_pnl for t in wins)
    sum_losses = sum(t.realized_pnl for t in losses)
    if sum_losses != 0:
        profit_factor = float(sum_wins / abs(sum_losses))
    elif sum_wins:
        profit_factor = None  # no losses — JSON-safe (not Infinity)
    else:
        profit_factor = 0.0

    loss_rate = losing_trades / total_trades if total_trades else 0.0
    win_rate_frac = winning_trades / total_trades if total_trades else 0.0
    expectancy = win_rate_frac * avg_win + loss_rate * avg_loss

    max_dd = 0.0
    for snap in snapshots:
        max_dd = max(max_dd, float(snap.drawdown_pct))

    total_return_pct = (
        float((final_equity - initial_capital) / initial_capital * 100)
        if initial_capital
        else 0.0
    )

    return {
        "initial_capital": float(initial_capital),
        "final_capital": float(final_equity),
        "total_return_pct": total_return_pct,
        "total_trades": total_trades,
        "winning_trades": winning_trades,
        "losing_trades": losing_trades,
        "win_rate": win_rate,
        "average_win": avg_win,
        "average_loss": avg_loss,
        "profit_factor": profit_factor,
        "maximum_drawdown_pct": max_dd,
        "best_trade": float(max((t.realized_pnl for t in trades), default=Decimal("0"))),
        "worst_trade": float(min((t.realized_pnl for t in trades), default=Decimal("0"))),
        "average_duration_seconds": (
            sum(t.duration_seconds for t in trades) / total_trades if total_trades else 0
        ),
        "total_fees": float(sum(t.fees_total for t in trades)),
        "total_slippage": float(sum(t.slippage_total for t in trades)),
        "total_spread": float(sum(t.spread_total for t in trades)),
        "expectancy": expectancy,
        "strategy_version": strategy_version,
        "parameters": parameters,
        "dataset_fingerprint": dataset_fingerprint,
        "execution_assumptions": execution_assumptions,
    }
