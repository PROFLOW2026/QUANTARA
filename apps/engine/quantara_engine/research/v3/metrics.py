"""Trade-list metrics for V3 research (1R-normalized where noted)."""

from __future__ import annotations

from statistics import mean
from typing import Any


def profit_factor(pnls: list[float]) -> float | None:
    wins = sum(p for p in pnls if p > 0)
    losses = sum(p for p in pnls if p < 0)
    if losses:
        return round(wins / abs(losses), 4)
    return None if wins else 0.0


def trade_metrics(pnls: list[float], *, risk_usd: list[float] | None = None) -> dict[str, Any]:
    if not pnls:
        return {
            "trades": 0,
            "pf": None,
            "expectancy": None,
            "expectancy_r": None,
            "win_rate_pct": None,
        }
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    exp = mean(pnls)
    exp_r = None
    if risk_usd and len(risk_usd) == len(pnls):
        rs = [p / r if r and r > 0 else 0.0 for p, r in zip(pnls, risk_usd)]
        exp_r = round(mean(rs), 4)
    streak = 0
    max_streak = 0
    for p in pnls:
        if p < 0:
            streak += 1
            max_streak = max(max_streak, streak)
        else:
            streak = 0
    return {
        "trades": len(pnls),
        "pf": profit_factor(pnls),
        "expectancy": round(exp, 4),
        "expectancy_r": exp_r,
        "win_rate_pct": round(len(wins) / len(pnls) * 100, 2),
        "avg_winner": round(mean(wins), 4) if wins else None,
        "avg_loser": round(mean(losses), 4) if losses else None,
        "payoff_ratio": round(abs(mean(wins) / mean(losses)), 4)
        if wins and losses
        else None,
        "max_consecutive_losses": max_streak,
        "largest_win": round(max(pnls), 4),
        "largest_loss": round(min(pnls), 4),
    }
