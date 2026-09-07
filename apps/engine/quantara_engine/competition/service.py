"""Build competition comparison payloads for the API."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from quantara_engine.analytics.service import AnalyticsService
from quantara_engine.competition.constants import COMPETITION_TOTAL_INITIAL, RISK_SLUG_HE
from quantara_engine.competition.leverage import compute_sizing_metrics
from quantara_engine.persistence.store import TradingStore


def _return_pct(initial: Decimal, equity: Decimal) -> float:
    if initial <= 0:
        return 0.0
    return float((equity - initial) / initial * 100)


def _drawdown_pct(equity: Decimal, peak: Decimal) -> float:
    if peak <= 0:
        return 0.0
    return float((peak - equity) / peak * 100)


def _portfolio_summary(store: TradingStore, entry: dict[str, Any]) -> dict[str, Any]:
    portfolio = entry["portfolio"]
    risk = entry["risk_profile"]
    realized = store.sum_realized_pnl(portfolio.id)
    trades_count = store.count_trades_for_portfolio(portfolio.id)
    win_rate = store.portfolio_win_rate(portfolio.id)
    open_positions = store.list_positions(portfolio.id, open_only=True)
    exposure_pct = (
        float(portfolio.exposure_notional / portfolio.equity * 100)
        if portfolio.equity > 0
        else 0.0
    )
    total_pnl = portfolio.equity - portfolio.initial_capital
    target_risk_pct = float(risk.risk_per_trade_pct)
    actual_risk_pct: float | None = None
    virtual_leverage: float | None = None
    notional_exposure = float(portfolio.exposure_notional)

    if open_positions:
        pos = open_positions[0]
        mark = pos.current_price or pos.entry_price
        metrics = compute_sizing_metrics(
            pos.quantity,
            mark,
            portfolio.equity,
            pos.target_risk_amount,
            pos.actual_risk_amount,
        )
        actual_risk_pct = float(metrics["actual_risk_pct"])
        virtual_leverage = float(metrics["virtual_leverage"])
        notional_exposure = float(metrics["notional"])

    return {
        "id": portfolio.id,
        "name": portfolio.name,
        "risk_slug": risk.slug,
        "risk_name_he": RISK_SLUG_HE.get(risk.slug, portfolio.name),
        "risk_per_trade_pct": float(risk.risk_per_trade_pct),
        "initial_capital": float(portfolio.initial_capital),
        "equity": float(portfolio.equity),
        "balance": float(portfolio.balance),
        "realized_pnl": float(realized),
        "unrealized_pnl": float(portfolio.unrealized_pnl),
        "total_pnl": float(total_pnl),
        "return_pct": round(_return_pct(portfolio.initial_capital, portfolio.equity), 2),
        "trades_count": trades_count,
        "win_rate": round(win_rate, 1) if win_rate is not None else None,
        "max_drawdown_pct": round(
            _drawdown_pct(portfolio.equity, portfolio.peak_equity), 2
        ),
        "exposure_pct": round(exposure_pct, 2),
        "notional_exposure": round(notional_exposure, 2),
        "target_risk_pct": target_risk_pct,
        "actual_risk_pct": actual_risk_pct,
        "virtual_leverage": virtual_leverage,
        "open_position": len(open_positions) > 0,
        "open_positions_count": len(open_positions),
        "status": portfolio.status.value,
        "strategy_instance_id": entry["instance"].id,
        "sort_order": entry["sort_order"],
    }


def _build_activity(store: TradingStore, limit: int = 20) -> list[dict[str, Any]]:
    decisions = store.list_competition_decisions(limit=limit * 3)
    activity: list[dict[str, Any]] = []
    seen_signal_candles: set[str] = set()

    portfolio_names = {
        e["portfolio"].id: e["portfolio"].name for e in store.list_competition_entries()
    }

    for decision in decisions:
        ts = decision.candle_timestamp.isoformat()
        dtype = decision.decision_type.value

        if dtype in ("buy_signal", "sell_signal"):
            key = f"{dtype}:{ts}"
            if key not in seen_signal_candles:
                seen_signal_candles.add(key)
                direction = "קנייה" if dtype == "buy_signal" else "מכירה"
                activity.append(
                    {
                        "timestamp": ts,
                        "kind": "signal_all",
                        "message": f"כל 5 התיקים קיבלו איתות {direction}",
                    }
                )
            continue

        instance_portfolio = next(
            (
                e["portfolio"]
                for e in store.list_competition_entries()
                if e["instance"].id == decision.strategy_instance_id
            ),
            None,
        )
        name = instance_portfolio.name if instance_portfolio else "תיק"

        if dtype == "risk_approved":
            activity.append(
                {
                    "timestamp": ts,
                    "kind": "risk_approved",
                    "portfolio_name": name,
                    "message": decision.message,
                }
            )
        elif dtype in ("sl_triggered", "tp_triggered"):
            label = "סטופ" if dtype == "sl_triggered" else "יעד"
            activity.append(
                {
                    "timestamp": ts,
                    "kind": dtype,
                    "portfolio_name": name,
                    "message": f"תיק {name} — {label} ({decision.message})",
                }
            )
        elif dtype == "risk_denied":
            activity.append(
                {
                    "timestamp": ts,
                    "kind": "risk_denied",
                    "portfolio_name": name,
                    "message": f"{name} — סיכון נדחה",
                }
            )

        if len(activity) >= limit:
            break

    activity.sort(key=lambda a: a["timestamp"], reverse=True)
    return activity[:limit]


def build_competition_response(store: TradingStore) -> dict[str, Any]:
    entries = store.list_competition_entries()
    if not entries:
        return {"active": False}

    started_at = store.get_competition_started_at()
    exp_id = store.get_competition_experiment_id()
    svc = AnalyticsService()

    portfolios = [_portfolio_summary(store, entry) for entry in entries]
    combined_equity = sum(p["equity"] for p in portfolios)
    combined_pnl = combined_equity - float(COMPETITION_TOTAL_INITIAL)

    leaderboard = sorted(
        [
            {
                "rank": 0,
                "portfolio_id": p["id"],
                "name": p["name"],
                "return_pct": p["return_pct"],
                "max_drawdown_pct": p["max_drawdown_pct"],
                "realized_pnl": p["realized_pnl"],
                "trades_count": p["trades_count"],
                "return_vs_drawdown": (
                    round(p["return_pct"] / p["max_drawdown_pct"], 2)
                    if p["max_drawdown_pct"] > 0
                    else None
                ),
            }
            for p in portfolios
        ],
        key=lambda x: x["return_pct"],
        reverse=True,
    )
    for i, row in enumerate(leaderboard, start=1):
        row["rank"] = i

    equity_curves: dict[str, list[dict[str, Any]]] = {}
    for entry in entries:
        pid = entry["portfolio"].id
        state = store.load_portfolio_state(pid)
        curve = svc.equity_curve(state.snapshots) or [
            {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "equity": float(entry["portfolio"].equity),
            }
        ]
        equity_curves[pid] = [
            {
                "date": c.get("timestamp", c.get("date", "")),
                "equity": c["equity"],
            }
            for c in curve
        ]

    leader = leaderboard[0] if leaderboard else None
    open_positions_total = sum(p["open_positions_count"] for p in portfolios)

    return {
        "active": True,
        "experiment": {
            "id": exp_id,
            "name": "השוואת 5 תיקים אוטומטיים",
            "subtitle": "אותה אסטרטגיה ואותם נתוני שוק — רמות סיכון שונות",
            "started_at": started_at.isoformat() if started_at else None,
            "status": "running",
            "strategy_name": "Gold Trend Pullback",
            "strategy_version": "1.0.0",
            "instrument": "XAU/USD",
            "timeframe": "1h",
            "total_initial_capital": float(COMPETITION_TOTAL_INITIAL),
            "portfolio_initial_capital": float(entries[0]["portfolio"].initial_capital),
            "portfolio_count": len(portfolios),
        },
        "combined": {
            "initial_equity": float(COMPETITION_TOTAL_INITIAL),
            "current_equity": round(combined_equity, 2),
            "combined_pnl": round(combined_pnl, 2),
            "open_positions_total": open_positions_total,
        },
        "leader": {
            "portfolio_id": leader["portfolio_id"],
            "name": leader["name"],
            "return_pct": leader["return_pct"],
        }
        if leader
        else None,
        "portfolios": portfolios,
        "leaderboard": leaderboard,
        "equity_curves": equity_curves,
        "activity": _build_activity(store),
    }
