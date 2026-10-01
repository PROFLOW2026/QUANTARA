"""Chronological shared-capital portfolio sim with Live Sim V2 entry gates."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from quantara_engine.live_sim.constants import (
    DEFAULT_DAILY_LOSS_GATE_PCT,
    DEFAULT_MAX_DRAWDOWN_GATE_PCT,
    DEFAULT_MAX_GROUP_SL_RISK_PCT,
    DEFAULT_MAX_SYMBOL_SL_RISK_PCT,
    DEFAULT_MAX_TOTAL_OPEN_SL_RISK_PCT,
)
from quantara_engine.live_sim.offline_replay import ReplayState
from quantara_engine.live_sim.risk_policy import (
    LiveSimRiskSettings,
    OpenRiskSnapshot,
    evaluate_entry_gates,
    symbol_risk_group,
)
from quantara_engine.research.v3.metrics import profit_factor
from quantara_engine.research.v3_1.broker_replay import _coerce_replay_ts, monte_carlo_equity


@dataclass
class _OpenLeg:
    leg_id: str
    symbol: str
    opened_at: Any
    closes_at: Any
    planned_risk: float
    pnl: float


@dataclass
class PortfolioSimState:
    equity: Decimal
    starting: Decimal
    peak: Decimal
    hwm: Decimal
    daily_start_equity: Decimal
    daily_date: str
    daily_pnl: Decimal
    replay: ReplayState = field(default_factory=ReplayState)
    open_legs: dict[str, _OpenLeg] = field(default_factory=dict)
    open_by_symbol: dict[str, int] = field(default_factory=dict)


def _settings_025() -> LiveSimRiskSettings:
    return LiveSimRiskSettings(
        risk_per_trade_pct=Decimal("0.25"),
        max_total_open_sl_risk_pct=DEFAULT_MAX_TOTAL_OPEN_SL_RISK_PCT,
        max_symbol_sl_risk_pct=DEFAULT_MAX_SYMBOL_SL_RISK_PCT,
        max_group_sl_risk_pct=DEFAULT_MAX_GROUP_SL_RISK_PCT,
        daily_loss_gate_pct=DEFAULT_DAILY_LOSS_GATE_PCT,
        max_drawdown_gate_pct=DEFAULT_MAX_DRAWDOWN_GATE_PCT,
        concentration_mode="ENFORCE",
        high_water_mark=Decimal("10000"),
        daily_start_equity=Decimal("10000"),
        daily_start_date="",
    )


def simulate_portfolio_v2_gates(
    legs: list[dict[str, Any]],
    *,
    starting: float = 10_000.0,
) -> dict[str, Any]:
    """
    legs: [{key, symbol, trade_rows: [(opened, closed, sym, pnl, risk), ...]}]
    Event-driven chronological sim with V2 open-SL gates at entry; broker PnL at exit.
    """
    settings = _settings_025()
    st = PortfolioSimState(
        equity=Decimal(str(starting)),
        starting=Decimal(str(starting)),
        peak=Decimal(str(starting)),
        hwm=Decimal(str(starting)),
        daily_start_equity=Decimal(str(starting)),
        daily_date="",
        daily_pnl=Decimal("0"),
    )
    events: list[tuple] = []
    for leg in legs:
        base = leg["key"]
        for opened_at, closed_at, symbol, pnl, risk in leg.get("trade_rows") or []:
            o = _coerce_replay_ts(opened_at)
            c = _coerce_replay_ts(closed_at)
            inst = f"{base}|{o.isoformat()}"
            events.append(("open", o, inst, str(symbol).upper(), float(pnl), float(risk)))
            events.append(("close", c, inst, str(symbol).upper(), float(pnl), float(risk)))
    events.sort(key=lambda x: x[1])

    accepted_pnls: list[float] = []
    rs: list[float] = []
    wins = losses = skipped = 0
    exposure_samples: list[float] = []
    cluster_risk_samples: list[float] = []
    amd_risk_samples: list[float] = []
    concurrent_samples: list[int] = []
    peak_concurrent = 0
    peak_concurrent_risk = 0.0
    streak = max_streak = 0
    daily: dict[str, float] = {}

    for kind, ts, lid, symbol, pnl, risk in events:
        day = ts.date().isoformat()
        if st.daily_date != day:
            st.daily_date = day
            st.daily_start_equity = st.equity
            st.daily_pnl = Decimal("0")

        if kind == "close":
            leg = st.open_legs.pop(lid, None)
            if leg is None:
                continue
            st.replay.remove(symbol, Decimal(str(leg.planned_risk)))
            st.open_by_symbol[symbol] = max(0, st.open_by_symbol.get(symbol, 0) - 1)
            eq_f = float(st.equity)
            st.equity += Decimal(str(pnl))
            accepted_pnls.append(pnl)
            if leg.planned_risk > 0:
                rs.append(pnl / leg.planned_risk)
            if pnl >= 0:
                wins += 1
                streak = 0
            else:
                losses += 1
                streak += 1
                max_streak = max(max_streak, streak)
            st.daily_pnl += Decimal(str(pnl))
            st.peak = max(st.peak, st.equity)
            if st.equity > st.hwm:
                st.hwm = st.equity
            daily[day] = daily.get(day, 0.0) + pnl
            continue

        # open attempt
        if lid in st.open_legs:
            continue
        eq = st.equity
        target_risk = eq * Decimal("0.0025")
        proposed = min(Decimal(str(risk)), target_risk)
        if proposed <= 0:
            skipped += 1
            continue
        snap = st.replay.snapshot()
        gate_settings = LiveSimRiskSettings(
            risk_per_trade_pct=settings.risk_per_trade_pct,
            max_total_open_sl_risk_pct=settings.max_total_open_sl_risk_pct,
            max_symbol_sl_risk_pct=settings.max_symbol_sl_risk_pct,
            max_group_sl_risk_pct=settings.max_group_sl_risk_pct,
            daily_loss_gate_pct=settings.daily_loss_gate_pct,
            max_drawdown_gate_pct=settings.max_drawdown_gate_pct,
            concentration_mode=settings.concentration_mode,
            high_water_mark=st.hwm,
            daily_start_equity=st.daily_start_equity,
            daily_start_date=st.daily_date,
        )
        gate = evaluate_entry_gates(
            settings=gate_settings,
            equity=eq,
            realized_pnl_today=st.daily_pnl,
            open_risk=snap,
            proposed_risk_usd=proposed,
            symbol=symbol,
        )
        if not gate.allowed:
            skipped += 1
            continue
        # One concurrent position per symbol across portfolio (cluster discipline)
        if st.open_by_symbol.get(symbol, 0) >= 1:
            skipped += 1
            continue
        st.replay.add(symbol, proposed)
        st.open_legs[lid] = _OpenLeg(lid, symbol, ts, None, float(proposed), pnl)
        st.open_by_symbol[symbol] = st.open_by_symbol.get(symbol, 0) + 1
        n_open = len(st.open_legs)
        peak_concurrent = max(peak_concurrent, n_open)
        open_risk = float(st.replay.total)
        peak_concurrent_risk = max(peak_concurrent_risk, open_risk)
        concurrent_samples.append(n_open)
        exposure_samples.append(open_risk)
        grp = symbol_risk_group(symbol)
        if grp == "US_HIGH_BETA":
            cluster_risk_samples.append(float(st.replay.open_by_group.get(grp, Decimal("0"))))
        if symbol == "AMD":
            amd_risk_samples.append(float(st.replay.open_by_symbol.get("AMD", Decimal("0"))))

    max_dd = float(st.peak - min(st.equity, st.peak))
    # recompute max dd along curve
    eq = float(st.starting)
    peak = eq
    max_dd = 0.0
    for p in accepted_pnls:
        eq += p
        peak = max(peak, eq)
        max_dd = max(max_dd, peak - eq)

    pf = profit_factor(accepted_pnls)
    return {
        "starting_equity": float(st.starting),
        "ending_equity": round(float(st.equity), 2),
        "pnl": round(float(st.equity - st.starting), 2),
        "return_pct": round((float(st.equity) / float(st.starting) - 1) * 100, 4),
        "trades": len(accepted_pnls),
        "wins": wins,
        "losses": losses,
        "win_rate_pct": round(wins / len(accepted_pnls) * 100, 2) if accepted_pnls else None,
        "skipped_entries": skipped,
        "pf": pf,
        "expectancy_r": round(sum(rs) / len(rs), 4) if rs else None,
        "max_dd_usd": round(max_dd, 2),
        "max_dd_pct": round(max_dd / float(st.starting) * 100, 4),
        "return_over_dd": round((float(st.equity) / float(st.starting) - 1) * 100 / max(max_dd / float(st.starting) * 100, 0.01), 4),
        "max_losing_streak": max_streak,
        "average_exposure": round(sum(exposure_samples) / len(exposure_samples), 2) if exposure_samples else 0,
        "peak_exposure": round(max(exposure_samples), 2) if exposure_samples else 0,
        "capital_utilization": round(sum(exposure_samples) / (float(st.starting) * len(exposure_samples)) * 100, 4)
        if exposure_samples
        else 0,
        "average_concurrent_positions": round(sum(concurrent_samples) / len(concurrent_samples), 2)
        if concurrent_samples
        else 0,
        "max_concurrent_positions": peak_concurrent,
        "average_risk_per_trade": round(sum(exposure_samples) / len(exposure_samples), 2) if exposure_samples else 0,
        "peak_concurrent_risk_usd": round(peak_concurrent_risk, 2),
        "average_cluster_risk_usd": round(sum(cluster_risk_samples) / len(cluster_risk_samples), 2)
        if cluster_risk_samples
        else 0,
        "peak_cluster_risk_usd": round(max(cluster_risk_samples), 2) if cluster_risk_samples else 0,
        "average_amd_open_risk_usd": round(sum(amd_risk_samples) / len(amd_risk_samples), 2)
        if amd_risk_samples
        else 0,
        "peak_amd_open_risk_usd": round(max(amd_risk_samples), 2) if amd_risk_samples else 0,
        "best_day": round(max(daily.values()), 2) if daily else 0,
        "worst_day": round(min(daily.values()), 2) if daily else 0,
        "chronological_pnls": accepted_pnls,
    }


def monte_carlo_from_sim(sim: dict, *, iterations: int = 5000) -> dict:
    import random

    pnls = sim.get("chronological_pnls") or []
    if len(pnls) < 5:
        return {}
    random.seed(42)
    starting = float(sim.get("starting_equity") or 10_000)
    dds: list[float] = []
    returns_pct: list[float] = []
    streaks: list[int] = []
    for _ in range(iterations):
        sample = [pnls[random.randint(0, len(pnls) - 1)] for _ in range(len(pnls))]
        eq = starting
        peak = eq
        dd = 0.0
        streak = 0
        max_streak = 0
        for p in sample:
            eq += p
            peak = max(peak, eq)
            dd = max(dd, peak - eq)
            if p < 0:
                streak += 1
                max_streak = max(max_streak, streak)
            else:
                streak = 0
        dds.append(dd / starting * 100)
        returns_pct.append((eq / starting - 1) * 100)
        streaks.append(max_streak)
    dds.sort()
    returns_pct.sort()
    streaks.sort()
    n = iterations
    mc = monte_carlo_equity(pnls, starting=starting, iterations=iterations)
    return {
        **mc,
        "probability_dd_gt_20pct": sum(1 for d in dds if d > 20) / n,
    }
