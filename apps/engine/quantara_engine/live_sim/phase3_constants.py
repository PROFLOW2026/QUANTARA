"""Phase 3 edge-discovery experiment identifiers (Live Sim measurement)."""

from __future__ import annotations

PHASE3_OBSERVATION_ANCHOR_KEY = "phase3_edge_discovery_anchor"

PHASE3_MOMENTUM_INTRADAY_SLUG = "phase3-momentum-intraday"
PHASE3_VOLUME_BREAKOUT_SLUG = "phase3-volume-breakout"
PHASE3_MEAN_REVERSION_EDGE_SLUG = "phase3-mean-reversion-edge"

PHASE3_STRATEGY_SLUGS: frozenset[str] = frozenset(
    {
        PHASE3_MOMENTUM_INTRADAY_SLUG,
        PHASE3_VOLUME_BREAKOUT_SLUG,
        PHASE3_MEAN_REVERSION_EDGE_SLUG,
    }
)

EXPERIMENT_ID_BY_SLUG: dict[str, str] = {
    PHASE3_MOMENTUM_INTRADAY_SLUG: "phase3_momentum_intraday_2026",
    PHASE3_VOLUME_BREAKOUT_SLUG: "phase3_volume_breakout_2026",
    PHASE3_MEAN_REVERSION_EDGE_SLUG: "phase3_mean_reversion_edge_2026",
}

# Robot A (gold-trend-pullback) — Phase 3 narrow book: macro metals/FX only.
ROBOT_A_LIVE_SIM_SYMBOLS: frozenset[str] = frozenset({"XAUUSD", "GBPJPY"})
