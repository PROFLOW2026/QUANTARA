"""V3.2 virtual Live Sim — owner-facing experiment metadata (no trading logic)."""

from __future__ import annotations

from typing import Any

from quantara_engine.live_sim.v2_policy import V32_LIVE_SIM_STRATEGY_SLUG
from quantara_engine.live_sim.v32_registry import load_v32_live_sim_active_combinations

V32_EXPERIMENT_ID = "v3.2-p2"
V32_OWNER_LABEL = "QUANTARA V3.2"
V32_STARTING_MODEL_EQUITY_USD = 10_000
V32_INITIAL_RISK_PCT = 1.0


def _active_strategy_rows() -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for c in load_v32_live_sim_active_combinations():
        variant = c.get("variant_id") or str(c["key"]).split("|")[1]
        rows.append(
            {
                "display": f"{c['family']} {c['asset']} {c['timeframe']} {c['direction']}",
                "key": c["key"],
                "family": c["family"],
                "version": variant,
                "symbol": c["asset"],
                "timeframe": c["timeframe"],
                "direction": c["direction"],
                "robustness": str(c.get("robustness") or ""),
            }
        )
    return rows


def build_v32_owner_experiment_payload(
    *,
    observation_anchor_iso: str | None,
    account_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    meta = account_metadata or {}
    anchor = observation_anchor_iso or meta.get("v32_observation_anchor") or meta.get("baseline_reset_at")
    return {
        "experiment_id": V32_EXPERIMENT_ID,
        "label": V32_OWNER_LABEL,
        "portfolio": "qualified_v32",
        "strategy_slug": V32_LIVE_SIM_STRATEGY_SLUG,
        "risk_per_trade_pct": V32_INITIAL_RISK_PCT,
        "starting_model_equity_usd": V32_STARTING_MODEL_EQUITY_USD,
        "active_strategies": _active_strategy_rows(),
        "qualified_candidate_count": len(_active_strategy_rows()),
        "observation_anchor": anchor,
        "legacy_ae_live_disabled": True,
    }
