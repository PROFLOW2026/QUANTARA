"""Lightweight periodic attribution integrity — no strategy/risk changes."""

from __future__ import annotations

from typing import Any

from quantara_engine.broker.attribution import link_orphan_lots_via_intent_chain
from quantara_engine.persistence.store import TradingStore


def run_attribution_integrity_maintenance(store: TradingStore) -> dict[str, Any]:
    """
    Link Research/Live Sim orphan lots when intent → fill → position evidence exists.

    Safe to run frequently; no-op when nothing is linkable.
    """
    linked = link_orphan_lots_via_intent_chain(store)
    return {"orphan_lots_linked": len(linked), "linked": linked}
