"""Decision funnel aggregation from canonical reasons + learning eval events."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import text

logger = logging.getLogger(__name__)

ROBOT_BY_SLUG = {
    "gold-trend-pullback": "Robot A",
    "opening-range-breakout": "Robot B",
    "mean-reversion": "Robot C",
    "volatility-squeeze": "Robot D",
    "momentum-continuation": "Robot E",
}


def robot_label_for_slug(slug: str | None) -> str | None:
    if not slug:
        return None
    return ROBOT_BY_SLUG.get(slug)


def record_funnel_event(
    store,
    *,
    baseline_id: str | None,
    stage: str,
    reason: str | None = None,
    strategy_slug: str | None = None,
    symbol: str | None = None,
    timeframe: str | None = None,
    direction: str | None = None,
    candle_timestamp: datetime | None = None,
    source_table: str | None = None,
    source_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    try:
        store.session.execute(
            text(
                """
                INSERT INTO learning_funnel_events (
                  id, baseline_id, event_at, candle_timestamp, strategy_slug, robot_label,
                  symbol, timeframe, direction, stage, reason, source_table, source_id, metadata
                ) VALUES (
                  CAST(:id AS uuid), CAST(:baseline_id AS uuid), NOW(), :candle_timestamp,
                  :strategy_slug, :robot_label, :symbol, :timeframe, :direction,
                  :stage, :reason, :source_table, CAST(:source_id AS uuid), CAST(:metadata AS jsonb)
                )
                """
            ),
            {
                "id": str(uuid4()),
                "baseline_id": baseline_id,
                "candle_timestamp": candle_timestamp,
                "strategy_slug": strategy_slug,
                "robot_label": robot_label_for_slug(strategy_slug),
                "symbol": symbol,
                "timeframe": timeframe,
                "direction": direction,
                "stage": stage,
                "reason": reason,
                "source_table": source_table,
                "source_id": source_id,
                "metadata": json.dumps({**(metadata or {}), "observational_only": True}, default=str),
            },
        )
    except Exception:
        logger.exception("Failed to record funnel event (non-fatal)")


def build_decision_funnel(
    store,
    *,
    since: datetime | None,
    until: datetime | None = None,
) -> dict[str, Any]:
    """Aggregate funnel using learning events + canonical decisions/intents/trades/live-sim log."""
    params: dict[str, Any] = {}
    time_clause = "TRUE"
    if since is not None:
        time_clause = "event_at >= :since"
        params["since"] = since
    if until is not None:
        time_clause += " AND event_at < :until"
        params["until"] = until

    stages = store.session.execute(
        text(
            f"""
            SELECT stage, reason, strategy_slug, robot_label, symbol, timeframe, direction,
                   COUNT(*)::int AS n
            FROM learning_funnel_events
            WHERE {time_clause}
            GROUP BY stage, reason, strategy_slug, robot_label, symbol, timeframe, direction
            """
        ),
        params,
    ).mappings().all()

    # Canonical decisions (research) since activation
    dec_params: dict[str, Any] = {}
    dec_clause = "TRUE"
    if since is not None:
        dec_clause = "d.created_at >= :since"
        dec_params["since"] = since
    if until is not None:
        dec_clause += " AND d.created_at < :until"
        dec_params["until"] = until

    decisions = store.session.execute(
        text(
            f"""
            SELECT d.decision_type::text AS decision_type, d.message,
                   s.slug AS strategy_slug, i.symbol, si.timeframe,
                   COUNT(*)::int AS n
            FROM decisions d
            JOIN strategy_instances si ON si.id = d.strategy_instance_id
            JOIN strategy_versions sv ON sv.id = si.strategy_version_id
            JOIN strategies s ON s.id = sv.strategy_id
            JOIN instruments i ON i.id = d.instrument_id
            WHERE {dec_clause}
            GROUP BY d.decision_type, d.message, s.slug, i.symbol, si.timeframe
            """
        ),
        dec_params,
    ).mappings().all()

    live_sim = store.session.execute(
        text(
            f"""
            SELECT status, rejection_reason, canonical_symbol AS symbol, timeframe, direction,
                   COUNT(*)::int AS n
            FROM live_sim_allocation_log
            WHERE {"created_at >= :since" if since else "TRUE"}
              {"AND created_at < :until" if until else ""}
            GROUP BY status, rejection_reason, canonical_symbol, timeframe, direction
            """
        ),
        {k: v for k, v in (("since", since), ("until", until)) if v is not None},
    ).mappings().all()

    rejection_counts: dict[str, int] = {}
    for row in stages:
        if row["stage"] in {"rejection", "risk_denied", "stale_rejection", "broker_rejection"} and row["reason"]:
            rejection_counts[row["reason"]] = rejection_counts.get(row["reason"], 0) + int(row["n"])
    for row in decisions:
        if row["decision_type"] in {
            "risk_denied",
            "broker_capability_denied",
            "broker_rejected",
            "no_setup",
            "hold",
        }:
            key = row["message"] or row["decision_type"]
            rejection_counts[key] = rejection_counts.get(key, 0) + int(row["n"])
    for row in live_sim:
        if row["rejection_reason"]:
            rejection_counts[row["rejection_reason"]] = (
                rejection_counts.get(row["rejection_reason"], 0) + int(row["n"])
            )

    def _sum_stage(name: str) -> int:
        return sum(int(r["n"]) for r in stages if r["stage"] == name)

    return {
        "by_stage": [
            {
                "stage": r["stage"],
                "reason": r["reason"],
                "strategy_slug": r["strategy_slug"],
                "robot_label": r["robot_label"],
                "symbol": r["symbol"],
                "timeframe": r["timeframe"],
                "direction": r["direction"],
                "count": int(r["n"]),
            }
            for r in stages
        ],
        "canonical_decisions": [dict(r) for r in decisions],
        "live_sim_allocations": [dict(r) for r in live_sim],
        "rejection_reason_counts": rejection_counts,
        "totals": {
            "strategy_evaluations": _sum_stage("strategy_evaluation"),
            "buy_sell_setups": _sum_stage("buy_sell_setup"),
            "hold_no_setup": _sum_stage("hold_no_setup"),
            "fresh_candidates": _sum_stage("fresh_candidate"),
            "stale_rejections": _sum_stage("stale_rejection"),
            "strategy_filter_rejections": _sum_stage("strategy_filter_rejection"),
            "asset_risk_rejections": _sum_stage("asset_risk_rejection"),
            "broker_risk_rejections": _sum_stage("broker_risk_rejection"),
            "owner_global_risk_rejections": _sum_stage("owner_global_risk_rejection"),
            "broker_pretrade_rejections": _sum_stage("broker_pretrade_rejection"),
            "order_intents": _sum_stage("order_intent"),
            "orders": _sum_stage("order"),
            "fills": _sum_stage("fill"),
            "positions_opened": _sum_stage("position_opened"),
            "positions_closed": _sum_stage("position_closed"),
            "wins": _sum_stage("win"),
            "losses": _sum_stage("loss"),
        },
    }
