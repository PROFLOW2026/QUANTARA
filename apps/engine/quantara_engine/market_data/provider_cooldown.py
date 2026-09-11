"""Transient provider failure cooldown — avoids retry storms after timeout/5xx."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from quantara_engine.persistence.store import TradingStore

DEFAULT_COOLDOWN = timedelta(minutes=15)
SETTINGS_PREFIX = "provider_cooldown:"


def _settings_key(provider: str) -> str:
    return f"{SETTINGS_PREFIX}{provider.strip().lower()}"


def mark_cooldown(
    store: TradingStore | None,
    provider: str,
    *,
    reason: str,
    duration: timedelta = DEFAULT_COOLDOWN,
) -> None:
    if store is None:
        return
    until = datetime.now(timezone.utc) + duration
    store.update_settings(
        _settings_key(provider),
        {"until": until.isoformat(), "reason": reason[:500]},
        description=f"Provider cooldown for {provider}",
    )


def clear_cooldown(store: TradingStore | None, provider: str) -> None:
    if store is None:
        return
    store.update_settings(_settings_key(provider), None)


def cooldown_payload(store: TradingStore | None, provider: str) -> dict[str, Any] | None:
    if store is None:
        return None
    raw = store.get_settings_dict().get(_settings_key(provider))
    if not isinstance(raw, dict):
        return None
    until_raw = raw.get("until")
    if not until_raw:
        return None
    try:
        until = datetime.fromisoformat(str(until_raw))
        if until.tzinfo is None:
            until = until.replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    return {"until": until, "reason": raw.get("reason")}


def is_in_cooldown(store: TradingStore | None, provider: str, *, now: datetime | None = None) -> bool:
    payload = cooldown_payload(store, provider)
    if payload is None:
        return False
    now = now or datetime.now(timezone.utc)
    return payload["until"] > now.astimezone(timezone.utc)
