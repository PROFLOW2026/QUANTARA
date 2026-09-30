"""Baseline metrics for frozen A/B/C/D/E from Research competition trades."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import text

from quantara_engine.persistence.store import TradingStore
from quantara_engine.research.v3.constants import BASELINE_STRATEGY_SLUGS
from quantara_engine.research.v3.metrics import trade_metrics


def research_baseline_rows(
    store: TradingStore,
    *,
    since: datetime | None = None,
    until: datetime | None = None,
) -> list[dict]:
    return list(
        store.session.execute(
            text(
                """
                SELECT s.slug AS strategy_slug,
                       i.symbol,
                       si.timeframe::text AS timeframe,
                       t.direction::text AS direction,
                       t.realized_pnl::float AS pnl,
                       t.actual_risk_amount::float AS risk_usd,
                       t.closed_at,
                       t.exit_reason::text AS exit_reason
                FROM trades t
                JOIN strategy_instances si ON si.id = t.strategy_instance_id
                JOIN strategy_versions sv ON sv.id = si.strategy_version_id
                JOIN strategies s ON s.id = sv.strategy_id
                JOIN instruments i ON i.id = t.instrument_id
                WHERE si.experiment_id IS NOT NULL
                  AND t.closed_at IS NOT NULL
                  AND (:since IS NULL OR t.closed_at >= :since)
                  AND (:until IS NULL OR t.closed_at < :until)
                ORDER BY t.closed_at ASC
                """
            ),
            {"since": since, "until": until},
        ).mappings()
    )


def aggregate_baseline_report(
    store: TradingStore,
    *,
    since: datetime | None = None,
    until: datetime | None = None,
) -> dict[str, Any]:
    rows = research_baseline_rows(store, since=since, until=until)
    slug_to_robot = {v: k for k, v in BASELINE_STRATEGY_SLUGS.items()}
    buckets: dict[tuple, list[float]] = {}
    risk_buckets: dict[tuple, list[float]] = {}
    exit_counts: dict[tuple, dict[str, int]] = {}

    for r in rows:
        slug = str(r["strategy_slug"])
        if slug not in slug_to_robot:
            continue
        key = (
            slug_to_robot[slug],
            str(r["symbol"]),
            str(r["timeframe"]),
            str(r["direction"]),
        )
        buckets.setdefault(key, []).append(float(r["pnl"] or 0))
        if r.get("risk_usd"):
            risk_buckets.setdefault(key, []).append(float(r["risk_usd"]))
        er = str(r.get("exit_reason") or "unknown")
        exit_counts.setdefault(key, {})
        exit_counts[key][er] = exit_counts[key].get(er, 0) + 1

    combos: list[dict] = []
    for key, pnls in sorted(buckets.items()):
        robot, sym, tf, direction = key
        risks = risk_buckets.get(key)
        m = trade_metrics(pnls, risk_usd=risks)
        exits = exit_counts.get(key, {})
        total_exits = sum(exits.values()) or 1
        combos.append(
            {
                "robot": robot,
                "strategy_slug": BASELINE_STRATEGY_SLUGS[robot],
                "asset": sym,
                "timeframe": tf,
                "direction": direction,
                **m,
                "sl_hit_pct": round(exits.get("stop_loss", 0) / total_exits * 100, 2),
                "tp_hit_pct": round(exits.get("take_profit", 0) / total_exits * 100, 2),
                "strategy_exit_pct": round(exits.get("strategy", 0) / total_exits * 100, 2),
            }
        )

    by_robot: dict[str, Any] = {}
    for robot in BASELINE_STRATEGY_SLUGS:
        robot_pnls = [
            float(r["pnl"])
            for r in rows
            if slug_to_robot.get(str(r["strategy_slug"])) == robot
        ]
        by_robot[robot] = trade_metrics(robot_pnls)

    return {
        "since": since.isoformat() if since else None,
        "until": until.isoformat() if until else None,
        "by_robot": by_robot,
        "combinations": combos,
        "combination_count": len(combos),
    }
