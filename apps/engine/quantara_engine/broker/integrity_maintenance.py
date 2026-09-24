"""Lightweight periodic attribution integrity — no strategy/risk changes."""

from __future__ import annotations

from typing import Any

from quantara_engine.broker.attribution import link_orphan_lots_via_intent_chain
from quantara_engine.live_sim.shadow_exit_sync import reconcile_stale_live_sim_shadows
from quantara_engine.persistence.store import TradingStore


def run_attribution_integrity_maintenance(store: TradingStore) -> dict[str, Any]:
    """
    Link orphan lots and reconcile stale Live Sim shadows.

    Safe to run frequently; no-op when nothing is linkable or stale.
    """
    linked = link_orphan_lots_via_intent_chain(store)
    stale_closed = reconcile_stale_live_sim_shadows(store)
    return {
        "orphan_lots_linked": len(linked),
        "linked": linked,
        "stale_shadows_closed": len(stale_closed),
        "stale_shadows": stale_closed,
    }
