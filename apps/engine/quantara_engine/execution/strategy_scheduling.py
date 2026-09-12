"""Fair round-robin cursors for live strategy scheduling."""

from __future__ import annotations

STRATEGY_SCHEDULING_CURSORS_KEY = "strategy_scheduling_cursors"
ROBOT_A_LIVE_CURSOR_KEY = "robot_a_live"
ROBOT_B_ORB_LIVE_CURSOR_KEY = "robot_b_orb"
ROBOT_CDE_LIVE_CURSOR_KEY = "robot_cde_live"


def load_scheduling_cursor(store, key: str) -> int:
    raw = (store.get_settings_dict().get(STRATEGY_SCHEDULING_CURSORS_KEY) or {}).get(key, 0)
    try:
        return max(0, int(raw))
    except (TypeError, ValueError):
        return 0


def save_scheduling_cursor(
    store,
    key: str,
    index: int,
    *,
    cursors: dict | None = None,
    flush: bool = True,
) -> None:
    target = dict(
        cursors
        if cursors is not None
        else (store.get_settings_dict().get(STRATEGY_SCHEDULING_CURSORS_KEY) or {})
    )
    target[key] = int(index)
    if cursors is None or flush:
        store.update_settings(STRATEGY_SCHEDULING_CURSORS_KEY, target, flush=flush)


def rotated_indices(length: int, start_index: int) -> list[int]:
    if length <= 0:
        return []
    start = start_index % length
    return [(start + offset) % length for offset in range(length)]
