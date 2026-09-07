"""Analytics service."""

from __future__ import annotations

from decimal import Decimal

from quantara_engine.analytics.metrics import (
    compute_drawdown_series,
    compute_portfolio_metrics,
    compute_trade_statistics,
)
from quantara_engine.domain.types import Trade
from quantara_engine.portfolio.service import PortfolioSnapshot


class AnalyticsService:
    def portfolio_analytics(
        self,
        initial_capital: Decimal,
        final_equity: Decimal,
        trades: list[Trade],
        snapshots: list[PortfolioSnapshot],
    ) -> dict:
        return compute_portfolio_metrics(initial_capital, final_equity, trades, snapshots)

    def strategy_analytics(self, trades: list[Trade], strategy_version_id: str) -> dict:
        filtered = [t for t in trades if t.strategy_version_id == strategy_version_id]
        return compute_trade_statistics(filtered)

    def equity_curve(self, snapshots: list[PortfolioSnapshot]) -> list[dict]:
        return [
            {
                "timestamp": s.timestamp.isoformat(),
                "equity": float(s.equity),
                "balance": float(s.balance),
                "unrealized_pnl": float(s.unrealized_pnl),
            }
            for s in sorted(snapshots, key=lambda x: x.timestamp)
        ]

    def drawdown(self, snapshots: list[PortfolioSnapshot]) -> list[dict]:
        return compute_drawdown_series(snapshots)
