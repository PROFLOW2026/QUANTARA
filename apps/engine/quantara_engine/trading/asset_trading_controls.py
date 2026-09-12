"""Per-account, per-symbol trading pause — blocks new entries only."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

SETTINGS_KEY = "asset_trading_controls"

SCOPE_RESEARCH = "research"
SCOPE_LIVE_SIM = "live_sim"
VALID_SCOPES = frozenset({SCOPE_RESEARCH, SCOPE_LIVE_SIM})


def normalize_symbol(symbol: str) -> str:
    return symbol.upper().replace("/", "").replace("-", "").strip()


@dataclass
class AssetTradingControlsSnapshot:
    research: dict[str, bool] = field(default_factory=dict)
    live_sim: dict[str, bool] = field(default_factory=dict)
    updated_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "research": {normalize_symbol(k): bool(v) for k, v in self.research.items()},
            "live_sim": {normalize_symbol(k): bool(v) for k, v in self.live_sim.items()},
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> AssetTradingControlsSnapshot:
        if not raw:
            return cls()
        research = {
            normalize_symbol(k): bool(v)
            for k, v in (raw.get("research") or {}).items()
            if normalize_symbol(k)
        }
        live_sim = {
            normalize_symbol(k): bool(v)
            for k, v in (raw.get("live_sim") or {}).items()
            if normalize_symbol(k)
        }
        return cls(
            research=research,
            live_sim=live_sim,
            updated_at=raw.get("updated_at"),
        )

    def scope_map(self, scope: str) -> dict[str, bool]:
        if scope == SCOPE_LIVE_SIM:
            return self.live_sim
        return self.research

    def is_paused(self, scope: str, symbol: str) -> bool:
        sym = normalize_symbol(symbol)
        return bool(self.scope_map(scope).get(sym))


def load_asset_trading_controls(settings: dict[str, Any]) -> AssetTradingControlsSnapshot:
    raw = settings.get(SETTINGS_KEY)
    if isinstance(raw, str):
        try:
            import json

            raw = json.loads(raw)
        except json.JSONDecodeError:
            raw = None
    return AssetTradingControlsSnapshot.from_dict(raw if isinstance(raw, dict) else None)


def save_asset_trading_controls(store, snapshot: AssetTradingControlsSnapshot) -> None:
    snapshot.updated_at = datetime.now(timezone.utc).isoformat()
    store.update_settings(SETTINGS_KEY, snapshot.to_dict())


def set_asset_trading_paused(
    store,
    *,
    scope: str,
    symbol: str,
    paused: bool,
) -> AssetTradingControlsSnapshot:
    if scope not in VALID_SCOPES:
        raise ValueError(f"invalid scope: {scope}")
    sym = normalize_symbol(symbol)
    settings = store.get_settings_dict()
    snap = load_asset_trading_controls(settings)
    target = snap.scope_map(scope)
    if paused:
        target[sym] = True
    elif sym in target:
        del target[sym]
    save_asset_trading_controls(store, snap)
    return snap


def allows_entries_for_symbol(
    settings: dict[str, Any],
    *,
    scope: str,
    symbol: str,
) -> bool:
    snap = load_asset_trading_controls(settings)
    return not snap.is_paused(scope, symbol)


def list_paused_symbols(settings: dict[str, Any], scope: str) -> list[str]:
    snap = load_asset_trading_controls(settings)
    return sorted(sym for sym, paused in snap.scope_map(scope).items() if paused)
