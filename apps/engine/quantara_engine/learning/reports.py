"""Hebrew learning report payloads — observational analytics only."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import text

from quantara_engine.learning.activation import ensure_trading_week_baseline, get_active_baseline
from quantara_engine.learning.daily_loss_shadow import refresh_daily_loss_shadows
from quantara_engine.learning.funnel import build_decision_funnel
from quantara_engine.learning.shadow_outcomes import shadow_variant_metrics
from quantara_engine.market_regime.snapshots import latest_regimes_for_assets


def _day_bounds(d: date) -> tuple[datetime, datetime]:
    start = datetime(d.year, d.month, d.day, tzinfo=timezone.utc)
    return start, start + timedelta(days=1)


def build_daily_learning_report(store, *, report_date: date | None = None) -> dict[str, Any]:
    baseline = ensure_trading_week_baseline(store)
    baseline_id = baseline["id"] if baseline else None
    activated_at = baseline.get("activated_at") if baseline else None
    report_date = report_date or datetime.now(timezone.utc).date()
    day_start, day_end = _day_bounds(report_date)

    refresh_daily_loss_shadows(store, baseline_id=baseline_id, trade_date=report_date)

    # א. סיכום היום — Research competition aggregates
    summary = store.session.execute(
        text(
            """
            SELECT
              COUNT(*) FILTER (WHERE t.closed_at >= :start AND t.closed_at < :end)::int AS closed_trades,
              COALESCE(SUM(t.realized_pnl) FILTER (WHERE t.closed_at >= :start AND t.closed_at < :end), 0) AS realized_pnl,
              COUNT(*) FILTER (WHERE p.status = 'open')::int AS open_positions,
              COALESCE(SUM(p.quantity * p.current_price) FILTER (WHERE p.status = 'open'), 0) AS exposure
            FROM portfolios port
            JOIN strategy_instances si ON si.portfolio_id = port.id AND si.is_active
            LEFT JOIN trades t ON t.portfolio_id = port.id
            LEFT JOIN positions p ON p.portfolio_id = port.id
            WHERE si.experiment_id IS NOT NULL
            """
        ),
        {"start": day_start, "end": day_end},
    ).mappings().first()

    dd_row = store.session.execute(
        text(
            """
            SELECT MAX(
              CASE WHEN peak_equity > 0
                THEN (peak_equity - equity) / peak_equity * 100
                ELSE 0 END
            ) AS max_dd
            FROM portfolios port
            JOIN strategy_instances si ON si.portfolio_id = port.id AND si.is_active
            WHERE si.experiment_id IS NOT NULL
            """
        )
    ).scalar()

    since = activated_at if activated_at and activated_at > day_start else day_start
    funnel = build_decision_funnel(store, since=since, until=day_end)

    pva = store.session.execute(
        text(
            """
            SELECT
              COUNT(*)::int AS n,
              AVG(risk_diff_pct) AS avg_risk_diff_pct,
              MAX(risk_diff_pct) AS max_risk_diff_pct,
              COUNT(*) FILTER (WHERE risk_overrun)::int AS overrun_count,
              AVG(gap_from_signal) AS avg_gap,
              AVG(slippage) AS avg_slippage
            FROM learning_planned_vs_actual_risk
            WHERE created_at >= :start AND created_at < :end
            """
        ),
        {"start": day_start, "end": day_end},
    ).mappings().first()

    rsi_metrics = shadow_variant_metrics(store, baseline_id=baseline_id, since=since)

    daily_loss = store.session.execute(
        text(
            """
            SELECT portfolio_id::text, shadow_halt, shadow_halt_time,
                   actual_eod_pnl, shadow_stop_eod_pnl, difference,
                   start_equity, daily_loss_limit_pct
            FROM learning_daily_loss_shadow
            WHERE trade_date = CAST(:d AS date)
            ORDER BY shadow_halt DESC, ABS(COALESCE(difference, 0)) DESC
            LIMIT 50
            """
        ),
        {"d": report_date.isoformat()},
    ).mappings().all()

    assets = store.session.execute(
        text("SELECT symbol FROM instruments WHERE COALESCE(is_active, TRUE) = TRUE")
    ).scalars().all()
    regimes = latest_regimes_for_assets(store, list(assets), timeframe="15m")

    return {
        "report_date": report_date.isoformat(),
        "baseline": {
            "id": baseline_id,
            "week_label": baseline.get("week_label"),
            "activated_at": baseline.get("activated_at").isoformat()
            if hasattr(baseline.get("activated_at"), "isoformat")
            else baseline.get("activated_at"),
            "commit_sha": baseline.get("commit_sha"),
        },
        "summary": {
            "closed_trades": int(summary["closed_trades"] or 0) if summary else 0,
            "realized_pnl": float(summary["realized_pnl"] or 0) if summary else 0.0,
            "open_positions": int(summary["open_positions"] or 0) if summary else 0,
            "exposure": float(summary["exposure"] or 0) if summary else 0.0,
            "drawdown_pct": float(dd_row or 0),
        },
        "funnel": funnel,
        "planned_vs_actual": dict(pva) if pva else {},
        "rsi_shadow": {
            "active_40_60": rsi_metrics.get("active"),
            "directional": rsi_metrics.get("directional"),
            "no_rsi_filter": rsi_metrics.get("no_rsi"),
        },
        "daily_loss_shadow": [dict(r) for r in daily_loss],
        "market_regime": regimes,
        "observational_only": True,
    }


def build_week_learning_summary(store) -> dict[str, Any]:
    baseline = get_active_baseline(store) or ensure_trading_week_baseline(store)
    baseline_id = baseline["id"]
    activated_at = baseline.get("activated_at")
    rsi = shadow_variant_metrics(store, baseline_id=baseline_id, since=activated_at)
    funnel = build_decision_funnel(store, since=activated_at)

    pva = store.session.execute(
        text(
            """
            SELECT
              COUNT(*)::int AS n,
              AVG(risk_diff_pct) AS avg_risk_diff_pct,
              MAX(ABS(risk_diff_pct)) AS max_abs_risk_diff_pct,
              COUNT(*) FILTER (WHERE risk_overrun)::int AS overrun_count
            FROM learning_planned_vs_actual_risk
            WHERE baseline_id = CAST(:bid AS uuid)
               OR (:activated_at IS NOT NULL AND created_at >= :activated_at)
            """
        ),
        {"bid": baseline_id, "activated_at": activated_at},
    ).mappings().first()

    daily_loss = store.session.execute(
        text(
            """
            SELECT
              COUNT(*) FILTER (WHERE shadow_halt)::int AS halt_days_portfolios,
              COALESCE(SUM(actual_eod_pnl), 0) AS sum_actual,
              COALESCE(SUM(shadow_stop_eod_pnl), 0) AS sum_shadow,
              COALESCE(SUM(difference), 0) AS sum_difference
            FROM learning_daily_loss_shadow
            WHERE baseline_id = CAST(:bid AS uuid)
               OR trade_date >= CAST(:activated AS date)
            """
        ),
        {
            "bid": baseline_id,
            "activated": activated_at.date() if hasattr(activated_at, "date") else activated_at,
        },
    ).mappings().first()

    live_sim_rejects = store.session.execute(
        text(
            """
            SELECT rejection_reason, COUNT(*)::int AS n
            FROM live_sim_allocation_log
            WHERE rejection_reason IS NOT NULL
              AND (:activated_at IS NULL OR created_at >= :activated_at)
            GROUP BY rejection_reason
            ORDER BY n DESC
            """
        ),
        {"activated_at": activated_at},
    ).mappings().all()

    robot_perf = store.session.execute(
        text(
            """
            SELECT s.slug, COUNT(t.id)::int AS trades,
                   COALESCE(SUM(t.realized_pnl), 0) AS net_pnl
            FROM trades t
            JOIN strategy_instances si ON si.id = t.strategy_instance_id
            JOIN strategy_versions sv ON sv.id = si.strategy_version_id
            JOIN strategies s ON s.id = sv.strategy_id
            WHERE (:activated_at IS NULL OR t.closed_at >= :activated_at)
            GROUP BY s.slug
            ORDER BY net_pnl DESC
            """
        ),
        {"activated_at": activated_at},
    ).mappings().all()

    asset_perf = store.session.execute(
        text(
            """
            SELECT i.symbol, COUNT(t.id)::int AS trades,
                   COALESCE(SUM(t.realized_pnl), 0) AS net_pnl
            FROM trades t
            JOIN instruments i ON i.id = t.instrument_id
            WHERE (:activated_at IS NULL OR t.closed_at >= :activated_at)
            GROUP BY i.symbol
            ORDER BY net_pnl DESC
            """
        ),
        {"activated_at": activated_at},
    ).mappings().all()

    return {
        "baseline": {
            "id": baseline_id,
            "week_label": baseline.get("week_label"),
            "activated_at": baseline.get("activated_at").isoformat()
            if hasattr(baseline.get("activated_at"), "isoformat")
            else baseline.get("activated_at"),
            "commit_sha": baseline.get("commit_sha"),
            "snapshot": baseline.get("snapshot"),
        },
        "questions": {
            "rsi_40_60_vs_directional": {
                "active": rsi.get("active"),
                "directional": rsi.get("directional"),
            },
            "rsi_40_60_vs_no_rsi": {
                "active": rsi.get("active"),
                "no_rsi": rsi.get("no_rsi"),
            },
            "daily_loss_shadow": dict(daily_loss) if daily_loss else {},
            "risk_overruns": dict(pva) if pva else {},
            "robot_performance": [dict(r) for r in robot_perf],
            "asset_performance": [dict(r) for r in asset_perf],
            "live_sim_rejection_reasons": [dict(r) for r in live_sim_rejects],
            "funnel": funnel,
        },
        "rsi_shadow": rsi,
        "observational_only": True,
        "ai_conclusions": None,
    }
