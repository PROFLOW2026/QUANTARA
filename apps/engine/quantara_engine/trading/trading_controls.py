"""Owner trading control state — logical pause/resume without killing workers."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

SETTINGS_KEY = "trading_control_state"


class TradingControlState(str, Enum):
    RUNNING = "running"
    PAUSE_NEW_ENTRIES = "pause_new_entries"
    PAUSE_TRADING = "pause_trading"
    FLATTENING = "flattening"
    STOPPED = "stopped"


@dataclass
class TradingControlSnapshot:
    state: TradingControlState = TradingControlState.RUNNING
    updated_at: str | None = None
    flatten_started_at: str | None = None
    open_positions_at_flatten: int = 0
    pending_market_reopen: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state.value,
            "updated_at": self.updated_at,
            "flatten_started_at": self.flatten_started_at,
            "open_positions_at_flatten": self.open_positions_at_flatten,
            "pending_market_reopen": list(self.pending_market_reopen),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> TradingControlSnapshot:
        if not raw:
            return cls()
        try:
            state = TradingControlState(str(raw.get("state", TradingControlState.RUNNING.value)))
        except ValueError:
            state = TradingControlState.RUNNING
        return cls(
            state=state,
            updated_at=raw.get("updated_at"),
            flatten_started_at=raw.get("flatten_started_at"),
            open_positions_at_flatten=int(raw.get("open_positions_at_flatten") or 0),
            pending_market_reopen=list(raw.get("pending_market_reopen") or []),
            metadata=dict(raw.get("metadata") or {}),
        )


def load_trading_control(settings: dict[str, Any]) -> TradingControlSnapshot:
    raw = settings.get(SETTINGS_KEY)
    if isinstance(raw, str):
        try:
            import json

            raw = json.loads(raw)
        except json.JSONDecodeError:
            raw = None
    return TradingControlSnapshot.from_dict(raw if isinstance(raw, dict) else None)


def save_trading_control(store, snapshot: TradingControlSnapshot) -> None:
    snapshot.updated_at = datetime.now(timezone.utc).isoformat()
    store.update_settings(SETTINGS_KEY, snapshot.to_dict())


def allows_market_data(_: TradingControlSnapshot) -> bool:
    return True


def allows_strategy_evaluation(snapshot: TradingControlSnapshot) -> bool:
    return snapshot.state in (
        TradingControlState.RUNNING,
        TradingControlState.PAUSE_NEW_ENTRIES,
    )


def allows_new_entries(snapshot: TradingControlSnapshot) -> bool:
    return snapshot.state == TradingControlState.RUNNING


def allows_position_management(snapshot: TradingControlSnapshot) -> bool:
    return snapshot.state in (
        TradingControlState.RUNNING,
        TradingControlState.PAUSE_NEW_ENTRIES,
        TradingControlState.PAUSE_TRADING,
        TradingControlState.FLATTENING,
        TradingControlState.STOPPED,
    )


def requires_flatten(snapshot: TradingControlSnapshot) -> bool:
    return snapshot.state == TradingControlState.FLATTENING


def _expire_pending_entries(store) -> int:
    from quantara_engine.competition.constants import ACTIVE_COMPETITION_EXPERIMENT_ID
    from quantara_engine.competition.orb_constants import ORB_COMPETITION_EXPERIMENT_ID

    now = datetime.now(timezone.utc)
    expired = store.cancel_stale_pending_intents(ACTIVE_COMPETITION_EXPERIMENT_ID, now)
    expired += store.cancel_stale_pending_intents(ORB_COMPETITION_EXPERIMENT_ID, now)
    return expired


def transition_to(
    store,
    new_state: TradingControlState,
    *,
    open_positions: int = 0,
    pending_market_reopen: list[str] | None = None,
) -> TradingControlSnapshot:
    settings = store.get_settings_dict()
    current = load_trading_control(settings)
    now = datetime.now(timezone.utc).isoformat()

    if new_state in (
        TradingControlState.PAUSE_NEW_ENTRIES,
        TradingControlState.PAUSE_TRADING,
        TradingControlState.FLATTENING,
        TradingControlState.STOPPED,
    ):
        _expire_pending_entries(store)

    snap = TradingControlSnapshot(
        state=new_state,
        updated_at=now,
        flatten_started_at=(
            now
            if new_state == TradingControlState.FLATTENING
            else (current.flatten_started_at if new_state == TradingControlState.STOPPED else None)
        ),
        open_positions_at_flatten=(
            open_positions if new_state == TradingControlState.FLATTENING else current.open_positions_at_flatten
        ),
        pending_market_reopen=(
            list(pending_market_reopen or [])
            if new_state == TradingControlState.FLATTENING
            else (current.pending_market_reopen if new_state == TradingControlState.STOPPED else [])
        ),
        metadata=dict(current.metadata),
    )

    if new_state == TradingControlState.RUNNING:
        snap.flatten_started_at = None
        snap.open_positions_at_flatten = 0
        snap.pending_market_reopen = []

    save_trading_control(store, snap)
    return snap
