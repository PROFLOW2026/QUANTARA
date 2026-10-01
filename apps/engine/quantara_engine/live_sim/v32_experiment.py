"""V3.2 P2 virtual Live Sim — owner-facing experiment metadata (no trading logic)."""

from __future__ import annotations

from typing import Any

from quantara_engine.live_sim.v2_policy import V32_LIVE_SIM_STRATEGY_SLUG

V32_EXPERIMENT_ID = "v3.2-p2"
V32_OWNER_LABEL = "QUANTARA V3.2"
V32_STARTING_MODEL_EQUITY_USD = 10_000
V32_INITIAL_RISK_PCT = 0.25

V32_ACTIVE_STRATEGIES: tuple[dict[str, str], ...] = (
    {
        "display": "AMD RSI Divergence",
        "family": "rsi_divergence_mr",
        "version": "v1",
        "symbol": "AMD",
        "timeframe": "15m",
        "direction": "long",
    },
    {
        "display": "NVDA EMA Pullback",
        "family": "ema_pullback_continue",
        "version": "v2",
        "symbol": "NVDA",
        "timeframe": "15m",
        "direction": "long",
    },
)


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
        "portfolio": "P2",
        "strategy_slug": V32_LIVE_SIM_STRATEGY_SLUG,
        "risk_per_trade_pct": V32_INITIAL_RISK_PCT,
        "starting_model_equity_usd": V32_STARTING_MODEL_EQUITY_USD,
        "active_strategies": list(V32_ACTIVE_STRATEGIES),
        "observation_anchor": anchor,
        "legacy_ae_live_disabled": True,
    }
