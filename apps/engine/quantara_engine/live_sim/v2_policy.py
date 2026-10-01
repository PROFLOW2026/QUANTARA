"""Live Sim V2 routing policy — robots, assets, combinations (Research unaffected)."""

from __future__ import annotations

from dataclasses import dataclass

from quantara_engine.competition.robot_registry import ROBOT_LABELS

# Strategy slugs paused for Live Sim only (Research + Learning continue).
LIVE_SIM_PAUSED_STRATEGY_SLUGS: frozenset[str] = frozenset(
    {
        "gold-trend-pullback",  # Robot A
        "opening-range-breakout",  # Robot B
        "mean-reversion",  # Robot C
        "volatility-squeeze",  # Robot D
        "momentum-continuation",  # Robot E
    }
)

V32_LIVE_SIM_STRATEGY_SLUG = "v32-p2-live-sim"

# V3.2 P2 virtual Live Sim (full broker Stage-B survivors).
V32_LIVE_SIM_ALLOWED: frozenset[tuple[str, str, str]] = frozenset(
    {
        (V32_LIVE_SIM_STRATEGY_SLUG, "AMD", "15m"),  # rsi_divergence_mr v1 long
        (V32_LIVE_SIM_STRATEGY_SLUG, "NVDA", "15m"),  # ema_pullback_continue v2 long
    }
)

# Legacy V3.1 finalists — superseded by V3.2 P2; keep empty for policy checks.
V31_LIVE_SIM_STRATEGY_SLUG = V32_LIVE_SIM_STRATEGY_SLUG
V31_LIVE_SIM_ALLOWED: frozenset[tuple[str, str, str]] = frozenset()

# Live Sim research-only assets (no new Live entries unless in V32 allowlist).
LIVE_SIM_RESEARCH_ONLY_SYMBOLS: frozenset[str] = frozenset({"COIN", "TSLA"})

# Blocked robot × asset × timeframe (normalized tf: 5m, 15m, 1h).
LIVE_SIM_BLOCKED_COMBINATIONS: frozenset[tuple[str, str, str]] = frozenset(
    {
        ("gold-trend-pullback", "XAUUSD", "1h"),
        ("gold-trend-pullback", "AMD", "5m"),
        ("gold-trend-pullback", "AMD", "15m"),
        ("gold-trend-pullback", "AMD", "1h"),
        ("v32-p2-live-sim", "AMD", "5m"),  # AMD ORB 5m — RESEARCH_ONLY
        ("v31-rule-replay", "AMD", "5m"),  # legacy slug — RESEARCH_ONLY
    }
)


def normalize_timeframe(tf: str | None) -> str:
    if not tf:
        return "?"
    t = str(tf).lower().replace("timeframe.", "")
    if t in ("m5", "5m", "orb_5m"):
        return "5m"
    if t in ("m15", "15m", "cde_15m"):
        return "15m"
    if t in ("h1", "1h"):
        return "1h"
    return t


@dataclass(frozen=True)
class LiveSimPolicyVerdict:
    allowed: bool
    reason: str | None = None
    category: str | None = None


def evaluate_live_sim_v2_policy(
    *,
    strategy_slug: str,
    symbol: str,
    timeframe: str | None,
) -> LiveSimPolicyVerdict:
    sym = symbol.upper().replace("/", "")
    tf = normalize_timeframe(timeframe)
    if strategy_slug in (V32_LIVE_SIM_STRATEGY_SLUG, "v31-rule-replay"):
        slug = V32_LIVE_SIM_STRATEGY_SLUG
        combo = (slug, sym, tf)
        if combo not in V32_LIVE_SIM_ALLOWED:
            return LiveSimPolicyVerdict(
                allowed=False,
                reason="V32_COMBINATION_NOT_IN_PORTFOLIO",
                category="ROBOT_POLICY",
            )
        return LiveSimPolicyVerdict(allowed=True)
    if strategy_slug in LIVE_SIM_PAUSED_STRATEGY_SLUGS:
        return LiveSimPolicyVerdict(
            allowed=False,
            reason="ROBOT_LIVE_PAUSED",
            category="ROBOT_POLICY",
        )
    if sym in LIVE_SIM_RESEARCH_ONLY_SYMBOLS:
        return LiveSimPolicyVerdict(
            allowed=False,
            reason="ASSET_RESEARCH_ONLY",
            category="ASSET_POLICY",
        )
    combo = (strategy_slug, sym, tf)
    if combo in LIVE_SIM_BLOCKED_COMBINATIONS:
        return LiveSimPolicyVerdict(
            allowed=False,
            reason="COMBINATION_BLOCKED",
            category="ASSET_POLICY",
        )
    return LiveSimPolicyVerdict(allowed=True)


def live_active_robot_slugs() -> frozenset[str]:
    paused = LIVE_SIM_PAUSED_STRATEGY_SLUGS
    return frozenset(slug for slug in ROBOT_LABELS if slug not in paused)
