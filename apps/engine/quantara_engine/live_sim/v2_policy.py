"""Live Sim V2 routing policy — robots, assets, combinations (Research unaffected)."""

from __future__ import annotations

from dataclasses import dataclass

from quantara_engine.competition.robot_registry import ROBOT_LABELS

# Strategy slugs paused for Live Sim only (Research + Learning continue).
LIVE_SIM_PAUSED_STRATEGY_SLUGS: frozenset[str] = frozenset(
    {
        "volatility-squeeze",  # Robot D
        "momentum-continuation",  # Robot E
    }
)

# Live Sim research-only assets (no new Live entries).
LIVE_SIM_RESEARCH_ONLY_SYMBOLS: frozenset[str] = frozenset({"COIN"})

# Blocked robot × asset × timeframe (normalized tf: 5m, 15m, 1h).
LIVE_SIM_BLOCKED_COMBINATIONS: frozenset[tuple[str, str, str]] = frozenset(
    {
        ("gold-trend-pullback", "XAUUSD", "1h"),
        ("gold-trend-pullback", "AMD", "5m"),
        ("gold-trend-pullback", "AMD", "15m"),
        ("gold-trend-pullback", "AMD", "1h"),
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
    tf = normalize_timeframe(timeframe)
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
