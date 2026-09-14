"""Live Sim integrity containment — block new entries without stopping exits/protection."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

SETTINGS_KEY = "live_sim_integrity_containment"


def load_containment(settings: dict[str, Any]) -> dict[str, Any]:
    raw = settings.get(SETTINGS_KEY)
    return dict(raw) if isinstance(raw, dict) else {}


def is_live_sim_entries_blocked(store) -> bool:
    """True when new Live Sim entries must not be created."""
    return bool(load_containment(store.get_settings_dict()).get("block_new_entries"))


def activate_live_sim_entry_containment(
    store,
    *,
    reason: str,
    activated_by: str = "system",
) -> dict[str, Any]:
    """Block new Live Sim entries; protection, exits, and reconciliation continue."""
    payload = {
        "block_new_entries": True,
        "reason": reason,
        "activated_at": datetime.now(timezone.utc).isoformat(),
        "activated_by": activated_by,
    }
    store.update_settings(
        SETTINGS_KEY,
        payload,
        description="Live Sim integrity containment — entries blocked, exits/protection active",
    )
    return payload


def release_live_sim_entry_containment(store) -> dict[str, Any]:
    """Re-enable new Live Sim entries after repair verification."""
    payload = {
        "block_new_entries": False,
        "released_at": datetime.now(timezone.utc).isoformat(),
    }
    store.update_settings(
        SETTINGS_KEY,
        payload,
        description="Live Sim integrity containment released",
    )
    return payload
