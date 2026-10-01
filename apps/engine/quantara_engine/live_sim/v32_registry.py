"""V3.2 Stage-B qualified Live Sim candidates — canonical registry (Research artifacts)."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from quantara_engine.broker.accounts import LIVE_SIM_VIRTUAL_PORTFOLIO_ID
from quantara_engine.research.v3_2.job_plan import job_key

V32_LIVE_SIM_STRATEGY_SLUG = "v32-p2-live-sim"
V32_LIVE_SIM_EXPERIMENT_ID = "00000000-0000-4000-8010-000000000032"


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
V32_QUALIFIED_MANIFEST = Path(__file__).with_name("v32_qualified_candidates.json")

# Explicit Live Sim blocks (not Stage-B qualified / owner research-only).
V32_LIVE_SIM_BLOCKED_KEYS: frozenset[str] = frozenset()

# Stage-B classifications excluded from virtual Live Sim (manifest may retain for research).
V32_LIVE_SIM_BLOCKED_CLASSIFICATIONS: frozenset[str] = frozenset({"FAIL"})

# FX Live Sim requires fresh 5m marks (Tiingo/Twelve Data); research may use stale history.
V32_FX_LIVE_SYMBOLS: frozenset[str] = frozenset({"EURUSD", "USDJPY"})
FX_LIVE_MAX_5M_AGE_HOURS: float = 48.0


@lru_cache(maxsize=1)
def load_v32_qualified_combinations() -> tuple[dict[str, Any], ...]:
    if not V32_QUALIFIED_MANIFEST.is_file():
        return ()
    raw = json.loads(V32_QUALIFIED_MANIFEST.read_text(encoding="utf-8"))
    rows = raw.get("qualified") or []
    return tuple(dict(r) for r in rows if r.get("key"))


@lru_cache(maxsize=1)
def v32_qualified_keys() -> frozenset[str]:
    return frozenset(r["key"] for r in load_v32_qualified_combinations())


def v32_row_live_sim_eligible(row: dict[str, Any]) -> bool:
    key = row.get("key")
    if not key or key in V32_LIVE_SIM_BLOCKED_KEYS:
        return False
    cls = str(row.get("classification") or "").upper()
    return cls not in V32_LIVE_SIM_BLOCKED_CLASSIFICATIONS


def load_v32_live_sim_active_combinations() -> tuple[dict[str, Any], ...]:
    """Manifest rows permitted for Live Sim execution and instance sync."""
    return tuple(r for r in load_v32_qualified_combinations() if v32_row_live_sim_eligible(r))


def v32_candidate_key_from_params(params: dict[str, Any] | None) -> str | None:
    if not params:
        return None
    explicit = params.get("v32_candidate_key")
    if explicit:
        return str(explicit)
    family = params.get("family")
    direction = params.get("trade_direction")
    asset = params.get("symbol")
    if not family or not direction or not asset:
        return None
    variant = str(params.get("v32_variant_id") or "v1")
    tf = normalize_timeframe(str(params.get("timeframe") or "15m"))
    param_subset = {
        k: v
        for k, v in params.items()
        if k
        not in (
            "family",
            "trade_direction",
            "symbol",
            "timeframe",
            "v32_candidate_key",
            "v32_variant_id",
            "atr_sl",
            "atr_tp",
        )
    }
    return job_key(str(family), variant, str(asset).upper(), tf, str(direction))


def is_v32_qualified_candidate_key(key: str | None) -> bool:
    if not key or key in V32_LIVE_SIM_BLOCKED_KEYS:
        return False
    for row in load_v32_qualified_combinations():
        if row["key"] == key:
            return v32_row_live_sim_eligible(row)
    return False


def v32_instance_id_for_key(key: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"quantara:v32-live-sim:{key}"))


def v32_portfolio_id_for_key(key: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"quantara:v32-portfolio:{key}"))


def parameter_overrides_for_combination(row: dict[str, Any]) -> dict[str, Any]:
    params = dict(row.get("parameters") or {})
    variant = row.get("variant_id") or row["key"].split("|")[1]
    return {
        **params,
        "family": row["family"],
        "trade_direction": row["direction"],
        "symbol": row["asset"],
        "timeframe": row["timeframe"],
        "v32_candidate_key": row["key"],
        "v32_variant_id": variant,
    }


def v32_live_sim_portfolio_id() -> str:
    """Legacy single-portfolio id (broker attribution); candidates use per-key portfolios."""
    return LIVE_SIM_VIRTUAL_PORTFOLIO_ID
