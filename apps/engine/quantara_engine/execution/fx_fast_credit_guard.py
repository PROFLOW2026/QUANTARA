"""Twelve Data credit budget and quota modes for FX 1m fast monitoring."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from quantara_engine.market_data.credits import (
    DAILY_HARD_LIMIT,
    INTERNAL_GUARD_LIMIT,
    FetchPriority,
    can_fetch,
    safe_used_today,
    status_payload,
)
from quantara_engine.persistence.store import TradingStore

# Reserve credits for scheduled 5m XAU/GBPJPY ingestion + health sync + gap recovery.
SCHEDULED_INGEST_RESERVE = 200
FAST_FX_CREDITS_PER_FETCH = 1
# Prefer normal-day TD burn << internal guard (target ~400–450 used).
CONSERVATION_USED_FLOOR = 520  # leave ≥200 under guard for OPEN_POSITION + reserve
EXHAUSTED_USED_FLOOR = INTERNAL_GUARD_LIMIT

QuotaMode = Literal["NORMAL", "CONSERVATION", "FALLBACK", "EXHAUSTED"]


def _used_today(store: TradingStore) -> int:
    """Safety authority: max(local ledger, provider api_usage)."""
    return safe_used_today(store)


def remaining_fast_fx_budget(store: TradingStore) -> int:
    """Credits available for fast FX 1m without breaching internal guard."""
    used = _used_today(store)
    ceiling = INTERNAL_GUARD_LIMIT - SCHEDULED_INGEST_RESERVE
    return max(0, ceiling - used)


def quota_mode(store: TradingStore, *, tiingo_primary_ok: bool = True) -> QuotaMode:
    """
    Protection quota mode (OPEN POSITION protection prioritized).

    NORMAL       — Tiingo 1m primary; TD REST not used for heartbeat
    FALLBACK     — Tiingo unavailable; TD used sparsely
    CONSERVATION — TD budget tight; TD only near-SL / no nonessential
    EXHAUSTED    — at/above internal guard; 5m fail-safe only for TD path
    """
    used = _used_today(store)
    if used >= EXHAUSTED_USED_FLOOR:
        return "EXHAUSTED"
    if used >= CONSERVATION_USED_FLOOR or remaining_fast_fx_budget(store) <= 0:
        return "CONSERVATION"
    if not tiingo_primary_ok:
        return "FALLBACK"
    return "NORMAL"


def can_run_fast_fx_fetch(store: TradingStore) -> bool:
    """True when any fast FX path may still protect (Tiingo or TD or stored 1m).

    Name kept for callers; 5m PM is skipped while forex session is open and
    this returns True. When EXHAUSTED, return False so 5m fail-safe runs.
    """
    mode = quota_mode(store)
    if mode == "EXHAUSTED":
        return False
    if mode == "CONSERVATION":
        # Still prefer skipping 5m when we may fetch TD near-SL or reuse stored 1m.
        return True
    return True


def can_fetch_twelve_data_1m(store: TradingStore) -> bool:
    if remaining_fast_fx_budget(store) < FAST_FX_CREDITS_PER_FETCH:
        return False
    if not can_fetch(store, FetchPriority.OPEN_POSITION):
        return False
    mode = quota_mode(store)
    return mode != "EXHAUSTED"


def select_fx_symbols_this_cycle(
    open_symbols: list[str],
    *,
    now: datetime | None = None,
) -> list[str]:
    """
    Legacy alternate helper — retained for tests.

    New protection path plans per-symbol independently (Tiingo primary);
    TD fallback may still alternate only when explicitly requested.
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
    payload = status_payload(store)
    mode = quota_mode(store)
    return {
        "used_today": used,
        "ledger_used": int(payload.get("ledger_used") or 0),
        "provider_daily_usage": payload.get("provider_daily_usage"),
        "internal_guard": INTERNAL_GUARD_LIMIT,
        "hard_limit": DAILY_HARD_LIMIT,
        "scheduled_reserve": SCHEDULED_INGEST_RESERVE,
        "remaining_fast_budget": remaining,
        "guard_remaining": max(0, INTERNAL_GUARD_LIMIT - used),
        "quota_mode": mode,
        "can_fetch": can_fetch_twelve_data_1m(store),
        "can_run_fast_fx": can_run_fast_fx_fetch(store),
    }
