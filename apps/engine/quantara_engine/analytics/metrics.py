"""Analytics metrics — expectancy with signed avg_loss."""

from __future__ import annotations

from decimal import Decimal

from quantara_engine.domain.types import Trade
from quantara_engine.portfolio.service import PortfolioSnapshot


def compute_trade_statistics(trades: list[Trade]) -> dict:
    total = len(trades)
    wins = [t for t in trades if t.realized_pnl > 0]
    losses = [t for t in trades if t.realized_pnl <= 0]
    winning = len(wins)
    losing = len(losses)
    win_rate = (winning / total * 100) if total else 0.0

    avg_win = float(sum(t.realized_pnl for t in wins) / len(wins)) if wins else 0.0
    avg_loss = float(sum(t.realized_pnl for t in losses) / len(losses)) if losses else 0.0

    sum_wins = sum(t.realized_pnl for t in wins)
    sum_losses = sum(t.realized_pnl for t in losses)
    if sum_losses != 0:
        profit_factor = float(sum_wins / abs(sum_losses))
    elif sum_wins:
        profit_factor = None
    else:
        profit_factor = 0.0

    win_rate_frac = winning / total if total else 0.0
    loss_rate = losing / total if total else 0.0
    expectancy = win_rate_frac * avg_win + loss_rate * avg_loss

    return {
        "total_trades": total,
        "winning_trades": winning,
        "losing_trades": losing,
        "win_rate": win_rate,
        "average_win": avg_win,
        "average_loss": avg_loss,
        "profit_factor": profit_factor,
        "expectancy": expectancy,
        "largest_win": float(max((t.realized_pnl for t in trades), default=Decimal("0"))),
        "largest_loss": float(min((t.realized_pnl for t in trades), default=Decimal("0"))),
        "total_fees": float(sum(t.fees_total for t in trades)),
        "total_slippage": float(sum(t.slippage_total for t in trades)),
        "total_spread": float(sum(t.spread_total for t in trades)),
    }


def compute_drawdown_series(snapshots: list[PortfolioSnapshot]) -> list[dict]:
    peak = Decimal("0")
    series = []
    for snap in sorted(snapshots, key=lambda s: s.timestamp):
        peak = max(peak, snap.equity)
        dd = (
            ((peak - snap.equity) / peak * Decimal("100")).quantize(Decimal("0.0001"))
            if peak > 0
            else Decimal("0")
        )
        series.append(
            {
                "timestamp": snap.timestamp.isoformat(),
                "drawdown_pct": float(dd),
                "equity": float(snap.equity),
                "peak": float(peak),
            }
        )
    return series


def compute_portfolio_metrics(
    initial_capital: Decimal,
    final_equity: Decimal,
    trades: list[Trade],
    snapshots: list[PortfolioSnapshot],
) -> dict:
    stats = compute_trade_statistics(trades)
    total_return_pct = (
        float((final_equity - initial_capital) / initial_capital * 100)
        if initial_capital
        else 0.0
    )
    max_dd = max((float(s.drawdown_pct) for s in snapshots), default=0.0)
    return {
        **stats,
        "initial_capital": float(initial_capital),
        "final_equity": float(final_equity),
        "total_return": float(final_equity - initial_capital),
        "total_return_pct": total_return_pct,
        "maximum_drawdown_pct": max_dd,
    }
