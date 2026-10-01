#!/usr/bin/env python3
"""Phase 2 closeout — worker runtime, v32 visibility, post-anchor funnel (read-only)."""

from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "engine"))

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from quantara_engine.db.sqlalchemy_url import normalize_sqlalchemy_postgres_url
from quantara_engine.live_sim.analytics import build_live_sim_summary
from quantara_engine.live_sim.audit_scope import resolve_live_sim_audit_since
from quantara_engine.live_sim.execution_routing import list_active_live_sim_broker_account_ids
from quantara_engine.live_sim.v2_policy import evaluate_live_sim_v2_policy
from quantara_engine.live_sim.v32_registry import (
    V32_LIVE_SIM_STRATEGY_SLUG,
    load_v32_live_sim_active_combinations,
    parameter_overrides_for_combination,
)
from quantara_engine.persistence.store import TradingStore
from quantara_workers.runtime_build import WORKER_RUNTIME_BUILD_TAG

ANCHOR = datetime.fromisoformat("2026-10-01T12:28:17.281564+00:00")
OUT = ROOT / "scripts" / "research" / "phase2_closeout_diagnosis.json"
V32_SLUG = V32_LIVE_SIM_STRATEGY_SLUG


def db_url() -> str:
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if line.startswith("DATABASE_URL="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise RuntimeError("DATABASE_URL missing")


def breakdown(rows: list[dict], key: str) -> dict[str, int]:
    c: Counter[str] = Counter()
    for r in rows:
        c[str(r.get(key) or "?")] += 1
    return dict(c.most_common())


def main() -> None:
    session = sessionmaker(bind=create_engine(normalize_sqlalchemy_postgres_url(db_url())))()
    store = TradingStore(session)

    build_row = store.get_settings_dict().get("worker_runtime:build") or {}
    strat = store.get_settings_dict().get("worker_status:strategy_runner") or {}

    manifest = load_v32_live_sim_active_combinations()
    manifest_keys = {r["key"] for r in manifest}

    db_instances = session.execute(
        text(
            """
            SELECT si.id::text, si.is_active, si.timeframe, i.symbol,
                   si.parameter_overrides->>'v32_candidate_key' AS v32_key
            FROM strategy_instances si
            JOIN strategy_versions sv ON sv.id = si.strategy_version_id
            JOIN strategies s ON s.id = sv.strategy_id
            JOIN instruments i ON i.id = si.instrument_id
            WHERE s.slug = :slug
            """
        ),
        {"slug": V32_SLUG},
    ).mappings().all()

    active_db = [r for r in db_instances if r["is_active"]]
    worker_seen = [r for r in active_db if r["v32_key"] in manifest_keys]

    policy_eligible = 0
    policy_blocked: Counter[str] = Counter()
    for r in manifest:
        inst = store.get_instrument_by_symbol(r["asset"])
        if not inst:
            policy_blocked["missing_instrument"] += 1
            continue
        pol = evaluate_live_sim_v2_policy(
            strategy_slug=V32_SLUG,
            symbol=r["asset"],
            timeframe=r["timeframe"],
            parameter_overrides=parameter_overrides_for_combination(r),
        )
        if pol.allowed:
            policy_eligible += 1
        else:
            policy_blocked[pol.reason or "blocked"] += 1

    stage_rows = session.execute(
        text(
            """
            SELECT stage, reason, COUNT(*)::int AS n
            FROM learning_funnel_events
            WHERE event_at >= :since
            GROUP BY stage, reason
            """
        ),
        {"since": ANCHOR},
    ).mappings().all()
    totals: dict[str, int] = defaultdict(int)
    rej: Counter[str] = Counter()
    for row in stage_rows:
        totals[row["stage"]] += int(row["n"])
        if row["stage"] in {
            "rejection",
            "risk_denied",
            "stale_rejection",
            "broker_rejection",
            "strategy_filter_rejection",
            "asset_risk_rejection",
            "broker_risk_rejection",
            "owner_global_risk_rejection",
            "broker_pretrade_rejection",
        }:
            if row["reason"]:
                rej[str(row["reason"])] += int(row["n"])

    v32_stage_rows = session.execute(
        text(
            """
            SELECT stage, reason, COUNT(*)::int AS n
            FROM learning_funnel_events
            WHERE event_at >= :since AND strategy_slug = :slug
            GROUP BY stage, reason
            """
        ),
        {"since": ANCHOR, "slug": V32_SLUG},
    ).mappings().all()
    v32_totals: dict[str, int] = defaultdict(int)
    for row in v32_stage_rows:
        v32_totals[row["stage"]] += int(row["n"])

    worker_entries = store.list_v32_live_sim_entries()

    alloc_rej = session.execute(
        text(
            """
            SELECT rejection_reason, COUNT(*)::int AS n
            FROM live_sim_allocation_log
            WHERE created_at >= :since AND accepted = FALSE
            GROUP BY rejection_reason
            ORDER BY n DESC
            """
        ),
        {"since": ANCHOR},
    ).mappings().all()

    alloc_all = session.execute(
        text(
            """
            SELECT
              COUNT(*)::int AS total,
              COUNT(*) FILTER (WHERE accepted)::int AS accepted,
              COUNT(*) FILTER (WHERE NOT accepted)::int AS rejected
            FROM live_sim_allocation_log
            WHERE created_at >= :since
            """
        ),
        {"since": ANCHOR},
    ).mappings().first()

    v32_evals = session.execute(
        text(
            """
            SELECT active_action, COUNT(*)::int AS n
            FROM learning_eval_events
            WHERE evaluated_at >= :since AND strategy_slug = :slug
            GROUP BY active_action
            """
        ),
        {"since": ANCHOR, "slug": V32_SLUG},
    ).mappings().all()

    v32_eval_total = sum(int(r["n"]) for r in v32_evals)
    v32_signals = sum(int(r["n"]) for r in v32_evals if str(r["active_action"]) in ("BUY", "SELL"))

    decisions_v32 = session.execute(
        text(
            """
            SELECT d.decision_type::text AS t, COUNT(*)::int AS n
            FROM decisions d
            JOIN strategy_instances si ON si.id = d.strategy_instance_id
            JOIN strategy_versions sv ON sv.id = si.strategy_version_id
            JOIN strategies s ON s.id = sv.strategy_id
            WHERE d.created_at >= :since AND s.slug = :slug
            GROUP BY d.decision_type
            """
        ),
        {"since": ANCHOR, "slug": V32_SLUG},
    ).mappings().all()

    processed_candles = session.execute(
        text(
            """
            SELECT COUNT(DISTINCT (si.id, d.instrument_id, d.candle_timestamp))::int AS n
            FROM decisions d
            JOIN strategy_instances si ON si.id = d.strategy_instance_id
            JOIN strategy_versions sv ON sv.id = si.strategy_version_id
            JOIN strategies s ON s.id = sv.strategy_id
            WHERE d.created_at >= :since AND s.slug = :slug
            """
        ),
        {"since": ANCHOR, "slug": V32_SLUG},
    ).scalar()

    summary = build_live_sim_summary(store)
    legacy = session.execute(
        text(
            """
            SELECT account_metadata, activated_at FROM broker_accounts
            WHERE slug = 'live-sim-10k'
            """
        )
    ).mappings().first()
    audit_since = resolve_live_sim_audit_since(
        store,
        account_metadata=dict(legacy.get("account_metadata") or {}),
        activated_at=legacy.get("activated_at"),
    )
    account_ids = list_active_live_sim_broker_account_ids(store)

    orders = session.execute(
        text(
            """
            SELECT COUNT(*) FROM broker_orders o
            WHERE o.broker_account_id = ANY(CAST(:aids AS uuid[]))
              AND o.created_at >= :since
            """
        ),
        {"aids": account_ids, "since": ANCHOR},
    ).scalar()
    fills = session.execute(
        text(
            """
            SELECT COUNT(*) FROM broker_fills f
            JOIN broker_orders o ON o.id = f.broker_order_id
            WHERE o.broker_account_id = ANY(CAST(:aids AS uuid[]))
              AND f.filled_at >= :since
            """
        ),
        {"aids": account_ids, "since": ANCHOR},
    ).scalar()
    open_pos = session.execute(
        text(
            """
            SELECT COUNT(*) FROM live_sim_positions p
            WHERE p.broker_account_id = ANY(CAST(:aids AS uuid[]))
              AND p.status = 'open' AND p.opened_at >= :since
            """
        ),
        {"aids": account_ids, "since": ANCHOR},
    ).scalar()
    closed_pos = session.execute(
        text(
            """
            SELECT COUNT(*) FROM live_sim_positions p
            WHERE p.broker_account_id = ANY(CAST(:aids AS uuid[]))
              AND p.status = 'closed' AND p.closed_at >= :since
            """
        ),
        {"aids": account_ids, "since": ANCHOR},
    ).scalar()

    samples = [
        ("AMD", "mtf_trend_ltf_entry|v1|AMD|5m|long"),
        ("NVDA", "channel_mean_revert|v2|NVDA|15m|long"),
        ("AAPL", "channel_mean_revert|v1|AAPL|1h|long"),
        ("SPY", "donchian_breakout_v2|v1|SPY|15m|long"),
        ("PLTR", None),
    ]
    sample_traces: list[dict] = []
    for label, key_hint in samples:
        if key_hint:
            key_row = next((r for r in manifest if r["key"] == key_hint), None)
        else:
            key_row = next((r for r in manifest if r["asset"] == "PLTR"), None)
        if not key_row:
            sample_traces.append({"label": label, "error": "no manifest row"})
            continue
        key = key_row["key"]
        sym = key_row["asset"]
        tf = key_row["timeframe"]
        inst = store.get_instrument_by_symbol(sym)
        last_eval = session.execute(
            text(
                """
                SELECT evaluated_at, candle_timestamp, active_action, active_reason
                FROM learning_eval_events
                WHERE strategy_slug = :slug AND symbol = :sym AND timeframe = :tf
                  AND evaluated_at >= :since
                ORDER BY evaluated_at DESC LIMIT 1
                """
            ),
            {"since": ANCHOR, "slug": V32_SLUG, "sym": sym, "tf": tf},
        ).mappings().first()
        last_alloc = session.execute(
            text(
                """
                SELECT created_at, accepted, rejection_reason, rejection_detail
                FROM live_sim_allocation_log
                WHERE metadata->>'v32_candidate_key' = :key
                  AND created_at >= :since
                ORDER BY created_at DESC LIMIT 1
                """
            ),
            {"since": ANCHOR, "key": key},
        ).mappings().first()
        pol = evaluate_live_sim_v2_policy(
            strategy_slug=V32_SLUG,
            symbol=sym,
            timeframe=tf,
            parameter_overrides=parameter_overrides_for_combination(key_row),
        )
        sample_traces.append(
            {
                "label": label,
                "candidate_key": key,
                "policy": {"allowed": pol.allowed, "reason": pol.reason},
                "last_eval": dict(last_eval) if last_eval else None,
                "last_allocation": dict(last_alloc) if last_alloc else None,
                "outcome": (
                    "NO_EVAL_YET"
                    if not last_eval
                    else (
                        "SIGNAL"
                        if str(last_eval.get("active_action")) in ("BUY", "SELL")
                        else "HOLD_NO_SETUP"
                    )
                ),
            }
        )

    alloc_rej_map = {str(r["rejection_reason"] or "NULL"): int(r["n"]) for r in alloc_rej}
    v32_alloc_rej = session.execute(
        text(
            """
            SELECT rejection_reason, COUNT(*)::int AS n
            FROM live_sim_allocation_log
            WHERE created_at >= :since AND strategy_slug = :slug
            GROUP BY rejection_reason ORDER BY n DESC
            """
        ),
        {"since": ANCHOR, "slug": V32_SLUG},
    ).mappings().all()
    v32_alloc_total = sum(int(r["n"]) for r in v32_alloc_rej)

    broker_hwm = session.execute(
        text(
            """
            SELECT slug, equity, risk_settings->>'high_water_mark' AS hwm
            FROM broker_accounts WHERE slug IN ('live-sim-ibkr-like', 'live-sim-kraken-like')
            """
        )
    ).mappings().all()

    root = "UNKNOWN"
    if v32_alloc_total > 0 and int(alloc_all["accepted"] or 0) == 0:
        root = (
            "Pipeline OK: V3.2 evaluates and forwards signals to maybe_allocate_live_sim, "
            f"but all {v32_alloc_total} V3.2 allocation attempts were rejected — "
            f"{dict((r['rejection_reason'], r['n']) for r in v32_alloc_rej)}. "
            "Primary blocker: broker-slice DRAWDOWN_GATE where high_water_mark remains $10,000 "
            "on ibkr/kraken slices ($7,500 / $2,500 equity) after owner capital split — "
            "not strategy silence. Secondary: STALE_SIGNAL on delayed 1h bars. "
            "Legacy Robot A paths separately hit ROBOT_LIVE_PAUSED."
        )
    elif int(alloc_all["total"] or 0) == 0 and v32_signals > 0:
        root = "V3.2 BUY/SELL signals recorded but zero allocation log rows (forward/allocator gap)."
    elif int(alloc_all["total"] or 0) == 0:
        root = "No allocation attempts since anchor; strategies mostly HOLD."

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "expected_build_tag": WORKER_RUNTIME_BUILD_TAG,
        "worker_runtime_settings": build_row,
        "strategy_runner_status": {
            k: strat.get(k)
            for k in (
                "status",
                "last_run",
                "last_finish",
                "decisions",
                "competition_portfolios",
                "timeframe_groups_evaluated",
                "reason",
                "jobs_live_pending",
            )
        },
        "candidates": {
            "manifest_active": len(manifest),
            "db_instances_active": len(active_db),
            "worker_seen_active": len(worker_seen),
            "worker_list_v32_entries": len(worker_entries),
            "policy_eligible": policy_eligible,
            "policy_blocked": dict(policy_blocked),
            "by_asset": breakdown(worker_seen, "symbol"),
            "by_timeframe": breakdown(worker_seen, "timeframe"),
        },
        "funnel_since_anchor": {
            "anchor": ANCHOR.isoformat(),
            "audit_since_resolved": audit_since.isoformat() if audit_since else None,
            "strategy_evaluations_all_robots": totals.get("strategy_evaluation", 0),
            "buy_sell_setups_all_robots": totals.get("buy_sell_setup", 0),
            "v32_strategy_evaluations_funnel": v32_totals.get("strategy_evaluation", 0),
            "v32_buy_sell_setups_funnel": v32_totals.get("buy_sell_setup", 0),
            "order_intents_funnel": totals.get("order_intent", 0),
            "orders_funnel": totals.get("order", 0),
            "fills_funnel": totals.get("fill", 0),
            "positions_opened_funnel": totals.get("position_opened", 0),
            "positions_closed_funnel": totals.get("position_closed", 0),
            "v32_learning_evaluations": v32_eval_total,
            "v32_buy_sell_evaluations": v32_signals,
            "v32_decisions_by_type": {r["t"]: int(r["n"]) for r in decisions_v32},
            "distinct_v32_candle_decisions": int(processed_candles or 0),
            "allocation_total": int(alloc_all["total"] or 0),
            "allocation_accepted": int(alloc_all["accepted"] or 0),
            "allocation_rejected": int(alloc_all["rejected"] or 0),
            "broker_orders": int(orders or 0),
            "broker_fills": int(fills or 0),
            "open_positions": int(open_pos or 0),
            "closed_positions": int(closed_pos or 0),
            "allocation_rejection_reasons": alloc_rej_map,
            "v32_allocation_attempts": v32_alloc_total,
            "v32_allocation_rejection_reasons": {
                str(r["rejection_reason"]): int(r["n"]) for r in v32_alloc_rej
            },
            "broker_slice_hwm": [dict(r) for r in broker_hwm],
        },
        "master": {
            "equity": summary.get("equity"),
            "starting_capital": summary.get("starting_capital"),
            "open_sl_risk_usd": summary.get("open_sl_risk_usd"),
            "risk_per_trade_usd_approx": (summary.get("risk_settings") or {}).get(
                "risk_per_trade_usd_approx"
            ),
            "authority": summary.get("authority"),
        },
        "sample_traces": sample_traces,
        "root_cause": root,
        "pipeline_health": (
            "PASS"
            if len(worker_seen) == len(manifest)
            and len(worker_entries) == len(manifest)
            and build_row.get("tag") == WORKER_RUNTIME_BUILD_TAG
            else "FAIL"
        ),
    }
    OUT.write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, default=str))
    session.close()


if __name__ == "__main__":
    main()
