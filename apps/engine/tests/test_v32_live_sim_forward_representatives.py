"""V3.2 Live Sim must forward each qualified combination — not one per shared slug."""

from __future__ import annotations

from unittest.mock import MagicMock

from quantara_engine.live_sim.v32_registry import V32_LIVE_SIM_STRATEGY_SLUG
from quantara_workers.jobs.run_strategy import (
    _canonical_strategy_representatives,
    _live_sim_forward_representatives,
)


def _v32_entry(key: str) -> dict:
    return {
        "portfolio": MagicMock(id=f"p-{key}"),
        "instance": MagicMock(
            id=f"i-{key}",
            strategy_slug=V32_LIVE_SIM_STRATEGY_SLUG,
            parameter_overrides={"v32_candidate_key": key, "symbol": "AMD", "timeframe": "15m"},
        ),
        "risk_profile": MagicMock(slug="balanced"),
    }


def test_v32_same_symbol_timeframe_forwards_all_combinations():
    group = [
        _v32_entry("rsi_divergence_mr|v1|AMD|15m|long"),
        _v32_entry("ema_pullback_continue|v1|AMD|15m|long"),
        _v32_entry("orb_continuation|v1|AMD|15m|long"),
    ]
    reps = _live_sim_forward_representatives(group)
    assert len(reps) == 3
    assert len(_canonical_strategy_representatives(group)) == 1


def test_cde_still_dedupes_by_strategy_slug():
    group = [
        {
            "portfolio": MagicMock(id="p1"),
            "instance": MagicMock(
                id="i1",
                strategy_slug="mean-reversion",
                parameter_overrides={},
            ),
            "risk_profile": MagicMock(slug="balanced"),
        },
        {
            "portfolio": MagicMock(id="p2"),
            "instance": MagicMock(
                id="i2",
                strategy_slug="volatility-squeeze",
                parameter_overrides={},
            ),
            "risk_profile": MagicMock(slug="balanced"),
        },
        {
            "portfolio": MagicMock(id="p3"),
            "instance": MagicMock(
                id="i3",
                strategy_slug="mean-reversion",
                parameter_overrides={},
            ),
            "risk_profile": MagicMock(slug="aggressive"),
        },
    ]
    reps = _live_sim_forward_representatives(group)
    assert len(reps) == 2
    slugs = {e["instance"].strategy_slug for e in reps}
    assert slugs == {"mean-reversion", "volatility-squeeze"}
