"""Twelve Data credit budget for position-only FX 1m fast monitoring."""

from __future__ import annotations

from datetime import datetime, timezone

from quantara_engine.market_data.credits import (
    DAILY_HARD_LIMIT,
    INTERNAL_GUARD_LIMIT,
    FetchPriority,
    can_fetch,
    status_payload,
)
from quantara_engine.persistence.store import TradingStore

# Reserve credits for scheduled 5m XAU/GBPJPY ingestion + health sync.
SCHEDULED_INGEST_RESERVE = 200
FAST_FX_CREDITS_PER_FETCH = 1


def _used_today(store: TradingStore) -> int:
    payload = status_payload(store)
    return int(payload.get("used_today") or payload.get("ledger_used") or 0)


def remaining_fast_fx_budget(store: TradingStore) -> int:
    """Credits available for fast FX 1m without breaching internal guard."""
    used = _used_today(store)
    ceiling = INTERNAL_GUARD_LIMIT - SCHEDULED_INGEST_RESERVE
    return max(0, ceiling - used)


def can_run_fast_fx_fetch(store: TradingStore) -> bool:
    if remaining_fast_fx_budget(store) < FAST_FX_CREDITS_PER_FETCH:
        return False
    return can_fetch(store, FetchPriority.OPEN_POSITION)


def select_fx_symbols_this_cycle(
    open_symbols: list[str],
    *,
    now: datetime | None = None,
) -> list[str]:
    """
    When both XAU and GBPJPY need fast monitoring, alternate by minute
    so we never burn 2 credits every minute by default.
    """
    if not open_symbols:
        return []
    ordered = sorted({s.upper() for s in open_symbols})
    if len(ordered) == 1:
        return ordered
    now = now or datetime.now(timezone.utc)
    idx = now.minute % len(ordered)
    return [ordered[idx]]


def fx_fast_budget_report(store: TradingStore) -> dict:
    used = _used_today(store)
    remaining = remaining_fast_fx_budget(store)
    return {
        "used_today": used,
        "internal_guard": INTERNAL_GUARD_LIMIT,
        "hard_limit": DAILY_HARD_LIMIT,
        "scheduled_reserve": SCHEDULED_INGEST_RESERVE,
        "remaining_fast_budget": remaining,
        "can_fetch": can_run_fast_fx_fetch(store),
    }
