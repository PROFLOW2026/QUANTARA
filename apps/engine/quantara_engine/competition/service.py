"""Build competition comparison payloads for the API."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from quantara_engine.competition.constants import (
    COMPETITION_NAME_HE,
    COMPETITION_SUBTITLE_HE,
    PORTFOLIO_DEF_BY_ID,
    RISK_SLUG_HE,
    TIMEFRAME_GROUP_TITLE_HE,
    TIMEFRAME_HE,
    TIMEFRAME_ORDER,
)
from quantara_engine.competition.leverage import compute_sizing_metrics
from quantara_engine.competition.multi_strategy_constants import (
    EXPERIMENT_META,
    MEAN_REVERSION_EXPERIMENT_ID,
    MOMENTUM_CONTINUATION_EXPERIMENT_ID,
    PORTFOLIO_DEF_BY_ID as MULTI_PORTFOLIO_DEF_BY_ID,
    SHADOW_REFERENCE_TOTAL,
    VOLATILITY_SQUEEZE_EXPERIMENT_ID,
)
from quantara_engine.competition.orb_constants import (
    ORB_COMPETITION_EXPERIMENT_ID,
    ORB_COMPETITION_NAME_HE,
    ORB_COMPETITION_SUBTITLE_HE,
    ORB_PORTFOLIO_DEF_BY_ID,
    ORB_STRATEGY_SLUG,
)
from quantara_engine.competition.robot_registry import ROBOT_LABELS
from quantara_engine.persistence.store import TradingStore
from quantara_engine.strategies.registry import get

ROBOT_A_LABEL = "Robot A"
ROBOT_B_LABEL = "Robot B"
ROBOT_C_LABEL = "Robot C"
ROBOT_D_LABEL = "Robot D"
ROBOT_E_LABEL = "Robot E"


def _return_pct(initial: Decimal, equity: Decimal) -> float:
    if initial <= 0:
        return 0.0
    return float((equity - initial) / initial * 100)


def _drawdown_pct(equity: Decimal, peak: Decimal) -> float:
    if peak <= 0:
        return 0.0
    return float((peak - equity) / peak * 100)


def _portfolio_display_name(portfolio_id: str, fallback: str) -> str:
    portfolio_def = PORTFOLIO_DEF_BY_ID.get(portfolio_id)
    if portfolio_def:
        return portfolio_def.name_he
    orb_def = ORB_PORTFOLIO_DEF_BY_ID.get(portfolio_id)
    if orb_def:
        return orb_def.name_he
    multi_def = MULTI_PORTFOLIO_DEF_BY_ID.get(portfolio_id)
    if multi_def:
        return multi_def.name_he
    return fallback


def _portfolio_summary(
    entry: dict[str, Any],
    batch_stats: dict[str, Any],
    *,
    robot_label: str,
    strategy_slug: str,
    strategy_name: str,
) -> dict[str, Any]:
    portfolio = entry["portfolio"]
    risk = entry["risk_profile"]
    instance = entry["instance"]
    timeframe = instance.timeframe
    stats = batch_stats.get(portfolio.id, {})
    realized = stats.get("realized_pnl", Decimal("0"))
    trades_count = stats.get("closed_trades_count", 0)
    win_rate = stats.get("win_rate")
    open_positions = stats.get("open_positions") or []
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

    risk_name_he = RISK_SLUG_HE.get(risk.slug, portfolio.name)
    display_name = _portfolio_display_name(portfolio.id, portfolio.name)

    return {
        "id": portfolio.id,
        "name": display_name,
        "robot_label": robot_label,
        "strategy_slug": strategy_slug,
        "strategy_name": strategy_name,
        "timeframe": timeframe,
        "timeframe_he": TIMEFRAME_HE.get(timeframe, timeframe),
        "risk_slug": risk.slug,
        "risk_name_he": risk_name_he,
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
        "strategy_instance_id": instance.id,
        "sort_order": entry["sort_order"],
    }


def _leaderboard_rows(portfolios: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = sorted(
        [
            {
                "rank": 0,
                "portfolio_id": p["id"],
                "name": p["name"],
                "robot_label": p.get("robot_label"),
                "strategy_slug": p.get("strategy_slug"),
                "timeframe": p["timeframe"],
                "timeframe_he": p["timeframe_he"],
                "return_pct": p["return_pct"],
                "max_drawdown_pct": p["max_drawdown_pct"],
                "realized_pnl": p["realized_pnl"],
                "trades_count": p["trades_count"],
                "win_rate": p["win_rate"],
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
    for i, row in enumerate(rows, start=1):
        row["rank"] = i
    return rows


def _timeframe_comparison(portfolios: list[dict[str, Any]]) -> list[dict[str, Any]]:
    summary: list[dict[str, Any]] = []
    for timeframe in TIMEFRAME_ORDER:
        group = [p for p in portfolios if p["timeframe"] == timeframe]
        if not group:
            continue
        returns = [p["return_pct"] for p in group]
        drawdowns = [p["max_drawdown_pct"] for p in group]
        trades = sum(p["trades_count"] for p in group)
        realized = sum(p["realized_pnl"] for p in group)
        unrealized = sum(p["unrealized_pnl"] for p in group)
        open_positions = sum(p.get("open_positions_count", 0) for p in group)
        summary.append(
            {
                "timeframe": timeframe,
                "timeframe_he": TIMEFRAME_HE.get(timeframe, timeframe),
                "title_he": TIMEFRAME_GROUP_TITLE_HE.get(timeframe, timeframe),
                "portfolio_count": len(group),
                "average_return_pct": round(sum(returns) / len(group), 2),
                "best_return_pct": max(returns),
                "total_trades": trades,
                "closed_trades": trades,
                "realized_pnl": round(realized, 2),
                "unrealized_pnl": round(unrealized, 2),
                "total_pnl": round(realized + unrealized, 2),
                "open_positions": open_positions,
                "average_drawdown_pct": round(sum(drawdowns) / len(group), 2),
                "max_drawdown_pct": max(drawdowns) if drawdowns else 0.0,
                "combined_equity": round(sum(p["equity"] for p in group), 2),
            }
        )
    return summary


def _robot_group_summary(
    *,
    robot_label: str,
    strategy_name: str,
    strategy_slug: str,
    experiment_id: str,
    portfolios: list[dict[str, Any]],
    initial_capital: float,
) -> dict[str, Any]:
    current_equity = sum(p["equity"] for p in portfolios)
    return {
        "robot_label": robot_label,
        "strategy_name": strategy_name,
        "strategy_slug": strategy_slug,
        "experiment_id": experiment_id,
        "portfolio_count": len(portfolios),
        "initial_capital": initial_capital,
        "current_equity": round(current_equity, 2),
        "combined_pnl": round(current_equity - initial_capital, 2),
    }


def _build_activity(store: TradingStore, entries: list[dict[str, Any]], limit: int = 24) -> list[dict[str, Any]]:
    decisions = store.list_competition_decisions(limit=limit * 4)
    activity: list[dict[str, Any]] = []

    instance_meta: dict[str, dict[str, str]] = {}
    for entry in entries:
        p = entry["portfolio"]
        tf = entry["instance"].timeframe
        instance_meta[entry["instance"].id] = {
            "name": _portfolio_display_name(p.id, p.name),
            "timeframe_he": TIMEFRAME_HE.get(tf, tf),
            "risk_he": RISK_SLUG_HE.get(entry["risk_profile"].slug, p.name),
        }

    meaningful = {
        "buy_signal",
        "sell_signal",
        "risk_approved",
        "risk_denied",
        "sl_triggered",
        "tp_triggered",
        "position_open",
    }

    for decision in decisions:
        dtype = decision.decision_type.value
        if dtype not in meaningful:
            continue

        meta = instance_meta.get(decision.strategy_instance_id, {})
        label = meta.get("name") or meta.get("risk_he") or "תיק"
        tf_he = meta.get("timeframe_he", "")
        prefix = f"{tf_he} / {label}" if tf_he else label

        if dtype == "buy_signal":
            message = f"{prefix} — איתות קנייה"
            kind = "buy_signal"
        elif dtype == "sell_signal":
            message = f"{prefix} — איתות מכירה"
            kind = "sell_signal"
        elif dtype == "risk_approved":
            message = f"{prefix} — {decision.message}"
            kind = "risk_approved"
        elif dtype == "risk_denied":
            message = f"{prefix} — סיכון נדחה"
            kind = "risk_denied"
        elif dtype == "sl_triggered":
            message = f"{prefix} — סטופ ({decision.message})"
            kind = "sl_triggered"
        elif dtype == "tp_triggered":
            message = f"{prefix} — יעד ({decision.message})"
            kind = "tp_triggered"
        elif dtype == "position_open":
            message = f"{prefix} — פוזיציה נפתחה"
            kind = "position_open"
        else:
            continue

        activity.append(
            {
                "timestamp": decision.candle_timestamp.isoformat(),
                "kind": kind,
                "portfolio_name": label,
                "timeframe": meta.get("timeframe_he"),
                "message": message,
            }
        )
        if len(activity) >= limit:
            break

    activity.sort(key=lambda a: a["timestamp"], reverse=True)
    return activity[:limit]


def _collect_closed_trades(store: TradingStore, robot_a_exp_id: str, robot_b_enabled: bool) -> list[dict[str, Any]]:
    experiment_ids = [robot_a_exp_id]
    if robot_b_enabled:
        experiment_ids.append(ORB_COMPETITION_EXPERIMENT_ID)
    for exp_id in (
        MEAN_REVERSION_EXPERIMENT_ID,
        VOLATILITY_SQUEEZE_EXPERIMENT_ID,
        MOMENTUM_CONTINUATION_EXPERIMENT_ID,
    ):
        if store.is_multi_strategy_competition_enabled():
            experiment_ids.append(exp_id)
    closed_trades: list[dict[str, Any]] = []
    for exp_id in experiment_ids:
        closed_trades.extend(store.list_competition_trades(exp_id, limit=30))
    return sorted(closed_trades, key=lambda row: row.get("closed_at") or "", reverse=True)[:30]


def build_competition_equity_curves(
    store: TradingStore,
    *,
    limit_per_portfolio: int = 100,
) -> dict[str, list[dict[str, Any]]]:
    """Lazy-loaded equity curves — not included in summary response."""
    from quantara_engine.persistence.batch_summary import batch_recent_snapshots

    _, _, all_entries = store.list_all_competition_entries()
    portfolio_ids = [e["portfolio"].id for e in all_entries]
    curves = batch_recent_snapshots(store, portfolio_ids, limit_per_portfolio=limit_per_portfolio)
    for entry in all_entries:
        pid = entry["portfolio"].id
        if not curves.get(pid):
            curves[pid] = [
                {
                    "date": datetime.now(timezone.utc).isoformat(),
                    "equity": float(entry["portfolio"].equity),
                }
            ]
    return curves


def _multi_strategy_portfolios(
    entries: list[dict[str, Any]],
    batch_stats: dict[str, Any],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for entry in entries:
        slug = entry["instance"].strategy_slug
        version = entry["instance"].strategy_version
        try:
            strategy_name = get(slug, version).name()
        except KeyError:
            strategy_name = slug
        rows.append(
            _portfolio_summary(
                entry,
                batch_stats.get(entry["portfolio"].id, {}),
                robot_label=ROBOT_LABELS.get(slug, slug),
                strategy_slug=slug,
                strategy_name=strategy_name,
            )
        )
    return rows


def build_competition_response(store: TradingStore) -> dict[str, Any]:
    robot_a_entries, robot_b_entries, all_entries = store.list_all_competition_entries()
    cde_entries = store.list_multi_strategy_competition_entries()
    if not robot_a_entries and not robot_b_entries and not cde_entries:
        return {"active": False}

    started_at = store.get_competition_started_at()
    multi_started_raw = (store.get_settings_dict().get("multi_strategy_competition_started_at"))
    robot_a_exp_id = store.get_competition_experiment_id()
    orb_enabled = bool(robot_b_entries)
    portfolio_ids = [e["portfolio"].id for e in all_entries]
    batch_stats = store.batch_portfolio_dashboard_stats(portfolio_ids)

    robot_a_portfolios = [
        _portfolio_summary(
            entry,
            batch_stats.get(entry["portfolio"].id, {}),
            robot_label=ROBOT_A_LABEL,
            strategy_slug="gold-trend-pullback",
            strategy_name="Trend Pullback",
        )
        for entry in robot_a_entries
    ]
    robot_b_portfolios = [
        _portfolio_summary(
            entry,
            batch_stats.get(entry["portfolio"].id, {}),
            robot_label=ROBOT_B_LABEL,
            strategy_slug=ORB_STRATEGY_SLUG,
            strategy_name="Opening Range Breakout",
        )
        for entry in robot_b_entries
    ]
    cde_portfolios = _multi_strategy_portfolios(cde_entries, batch_stats)
    portfolios = robot_a_portfolios + robot_b_portfolios + cde_portfolios

    robot_a_initial = sum(float(e["portfolio"].initial_capital) for e in robot_a_entries)
    robot_b_initial = sum(float(e["portfolio"].initial_capital) for e in robot_b_entries)
    cde_initial = sum(float(e["portfolio"].initial_capital) for e in cde_entries)
    total_initial = robot_a_initial + robot_b_initial + cde_initial
    shadow_reference_total = float(SHADOW_REFERENCE_TOTAL)
    combined_equity = sum(p["equity"] for p in portfolios)
    combined_pnl = combined_equity - total_initial

    leaderboard = _leaderboard_rows(portfolios)
    leaderboards_by_timeframe = {
        tf: _leaderboard_rows([p for p in portfolios if p["timeframe"] == tf])
        for tf in TIMEFRAME_ORDER
    }

    timeframe_groups = [
        {
            "timeframe": tf,
            "timeframe_he": TIMEFRAME_HE.get(tf, tf),
            "title_he": TIMEFRAME_GROUP_TITLE_HE.get(tf, tf),
            "portfolios": [p for p in portfolios if p["timeframe"] == tf],
        }
        for tf in TIMEFRAME_ORDER
    ]

    leader = leaderboard[0] if leaderboard else None
    open_positions_total = sum(p["open_positions_count"] for p in portfolios)

    timeframe_comparison = _timeframe_comparison(portfolios)
    leading_timeframe = None
    if timeframe_comparison:
        leading_timeframe = max(
            timeframe_comparison, key=lambda row: row["average_return_pct"]
        )

    identity_by_portfolio = {
        entry["portfolio"].id: {
            "robot_label": ROBOT_LABELS.get(entry["instance"].strategy_slug, entry["instance"].strategy_slug),
            "strategy_slug": entry["instance"].strategy_slug,
        }
        for entry in all_entries
    }
    open_positions_detail = []
    for entry in all_entries:
        pid = entry["portfolio"].id
        identity = identity_by_portfolio.get(pid, {})
        for pos in batch_stats.get(pid, {}).get("open_positions") or []:
            open_positions_detail.append(
                {
                    "portfolio_id": pid,
                    "portfolio_name": _portfolio_display_name(pid, entry["portfolio"].name),
                    "robot_label": identity.get("robot_label"),
                    "strategy_slug": identity.get("strategy_slug"),
                    "timeframe_he": TIMEFRAME_HE.get(entry["instance"].timeframe, entry["instance"].timeframe),
                    "direction": pos.direction.value,
                    "entry_price": float(pos.entry_price),
                    "current_price": float(pos.current_price),
                    "stop_loss": float(pos.stop_loss),
                    "take_profit": float(pos.take_profit) if pos.take_profit else None,
                    "unrealized_pnl": float(pos.unrealized_pnl),
                    "quantity": float(pos.quantity),
                }
            )

    closed_trades = _collect_closed_trades(store, robot_a_exp_id, orb_enabled)
    robot_groups = [
        _robot_group_summary(
            robot_label=ROBOT_A_LABEL,
            strategy_name="Trend Pullback",
            strategy_slug="gold-trend-pullback",
            experiment_id=robot_a_exp_id,
            portfolios=robot_a_portfolios,
            initial_capital=robot_a_initial,
        )
    ]
    if orb_enabled:
        robot_groups.append(
            _robot_group_summary(
                robot_label=ROBOT_B_LABEL,
                strategy_name="Opening Range Breakout",
                strategy_slug=ORB_STRATEGY_SLUG,
                experiment_id=ORB_COMPETITION_EXPERIMENT_ID,
                portfolios=robot_b_portfolios,
                initial_capital=robot_b_initial,
            )
        )
    for slug, exp_id in (
        ("mean-reversion", MEAN_REVERSION_EXPERIMENT_ID),
        ("volatility-squeeze", VOLATILITY_SQUEEZE_EXPERIMENT_ID),
        ("momentum-continuation", MOMENTUM_CONTINUATION_EXPERIMENT_ID),
    ):
        group_portfolios = [p for p in cde_portfolios if p["strategy_slug"] == slug]
        if not group_portfolios:
            continue
        try:
            strategy_name = get(slug, "1.0.0").name()
        except KeyError:
            strategy_name = slug
        robot_groups.append(
            _robot_group_summary(
                robot_label=ROBOT_LABELS.get(slug, slug),
                strategy_name=strategy_name,
                strategy_slug=slug,
                experiment_id=exp_id,
                portfolios=group_portfolios,
                initial_capital=sum(p["initial_capital"] for p in group_portfolios),
            )
        )

    return {
        "active": True,
        "experiment": {
            "id": robot_a_exp_id,
            "name": COMPETITION_NAME_HE,
            "subtitle": COMPETITION_SUBTITLE_HE,
            "started_at": started_at.isoformat() if started_at else None,
            "status": "running",
            "strategy_name": "Gold Trend Pullback",
            "strategy_version": "1.0.0",
            "instrument": "XAU/USD",
            "timeframe": "multi",
            "total_initial_capital": total_initial,
            "shadow_reference_capital": shadow_reference_total,
            "physical_broker_capital": 320000.0,
            "portfolio_initial_capital": float(robot_a_entries[0]["portfolio"].initial_capital),
            "portfolio_count": len(portfolios),
            "robot_a_portfolio_count": len(robot_a_portfolios),
            "robot_b_portfolio_count": len(robot_b_portfolios),
            "robot_c_portfolio_count": len([p for p in cde_portfolios if p["strategy_slug"] == "mean-reversion"]),
            "robot_d_portfolio_count": len([p for p in cde_portfolios if p["strategy_slug"] == "volatility-squeeze"]),
            "robot_e_portfolio_count": len([p for p in cde_portfolios if p["strategy_slug"] == "momentum-continuation"]),
            "robot_a_initial_capital": robot_a_initial,
            "robot_b_initial_capital": robot_b_initial,
            "robot_cde_initial_capital": cde_initial,
            "multi_strategy_started_at": multi_started_raw,
        },
        "robot_groups": robot_groups,
        "combined": {
            "initial_equity": total_initial,
            "current_equity": round(combined_equity, 2),
            "combined_pnl": round(combined_pnl, 2),
            "open_positions_total": open_positions_total,
        },
        "leader": {
            "portfolio_id": leader["portfolio_id"],
            "name": leader["name"],
            "return_pct": leader["return_pct"],
            "timeframe": leader["timeframe"],
            "timeframe_he": leader["timeframe_he"],
        }
        if leader
        else None,
        "leading_timeframe": leading_timeframe,
        "portfolios": portfolios,
        "timeframe_groups": timeframe_groups,
        "leaderboard": leaderboard,
        "leaderboards_by_timeframe": leaderboards_by_timeframe,
        "timeframe_comparison": timeframe_comparison,
        "equity_curves": {},
        "equity_curves_available": True,
        "activity": _build_activity(store, all_entries),
        "open_positions": open_positions_detail,
        "closed_trades": closed_trades,
        "today_summary": store.get_competition_today_stats(),
    }
