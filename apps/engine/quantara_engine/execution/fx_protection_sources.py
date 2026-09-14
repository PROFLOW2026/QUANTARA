"""Quota-safe FX protection source selection for XAUUSD / GBPJPY.

Design (no FX websocket available in QUANTARA):
1. Tiingo FX 1m REST (throttled) — primary, low TD burn
2. Already-stored local 1m candles — reuse without provider call
3. Twelve Data 1m REST — emergency fallback only when stored is NOT fresh
4. Canonical 5m PM — fail-safe when fast path unavailable

Near-SL rule (defensible from existing trade risk):
  R = abs(entry_price - stop_loss)
  distance_to_sl = remaining distance from mark to SL (positive if not breached)
  near_sl ⇔ distance_to_sl <= 0.5 * R

Many open Research legs share ONE per-symbol fetch.

Cadence uses last_attempt_at (not only last success) so empty/failed
provider responses still suppress retries.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Literal

from quantara_engine.domain.types import Candle, Direction, Position
from quantara_engine.market_data.symbols import normalize_db_symbol
from quantara_engine.persistence.store import TradingStore

logger = logging.getLogger(__name__)

ProtectionSource = Literal[
    "stored_1m",
    "tiingo_1m",
    "twelve_data_1m",
    "5m_fallback",
    "none",
]

QuotaMode = Literal["NORMAL", "CONSERVATION", "FALLBACK", "EXHAUSTED"]

LAST_FETCH_SETTINGS_KEY = "fx_protection:provider_fetch_state"
PROTECTION_STATUS_KEY = "fx_protection:last_source"

# One Tiingo 1m request returns a lookback of completed minutes, so we do not
# need a per-minute heartbeat. 5m cadence → ~24 Tiingo req/hour for 2 symbols.
TIINGO_1M_MIN_INTERVAL = timedelta(minutes=5)
# Twelve Data REST emergency only (stale stored + Tiingo unavailable).
TD_FAR_MIN_INTERVAL = timedelta(minutes=5)
TD_NEAR_MIN_INTERVAL = timedelta(minutes=2)
# Stored 1m is "fresh" if latest completed bar started within this window.
STORED_1M_FRESHNESS = timedelta(minutes=3)
NEAR_SL_R_FRACTION = Decimal("0.5")


@dataclass(frozen=True)
class ProtectionFetchPlan:
    symbol: str
    source: ProtectionSource
    fetch_provider: str | None  # tiingo | twelvedata | None (use stored only)
    reason: str
    near_sl: bool


def _as_utc(ts: datetime) -> datetime:
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc)


def position_r_distance(position: Position) -> Decimal:
    return abs(Decimal(str(position.entry_price)) - Decimal(str(position.stop_loss)))


def distance_to_stop(position: Position, mark: Decimal) -> Decimal:
    """Positive distance remaining to SL; <=0 means at/through stop."""
    sl = Decimal(str(position.stop_loss))
    if position.direction == Direction.LONG or str(position.direction).lower() == "long":
        return mark - sl
    return sl - mark


def is_near_stop(
    position: Position,
    mark: Decimal,
    *,
    fraction: Decimal = NEAR_SL_R_FRACTION,
) -> bool:
    r = position_r_distance(position)
    if r <= 0:
        return True
    dist = distance_to_stop(position, mark)
    if dist <= 0:
        return True
    return dist <= (r * fraction)


def any_position_near_stop(
    positions: list[Position],
    mark: Decimal | None,
) -> bool:
    if mark is None or not positions:
        return False
    return any(is_near_stop(p, mark) for p in positions)


def _load_fetch_state(store: TradingStore) -> dict[str, Any]:
    raw = store.get_settings_dict().get(LAST_FETCH_SETTINGS_KEY) or {}
    return dict(raw) if isinstance(raw, dict) else {}


def _save_fetch_state(store: TradingStore, state: dict[str, Any]) -> None:
    store.update_settings(
        LAST_FETCH_SETTINGS_KEY,
        state,
        description="FX protection last provider attempt/success timestamps",
        flush=False,
    )


def _entry_key(symbol: str, provider: str) -> str:
    return f"{normalize_db_symbol(symbol)}:{provider}"


def _parse_entry(raw: Any) -> dict[str, str | None]:
    """Normalize legacy string timestamps and new attempt/success dicts."""
    if raw is None:
        return {"last_attempt_at": None, "last_success_at": None}
    if isinstance(raw, str):
        # Legacy: only success was recorded — treat as both for throttle safety.
        return {"last_attempt_at": raw, "last_success_at": raw}
    if isinstance(raw, dict):
        return {
            "last_attempt_at": raw.get("last_attempt_at") or raw.get("at"),
            "last_success_at": raw.get("last_success_at") or raw.get("at"),
        }
    return {"last_attempt_at": None, "last_success_at": None}


def _parse_ts(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        ts = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        return _as_utc(ts)
    except ValueError:
        return None


def last_provider_attempt_at(
    store: TradingStore,
    symbol: str,
    provider: str,
) -> datetime | None:
    entry = _parse_entry(_load_fetch_state(store).get(_entry_key(symbol, provider)))
    return _parse_ts(entry.get("last_attempt_at") if isinstance(entry.get("last_attempt_at"), str) else None)


def last_provider_success_at(
    store: TradingStore,
    symbol: str,
    provider: str,
) -> datetime | None:
    entry = _parse_entry(_load_fetch_state(store).get(_entry_key(symbol, provider)))
    return _parse_ts(entry.get("last_success_at") if isinstance(entry.get("last_success_at"), str) else None)


def last_provider_fetch_at(
    store: TradingStore,
    symbol: str,
    provider: str,
) -> datetime | None:
    """Backward-compatible alias: cadence uses last attempt."""
    return last_provider_attempt_at(store, symbol, provider)


def mark_provider_attempted(
    store: TradingStore,
    symbol: str,
    provider: str,
    *,
    now: datetime | None = None,
) -> None:
    """Record that an HTTP provider request was made (success or failure)."""
    now = _as_utc(now or datetime.now(timezone.utc))
    state = _load_fetch_state(store)
    key = _entry_key(symbol, provider)
    entry = _parse_entry(state.get(key))
    entry["last_attempt_at"] = now.isoformat()
    state[key] = entry
    _save_fetch_state(store, state)


def mark_provider_success(
    store: TradingStore,
    symbol: str,
    provider: str,
    *,
    now: datetime | None = None,
) -> None:
    """Record a successful candle response (also refreshes attempt timestamp)."""
    now = _as_utc(now or datetime.now(timezone.utc))
    state = _load_fetch_state(store)
    key = _entry_key(symbol, provider)
    entry = _parse_entry(state.get(key))
    iso = now.isoformat()
    entry["last_attempt_at"] = iso
    entry["last_success_at"] = iso
    state[key] = entry
    _save_fetch_state(store, state)


def mark_provider_fetched(
    store: TradingStore,
    symbol: str,
    provider: str,
    *,
    now: datetime | None = None,
) -> None:
    """Backward-compatible success marker."""
    mark_provider_success(store, symbol, provider, now=now)


def _interval_elapsed(
    store: TradingStore,
    symbol: str,
    provider: str,
    minimum: timedelta,
    now: datetime,
) -> bool:
    last = last_provider_attempt_at(store, symbol, provider)
    if last is None:
        return True
    return (now - last) >= minimum


def latest_mark_from_candles(candles: list[Candle], now: datetime) -> Decimal | None:
    from quantara_engine.market_data.polling import is_bar_complete

    completed = [
        c
        for c in candles
        if c.is_complete or is_bar_complete(c.timestamp, c.timeframe, now)
    ]
    if not completed:
        return None
    completed.sort(key=lambda c: c.timestamp)
    return Decimal(str(completed[-1].close))


def record_protection_source(
    store: TradingStore,
    symbol: str,
    source: ProtectionSource,
    *,
    reason: str = "",
) -> None:
    """Persist last protection source per symbol for health UI (no provider credits)."""
    raw = store.get_settings_dict().get(PROTECTION_STATUS_KEY) or {}
    state = dict(raw) if isinstance(raw, dict) else {}
    sym = normalize_db_symbol(symbol)
    tiingo_success = last_provider_success_at(store, sym, "tiingo")
    td_attempt = last_provider_attempt_at(store, sym, "twelvedata")
    state[sym] = {
        "source": source,
        "reason": reason,
        "at": datetime.now(timezone.utc).isoformat(),
        "last_tiingo_success_at": tiingo_success.isoformat() if tiingo_success else None,
        "last_td_attempt_at": td_attempt.isoformat() if td_attempt else None,
    }
    store.update_settings(
        PROTECTION_STATUS_KEY,
        state,
        description="FX protection last source per symbol",
        flush=False,
    )


def protection_sources_snapshot(store: TradingStore) -> dict[str, Any]:
    raw = store.get_settings_dict().get(PROTECTION_STATUS_KEY) or {}
    return dict(raw) if isinstance(raw, dict) else {}


def plan_fx_protection_fetch(
    store: TradingStore,
    symbol: str,
    *,
    quota_mode: QuotaMode,
    tiingo_eligible: bool,
    td_eligible: bool,
    near_sl: bool,
    has_fresh_stored_1m: bool,
    now: datetime | None = None,
) -> ProtectionFetchPlan:
    """Decide provider call for one FX symbol this cycle (per-symbol, not per-position).

    Fresh stored 1m never triggers Twelve Data — even after a Tiingo miss —
    unless the stored series is no longer fresh (emergency path).
    """
    now = _as_utc(now or datetime.now(timezone.utc))
    sym = normalize_db_symbol(symbol)

    if quota_mode == "EXHAUSTED":
        return ProtectionFetchPlan(
            symbol=sym,
            source="stored_1m" if has_fresh_stored_1m else "5m_fallback",
            fetch_provider=None,
            reason="td_exhausted_use_stored_or_5m",
            near_sl=near_sl,
        )

    # Fresh completed 1m is sufficient for protection (FAR and NEAR).
    # Allow scheduled Tiingo refresh only; never TD while fresh.
    if has_fresh_stored_1m:
        if tiingo_eligible and _interval_elapsed(
            store, sym, "tiingo", TIINGO_1M_MIN_INTERVAL, now
        ):
            return ProtectionFetchPlan(
                symbol=sym,
                source="tiingo_1m",
                fetch_provider="tiingo",
                reason="scheduled_tiingo_refresh" if not near_sl else "near_sl_tiingo_refresh",
                near_sl=near_sl,
            )
        return ProtectionFetchPlan(
            symbol=sym,
            source="stored_1m",
            fetch_provider=None,
            reason="reuse_stored_1m",
            near_sl=near_sl,
        )

    # Stored is stale — prefer Tiingo.
    if tiingo_eligible and _interval_elapsed(
        store, sym, "tiingo", TIINGO_1M_MIN_INTERVAL, now
    ):
        return ProtectionFetchPlan(
            symbol=sym,
            source="tiingo_1m",
            fetch_provider="tiingo",
            reason="primary_tiingo_1m",
            near_sl=near_sl,
        )

    if tiingo_eligible:
        # Tiingo eligible but throttled — wait; do not pay TD yet.
        return ProtectionFetchPlan(
            symbol=sym,
            source="5m_fallback",
            fetch_provider=None,
            reason="tiingo_throttle_use_5m",
            near_sl=near_sl,
        )

    # Tiingo unavailable — Twelve Data emergency only when data is stale.
    if not td_eligible:
        return ProtectionFetchPlan(
            symbol=sym,
            source="5m_fallback",
            fetch_provider=None,
            reason="no_td_budget",
            near_sl=near_sl,
        )

    if quota_mode == "CONSERVATION" and not near_sl:
        return ProtectionFetchPlan(
            symbol=sym,
            source="5m_fallback",
            fetch_provider=None,
            reason="conservation_far_from_sl",
            near_sl=near_sl,
        )

    td_interval = TD_NEAR_MIN_INTERVAL if near_sl else TD_FAR_MIN_INTERVAL
    if quota_mode == "CONSERVATION":
        td_interval = TD_FAR_MIN_INTERVAL  # even when near, be thrifty
    if not _interval_elapsed(store, sym, "twelvedata", td_interval, now):
        return ProtectionFetchPlan(
            symbol=sym,
            source="5m_fallback",
            fetch_provider=None,
            reason="td_throttled",
            near_sl=near_sl,
        )

    return ProtectionFetchPlan(
        symbol=sym,
        source="twelve_data_1m",
        fetch_provider="twelvedata",
        reason="td_emergency_stale",
        near_sl=near_sl,
    )
