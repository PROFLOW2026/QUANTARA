"""In-memory V3 backtests with realistic execution costs."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from quantara_engine.backtesting.runner import BacktestRun, BacktestRunner
from quantara_engine.domain.types import Direction, Instrument, RiskProfile, StrategyInstance, Trade
from quantara_engine.execution.cost_profile import execution_assumptions_for
from quantara_engine.research.v3.candidates import V3Candidate
from quantara_engine.research.v3.metrics import profit_factor, trade_metrics


def normalized_risk_profile() -> RiskProfile:
    return RiskProfile(
        id="v3-1r",
        slug="v3-normalized-1r",
        name="V3 normalized 1R",
        risk_per_trade_pct=Decimal("1"),
        max_open_positions=3,
        max_total_exposure_pct=Decimal("100"),
        daily_loss_limit_pct=Decimal("20"),
        max_drawdown_pct=Decimal("50"),
        parameters={},
    )


def run_candidate_backtest(
    *,
    candidate: V3Candidate,
    instrument: Instrument,
    timeframe: str,
    candles: list,
) -> dict[str, Any]:
    if len(candles) < 250:
        return {"error": "insufficient_candles", "trades": []}

    assumptions = execution_assumptions_for(instrument, candles[-1].close)
    instance = StrategyInstance(
        id=str(uuid.uuid4()),
        portfolio_id=str(uuid.uuid4()),
        strategy_version_id=str(uuid.uuid4()),
        strategy_slug=candidate.strategy_slug,
        strategy_version=candidate.version,
        instrument_id=instrument.id,
        timeframe=timeframe,
        risk_profile_id="v3-1r",
        parameter_overrides=dict(candidate.parameters),
        is_active=True,
    )
    run = BacktestRun(
        id=str(uuid.uuid4()),
        strategy_instance=instance,
        instrument=instrument,
        risk_profile=normalized_risk_profile(),
        candles=candles,
        initial_capital=Decimal("10000"),
        execution_assumptions=assumptions,
    )
    BacktestRunner().run(run, store=None)
    trades: list[Trade] = run.trades or []
    return {
        "metrics": run.metrics,
        "trades": trades,
        "status": str(run.status),
        "error": run.error_message,
    }


def trades_to_r_pnls(trades: list[Trade], direction: str | None = None) -> tuple[list[float], list[float]]:
    pnls: list[float] = []
    risks: list[float] = []
    for t in trades:
        dir_val = t.direction.value if hasattr(t.direction, "value") else str(t.direction)
        if direction and dir_val != direction:
            continue
        risk = float(t.actual_risk_amount or t.target_risk_amount or 0)
        if risk <= 0:
            continue
        pnls.append(float(t.realized_pnl))
        risks.append(risk)
    return pnls, risks


def direction_metrics(trades: list[Trade]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for d in ("long", "short"):
        pnls, risks = trades_to_r_pnls(trades, d)
        rs = [p / r for p, r in zip(pnls, risks)] if pnls else []
        m = trade_metrics(pnls, risk_usd=risks)
        m["expectancy_r"] = round(sum(rs) / len(rs), 4) if rs else None
        m["pf"] = profit_factor(pnls)
        out[d] = m
    return out


def filter_trades_by_window(trades: list[Trade], start: datetime, end: datetime) -> list[Trade]:
    return [t for t in trades if t.closed_at and start <= t.closed_at < end]
