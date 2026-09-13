"""Finnhub LIVE_MARK for US equities — display/valuation only; Alpaca keeps protection."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from quantara_engine.execution.crypto_mark_valuation import (
    FAST_CANONICAL_MARKS_KEY,
    FAST_EQUITY_DB_SYMBOLS,
    apply_fast_1m_marks,
    is_fast_protection_equity,
    load_fast_canonical_marks,
)
from quantara_engine.market_data.adapters.finnhub import (
    FinnhubError,
    FinnhubMarketDataProvider,
    finnhub_configured,
)
from quantara_engine.market_data.provider_budgets import can_request
from quantara_engine.market_data.registry import get_asset, list_target_assets
from quantara_engine.market_data.sessions import is_us_equity_rth
from quantara_engine.market_data.symbols import normalize_db_symbol
from quantara_engine.persistence.store import TradingStore

logger = logging.getLogger(__name__)

FINNHUB_EQUITY_LIVE_MARK_STATE_KEY = "finnhub_equity_live_mark_state"
MAX_FINNHUB_QUOTE_AGE_SECONDS = 120
RECOVERY_HEALTHY_STREAK = 2
SOURCE_FINNHUB = "finnhub"
SOURCE_ALPACA = "alpaca"
SOURCE_ALPACA_WS = "alpaca_ws"


def _as_utc(ts: datetime) -> datetime:
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc)


def _parse_entry_at(entry: dict[str, Any], key: str = "at") -> datetime | None:
    raw = entry.get(key)
    if not raw:
        return None
    return _as_utc(datetime.fromisoformat(str(raw).replace("Z", "+00:00")))


def _load_state(store: TradingStore) -> dict[str, dict[str, Any]]:
    raw = store.get_settings_dict().get(FINNHUB_EQUITY_LIVE_MARK_STATE_KEY) or {}
    return dict(raw) if isinstance(raw, dict) else {}


def _save_state(store: TradingStore, state: dict[str, dict[str, Any]]) -> None:
    store.update_settings(
        FINNHUB_EQUITY_LIVE_MARK_STATE_KEY,
        state,
        description="Finnhub equity LIVE_MARK failover state",
        flush=False,
    )


def is_finnhub_quote_fresh(
    *,
    quote_at: datetime,
    received_at: datetime,
    now: datetime,
) -> bool:
    quote_at = _as_utc(quote_at)
    received_at = _as_utc(received_at)
    now = _as_utc(now)
    quote_age = (now - quote_at).total_seconds()
    receive_age = (now - received_at).total_seconds()
    return quote_age <= MAX_FINNHUB_QUOTE_AGE_SECONDS and receive_age <= MAX_FINNHUB_QUOTE_AGE_SECONDS


def apply_equity_ws_live_mark(
    store: TradingStore,
    symbol: str,
    price: Decimal,
    at: datetime,
) -> None:
    """Alpaca WebSocket trade as primary equity LIVE_MARK during RTH."""
    db_sym = normalize_db_symbol(symbol)
    if db_sym not in FAST_EQUITY_DB_SYMBOLS:
        return
    at = _as_utc(at)
    _apply_display_mark(store, db_sym, price, at, SOURCE_ALPACA_WS)
    stored = load_fast_canonical_marks(store)
    entry = dict(stored.get(db_sym) or {})
    entry["alpaca_price"] = str(price)
    entry["alpaca_at"] = at.isoformat()
    stored[db_sym] = entry
    store.update_settings(FAST_CANONICAL_MARKS_KEY, stored, flush=False)


def store_equity_alpaca_fallback_mark(
    store: TradingStore,
    symbol: str,
    price: Decimal,
    at: datetime,
    *,
    apply_if_no_finnhub: bool = True,
) -> None:
    """Persist Alpaca 1m close as protection fallback; optionally active display mark."""
    db_sym = normalize_db_symbol(symbol)
    if db_sym not in FAST_EQUITY_DB_SYMBOLS:
        return

    stored = load_fast_canonical_marks(store)
    entry = dict(stored.get(db_sym) or {})
    at = _as_utc(at)
    entry["alpaca_price"] = str(price)
    entry["alpaca_at"] = at.isoformat()

    active_source = entry.get("source")
    finnhub_active = active_source == SOURCE_FINNHUB and _finnhub_entry_still_fresh(entry, at)

    should_apply_display = apply_if_no_finnhub and not finnhub_active
    if should_apply_display:
        prev_at = _parse_entry_at(entry)
        if prev_at is None or at >= prev_at:
            entry["price"] = str(price)
            entry["at"] = at.isoformat()
            entry["source"] = SOURCE_ALPACA

    stored[db_sym] = entry
    store.update_settings(FAST_CANONICAL_MARKS_KEY, stored, flush=False)

    if should_apply_display:
        apply_fast_1m_marks(store, {db_sym: (price, at)}, flush=False)
        stored = load_fast_canonical_marks(store)
        merged = dict(stored.get(db_sym) or {})
        merged["source"] = SOURCE_ALPACA
        merged["alpaca_price"] = str(price)
        merged["alpaca_at"] = at.isoformat()
        stored[db_sym] = merged
        store.update_settings(FAST_CANONICAL_MARKS_KEY, stored, flush=False)


def _finnhub_entry_still_fresh(entry: dict[str, Any], now: datetime) -> bool:
    if entry.get("source") != SOURCE_FINNHUB:
        return False
    at = _parse_entry_at(entry)
    if at is None:
        return False
    return (now - at).total_seconds() <= MAX_FINNHUB_QUOTE_AGE_SECONDS


def resolve_equity_live_mark(
    store: TradingStore,
    symbol: str,
    *,
    now: datetime | None = None,
) -> tuple[Decimal, datetime, str] | None:
    """Resolved LIVE_MARK: Finnhub when fresh, else Alpaca fallback."""
    db_sym = normalize_db_symbol(symbol)
    if db_sym not in FAST_EQUITY_DB_SYMBOLS:
        return None

    now = _as_utc(now or datetime.now(timezone.utc))

    from quantara_engine.market_data.streaming.live_mark_read import (
        hub_mark_for_symbol,
        hub_mark_fresh,
    )

    hub_entry = hub_mark_for_symbol(db_sym)
    if hub_entry and hub_mark_fresh(hub_entry, now=now):
        return hub_entry.price, hub_entry.at, hub_entry.source

    entry = load_fast_canonical_marks(store).get(db_sym)
    if not entry:
        return None

    if entry.get("source") == SOURCE_ALPACA_WS and _finnhub_entry_still_fresh(entry, now):
        price_raw = entry.get("price")
        at = _parse_entry_at(entry)
        if price_raw and at:
            return Decimal(str(price_raw)), at, SOURCE_ALPACA_WS

    source = entry.get("source") or SOURCE_ALPACA
    if source == SOURCE_FINNHUB and _finnhub_entry_still_fresh(entry, now):
        price_raw = entry.get("price")
        at = _parse_entry_at(entry)
        if price_raw and at:
            return Decimal(str(price_raw)), at, SOURCE_FINNHUB

    alpaca_price = entry.get("alpaca_price") or entry.get("price")
    alpaca_at = _parse_entry_at(entry, "alpaca_at") or _parse_entry_at(entry)
    if alpaca_price and alpaca_at:
        return Decimal(str(alpaca_price)), alpaca_at, SOURCE_ALPACA

    price_raw = entry.get("price")
    at = _parse_entry_at(entry)
    if price_raw and at:
        return Decimal(str(price_raw)), at, str(source)
    return None


def _apply_display_mark(
    store: TradingStore,
    db_sym: str,
    price: Decimal,
    at: datetime,
    source: str,
) -> None:
    stored = load_fast_canonical_marks(store)
    prev = dict(stored.get(db_sym) or {})
    alpaca_price = prev.get("alpaca_price")
    alpaca_at = prev.get("alpaca_at")
    apply_fast_1m_marks(store, {db_sym: (price, at)}, flush=False)
    stored = load_fast_canonical_marks(store)
    entry = dict(stored.get(db_sym) or {})
    entry["source"] = source
    if alpaca_price:
        entry["alpaca_price"] = alpaca_price
    if alpaca_at:
        entry["alpaca_at"] = alpaca_at
    stored[db_sym] = entry
    store.update_settings(FAST_CANONICAL_MARKS_KEY, stored, flush=False)


def _fallback_to_alpaca(
    store: TradingStore,
    db_sym: str,
    entry: dict[str, Any],
    state: dict[str, dict[str, Any]],
    *,
    reason: str,
) -> bool:
    alpaca_price = entry.get("alpaca_price")
    alpaca_at = _parse_entry_at(entry, "alpaca_at")
    sym_state = dict(state.get(db_sym) or {})
    sym_state["using_finnhub"] = False
    sym_state["healthy_streak"] = 0
    sym_state["last_fallback_reason"] = reason
    state[db_sym] = sym_state

    if not alpaca_price or not alpaca_at:
        return False

    price = Decimal(str(alpaca_price))
    _apply_display_mark(store, db_sym, price, alpaca_at, SOURCE_ALPACA)
    logger.info("Equity LIVE_MARK fallback %s -> alpaca (%s)", db_sym, reason)
    return True


def run_finnhub_equity_live_marks(store: TradingStore, *, now: datetime | None = None) -> dict[str, Any]:
    """Fetch Finnhub quotes for US equities during RTH and apply LIVE_MARK when healthy."""
    now = _as_utc(now or datetime.now(timezone.utc))

    if not finnhub_configured():
        return {"status": "skipped", "reason": "finnhub_not_configured", "fetches": 0}
    if not is_us_equity_rth(now):
        return {"status": "skipped", "reason": "outside_us_rth", "fetches": 0}
    if not can_request(store, "finnhub", purpose="live_mark"):
        return {"status": "skipped", "reason": "finnhub_budget", "fetches": 0}

    provider = FinnhubMarketDataProvider(store=store, caller="finnhub_equity_live_mark")
    state = _load_state(store)
    applied: list[str] = []
    fallbacks: list[str] = []
    errors: list[str] = []
    fetches = 0

    for asset in list_target_assets():
        if asset.db_symbol not in FAST_EQUITY_DB_SYMBOLS:
            continue
        db_sym = asset.db_symbol
        sym_state = dict(state.get(db_sym) or {})
        stored = load_fast_canonical_marks(store)
        entry = dict(stored.get(db_sym) or {})

        try:
            obs = provider.fetch_quote(asset)
            fetches += 1
        except FinnhubError as exc:
            errors.append(f"{db_sym}:{exc}")
            if _fallback_to_alpaca(store, db_sym, entry, state, reason=str(exc)[:80]):
                fallbacks.append(db_sym)
            continue

        if obs is None or obs.price <= 0:
            errors.append(f"{db_sym}:empty_quote")
            if _fallback_to_alpaca(store, db_sym, entry, state, reason="empty_quote"):
                fallbacks.append(db_sym)
            continue

        fresh = is_finnhub_quote_fresh(
            quote_at=obs.timestamp,
            received_at=obs.received_at,
            now=now,
        )
        if not fresh:
            errors.append(f"{db_sym}:stale_quote")
            if _fallback_to_alpaca(store, db_sym, entry, state, reason="stale_quote"):
                fallbacks.append(db_sym)
            continue

        streak = int(sym_state.get("healthy_streak") or 0) + 1
        sym_state["healthy_streak"] = streak
        was_using = bool(sym_state.get("using_finnhub"))

        if not was_using and streak < RECOVERY_HEALTHY_STREAK:
            sym_state["using_finnhub"] = False
            state[db_sym] = sym_state
            continue

        sym_state["using_finnhub"] = True
        sym_state["last_finnhub_at"] = obs.timestamp.isoformat()
        state[db_sym] = sym_state

        _apply_display_mark(store, db_sym, obs.price, obs.timestamp, SOURCE_FINNHUB)
        applied.append(db_sym)

    _save_state(store, state)
    return {
        "status": "success",
        "fetches": fetches,
        "applied": applied,
        "fallbacks": fallbacks,
        "errors": errors,
    }


def equity_mark_providers(db_symbol: str) -> dict[str, str]:
    """Role-separated provider labels for API provenance."""
    db_sym = normalize_db_symbol(db_symbol)
    if db_sym in FAST_EQUITY_DB_SYMBOLS:
        return {
            "strategy_provider": "alpaca",
            "protection_provider": "alpaca",
            "live_mark_provider": "finnhub",
        }
    if db_sym in {"BTCUSD", "ETHUSD"}:
        return {
            "strategy_provider": "coinbase",
            "protection_provider": "coinbase",
            "live_mark_provider": "coinbase",
        }
    if db_sym in {"XAUUSD", "GBPJPY"}:
        return {
            "strategy_provider": "twelvedata",
            "protection_provider": "twelvedata",
            "live_mark_provider": "twelvedata",
        }
    asset = get_asset(db_sym)
    primary = asset.primary_provider.value if asset else "unknown"
    return {
        "strategy_provider": primary,
        "protection_provider": primary,
        "live_mark_provider": primary,
    }
