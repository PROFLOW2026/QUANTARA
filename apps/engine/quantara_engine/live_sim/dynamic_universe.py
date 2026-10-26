"""Dynamic Live Sim trading universe — scan-driven slot rotation with open-position safety."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text

from quantara_engine.broker.vendor import BrokerVendor
from quantara_engine.live_sim.market_scan import rank_active_universe
from quantara_engine.market_data.active_universe import list_active_db_symbols
from quantara_engine.market_data.polling import STRATEGY_MIN_CANDLES
from quantara_engine.market_data.registry import get_asset
from quantara_engine.market_data.strategy_freshness_health import is_strategy_candle_eligible
from quantara_engine.market_data.sessions import session_allows_entries
from quantara_engine.live_sim.asset_gate_settings import reset_asset_gate_baselines_to_current_equity
from quantara_engine.live_sim.candidate_data_prep import ensure_rotation_candidate_data
from quantara_engine.owner_portfolio.constants import LIVE_SIM_EQUAL_ASSET_DEFINITIONS
from quantara_engine.owner_portfolio.service import LIVE_SIM_OWNER_SLUG
from quantara_engine.persistence.store import TradingStore

# Always eligible for new entries when data is fresh (gold + core FX/crypto).
PINNED_SYMBOLS: frozenset[str] = frozenset({"XAUUSD", "GBPJPY", "BTCUSD", "ETHUSD"})

# IBKR equity slots that may rotate to scan winners (same $1,250 envelope — no extra capital).
ROTATABLE_IBKR_SLOTS: frozenset[str] = frozenset({"NVDA", "TSLA", "AMD", "COIN"})

MAX_SCAN_ROTATIONS_PER_CYCLE = 2
MIN_SCAN_SCORE = 8.0


@dataclass(frozen=True)
class DynamicUniverseVerdict:
    allowed: bool
    reason: str | None = None
    layer: str = "dynamic_universe"


def _sym(symbol: str) -> str:
    return symbol.upper().replace("/", "")


def _symbols_with_open_live_sim_positions(store: TradingStore) -> set[str]:
    rows = store.session.execute(
        text(
            """
            SELECT DISTINCT upper(replace(i.symbol, '/', '')) AS sym
            FROM live_sim_positions p
            JOIN instruments i ON i.id = p.instrument_id
            WHERE p.status = 'open'
            """
        )
    ).scalars().all()
    return {str(s) for s in rows}


def _allocation_rows(store: TradingStore) -> list[dict[str, Any]]:
    return [
        dict(r)
        for r in store.session.execute(
            text(
                """
                SELECT a.id::text, a.canonical_symbol, a.trade_count, a.gross_exposure,
                       a.live_sim_new_entries_allowed, a.enabled, a.broker_account_id::text
                FROM owner_portfolio_asset_allocations a
                JOIN owner_trading_portfolios otp ON otp.id = a.owner_portfolio_id
                WHERE otp.slug = :slug
                """
            ),
            {"slug": LIVE_SIM_OWNER_SLUG},
        ).mappings().all()
    ]


def _latest_snapshot(store: TradingStore) -> dict[str, Any] | None:
    row = store.session.execute(
        text(
            """
            SELECT active_symbols, pinned_symbols, manage_only_symbols, generated_at
            FROM live_sim_dynamic_universe_snapshots s
            JOIN owner_trading_portfolios otp ON otp.id = s.owner_portfolio_id
            WHERE otp.slug = :slug
            ORDER BY s.generated_at DESC
            LIMIT 1
            """
        ),
        {"slug": LIVE_SIM_OWNER_SLUG},
    ).mappings().first()
    return dict(row) if row else None


def list_live_sim_active_trading_symbols(store: TradingStore) -> set[str]:
    """Symbols allowed for new Live Sim entries (plus manage-only handled separately)."""
    snap = _latest_snapshot(store)
    if snap:
        active = {_sym(s) for s in (snap.get("active_symbols") or [])}
    else:
        active = {_sym(s) for s in LIVE_SIM_EQUAL_ASSET_DEFINITIONS}
    return active


def list_live_sim_manage_symbols(store: TradingStore) -> set[str]:
    """Open positions must stay managed even if not in active scan set."""
    return _symbols_with_open_live_sim_positions(store)


def symbol_has_fresh_strategy_data(store: TradingStore, symbol: str, *, now: datetime | None = None) -> bool:
    """Strict freshness for **live entries** (not rotation)."""
    now = now or datetime.now(timezone.utc)
    sym = _sym(symbol)
    asset = get_asset(sym)
    if not asset:
        return False
    inst = store.get_instrument_by_symbol(sym)
    if not inst:
        return False
    last = store.latest_candle_timestamp(inst.id, "5m")
    if not last:
        return False
    eligible, _ = is_strategy_candle_eligible(asset, last, now, timeframe="5m")
    return eligible


def symbol_strategy_ready_for_rotation(store: TradingStore, symbol: str, *, now: datetime | None = None) -> bool:
    """
    Rotation readiness: enough history to evaluate strategies.

    Does not require a new 5m bar after the cash session closes — that gate applies only to live entries.
    """
    now = now or datetime.now(timezone.utc)
    sym = _sym(symbol)
    asset = get_asset(sym)
    if not asset:
        return False
    inst = store.get_instrument_by_symbol(sym)
    if not inst:
        return False
    if store.count_candles(inst.id, "5m") < STRATEGY_MIN_CANDLES:
        return False
    last = store.latest_candle_timestamp(inst.id, "5m")
    if not last:
        return False
    if session_allows_entries(asset.trading_sessions, now):
        eligible, _ = is_strategy_candle_eligible(asset, last, now, timeframe="5m")
        return eligible
    return True


def _symbol_has_blocking_live_sim_activity(store: TradingStore, symbol: str) -> bool:
    """Block slot rotation when capital, risk, or execution is still reserved."""
    sym = _sym(symbol)
    pending_alloc = store.session.execute(
        text(
            """
            SELECT 1
            FROM live_sim_allocation_log l
            JOIN broker_accounts ba ON ba.id = l.broker_account_id
            WHERE upper(replace(l.symbol, '/', '')) = :sym
              AND ba.slug LIKE 'live-sim-%'
              AND (
                (l.accepted AND l.broker_order_id IS NULL AND l.live_sim_position_id IS NULL)
                OR COALESCE(l.metadata->>'pending_execution', 'false') = 'true'
              )
            LIMIT 1
            """
        ),
        {"sym": sym},
    ).first()
    if pending_alloc:
        return True
    pending_broker = store.session.execute(
        text(
            """
            SELECT 1
            FROM broker_orders o
            JOIN instruments i ON i.id = o.instrument_id
            JOIN broker_accounts ba ON ba.id = o.broker_account_id
            WHERE upper(replace(i.symbol, '/', '')) = :sym
              AND ba.slug LIKE 'live-sim-%'
              AND o.status IN (
                'created', 'validating', 'accepted', 'submitted', 'partially_filled'
              )
            LIMIT 1
            """
        ),
        {"sym": sym},
    ).first()
    if pending_broker:
        return True
    row = store.session.execute(
        text(
            """
            SELECT open_sl_risk_usd, gross_exposure
            FROM owner_portfolio_asset_allocations a
            JOIN owner_trading_portfolios otp ON otp.id = a.owner_portfolio_id
            WHERE otp.slug = :slug AND a.canonical_symbol = :sym
            """
        ),
        {"slug": LIVE_SIM_OWNER_SLUG, "sym": sym},
    ).mappings().first()
    if row:
        if float(row.get("open_sl_risk_usd") or 0) > 0:
            return True
        if float(row.get("gross_exposure") or 0) > 0:
            return True
    return False


def evaluate_dynamic_universe_entry(
    store: TradingStore,
    *,
    symbol: str,
    is_close: bool = False,
) -> DynamicUniverseVerdict:
    sym = _sym(symbol)
    if is_close or sym in list_live_sim_manage_symbols(store):
        return DynamicUniverseVerdict(allowed=True)

    if sym in PINNED_SYMBOLS:
        if not symbol_has_fresh_strategy_data(store, sym):
            return DynamicUniverseVerdict(allowed=False, reason="DATA_NOT_FRESH")
        return DynamicUniverseVerdict(allowed=True)

    active = list_live_sim_active_trading_symbols(store)
    if sym not in active:
        return DynamicUniverseVerdict(allowed=False, reason="DYNAMIC_UNIVERSE_INACTIVE")

    row = store.session.execute(
        text(
            """
            SELECT live_sim_new_entries_allowed
            FROM owner_portfolio_asset_allocations a
            JOIN owner_trading_portfolios otp ON otp.id = a.owner_portfolio_id
            WHERE otp.slug = :slug AND a.canonical_symbol = :sym
            """
        ),
        {"slug": LIVE_SIM_OWNER_SLUG, "sym": sym},
    ).scalar()
    if row is False:
        return DynamicUniverseVerdict(allowed=False, reason="asset_new_entries_paused")

    if not symbol_has_fresh_strategy_data(store, sym):
        return DynamicUniverseVerdict(allowed=False, reason="DATA_NOT_FRESH")

    return DynamicUniverseVerdict(allowed=True)


def _symbol_has_global_broker_routing(store: TradingStore, symbol: str) -> bool:
    sym = _sym(symbol)
    row = store.session.execute(
        text(
            """
            SELECT 1
            FROM broker_routing_rules
            WHERE enabled = TRUE
              AND owner_portfolio_id IS NULL
              AND canonical_symbol = :sym
            LIMIT 1
            """
        ),
        {"sym": sym},
    ).first()
    return bool(row)


def _vendor_for_symbol(symbol: str) -> BrokerVendor:
    spec = LIVE_SIM_EQUAL_ASSET_DEFINITIONS.get(_sym(symbol))
    if spec:
        return spec[0]
    asset = get_asset(_sym(symbol))
    if asset and asset.asset_class.value == "crypto":
        return BrokerVendor.KRAKEN
    return BrokerVendor.IBKR


def _idle_rotatable_slots(store: TradingStore) -> list[dict[str, Any]]:
    open_syms = _symbols_with_open_live_sim_positions(store)
    out: list[dict[str, Any]] = []
    for row in _allocation_rows(store):
        sym = _sym(str(row["canonical_symbol"]))
        if sym in PINNED_SYMBOLS or sym in open_syms:
            continue
        if _vendor_for_symbol(sym) != BrokerVendor.IBKR:
            continue
        if _symbol_has_blocking_live_sim_activity(store, sym):
            continue
        out.append(row)
    return out


def sync_dynamic_live_sim_universe(store: TradingStore) -> dict[str, Any]:
    """
    Rotate idle IBKR slots toward scan leaders; persist snapshot (not settings-only).
    """
    portfolio = store.session.execute(
        text("SELECT id::text FROM owner_trading_portfolios WHERE slug = :slug"),
        {"slug": LIVE_SIM_OWNER_SLUG},
    ).scalar()
    if not portfolio:
        return {"ok": False, "error": "portfolio_missing"}

    raw_ranked = [r for r in rank_active_universe(store, limit=20) if r.get("score", 0) >= MIN_SCAN_SCORE]
    ibkr_candidates = [
        _sym(str(r["symbol"]))
        for r in raw_ranked
        if _sym(str(r["symbol"])) not in PINNED_SYMBOLS
        and _vendor_for_symbol(str(r["symbol"])) == BrokerVendor.IBKR
        and _sym(str(r["symbol"])) in list_active_db_symbols()
    ]
    prep_report = ensure_rotation_candidate_data(store, ibkr_candidates)
    ranked = [r for r in raw_ranked if symbol_strategy_ready_for_rotation(store, str(r["symbol"]))]
    scan_syms = [_sym(str(r["symbol"])) for r in ranked]

    current_rows = _allocation_rows(store)
    current_syms = {_sym(str(r["canonical_symbol"])) for r in current_rows}
    open_syms = _symbols_with_open_live_sim_positions(store)

    rotations: list[dict[str, str]] = []
    idle_slots = _idle_rotatable_slots(store)
    _slot_order = ("COIN", "AMD", "TSLA", "NVDA")
    idle_slots.sort(
        key=lambda r: _slot_order.index(_sym(str(r["canonical_symbol"])))
        if _sym(str(r["canonical_symbol"])) in _slot_order
        else 99
    )
    picks = [
        s
        for s in scan_syms
        if s not in PINNED_SYMBOLS
        and s not in current_syms
        and _vendor_for_symbol(s) == BrokerVendor.IBKR
        and s in list_active_db_symbols()
        and _symbol_has_global_broker_routing(store, s)
    ]

    for slot in idle_slots[:MAX_SCAN_ROTATIONS_PER_CYCLE]:
        if not picks:
            break
        target = picks.pop(0)
        old = _sym(str(slot["canonical_symbol"]))
        if old == target:
            continue
        label = LIVE_SIM_EQUAL_ASSET_DEFINITIONS.get(target, (BrokerVendor.IBKR, target))[1]
        store.session.execute(
            text(
                """
                UPDATE owner_portfolio_asset_allocations
                SET canonical_symbol = :new_sym,
                    label_he = :label,
                    live_sim_new_entries_allowed = TRUE,
                    updated_at = NOW()
                WHERE id = CAST(:id AS uuid)
                """
            ),
            {"id": slot["id"], "new_sym": target, "label": label},
        )
        reset_asset_gate_baselines_to_current_equity(store, str(slot["id"]))
        rotations.append({"from": old, "to": target, "slot_id": slot["id"]})
        current_syms.discard(old)
        current_syms.add(target)

    active: set[str] = set(PINNED_SYMBOLS)
    active.update(current_syms)
    manage_only = open_syms - active

    current_rows = _allocation_rows(store)
    for row in current_rows:
        sym = _sym(str(row["canonical_symbol"]))
        allow_new = sym in active and sym not in open_syms
        store.session.execute(
            text(
                """
                UPDATE owner_portfolio_asset_allocations
                SET live_sim_new_entries_allowed = :allow,
                    updated_at = NOW()
                WHERE id = CAST(:id AS uuid)
                """
            ),
            {"id": row["id"], "allow": allow_new if sym not in open_syms else False},
        )

    for sym in open_syms:
        active.add(sym)

    payload = {
        "active_symbols": sorted(active),
        "pinned_symbols": sorted(PINNED_SYMBOLS),
        "manage_only_symbols": sorted(manage_only),
        "rotated_slots": rotations,
        "scan_scores": ranked[:12],
    }
    store.session.execute(
        text(
            """
            INSERT INTO live_sim_dynamic_universe_snapshots (
              owner_portfolio_id, active_symbols, pinned_symbols,
              manage_only_symbols, rotated_slots, scan_scores
            ) VALUES (
              CAST(:pid AS uuid),
              CAST(:active AS jsonb),
              CAST(:pinned AS jsonb),
              CAST(:manage AS jsonb),
              CAST(:rot AS jsonb),
              CAST(:scores AS jsonb)
            )
            """
        ),
        {
            "pid": portfolio,
            "active": json.dumps(payload["active_symbols"]),
            "pinned": json.dumps(payload["pinned_symbols"]),
            "manage": json.dumps(payload["manage_only_symbols"]),
            "rot": json.dumps(rotations),
            "scores": json.dumps(ranked[:12]),
        },
    )

    from quantara_engine.live_sim.phase3_instance_sync import sync_phase3_live_sim_instances

    phase3_sync = sync_phase3_live_sim_instances(store)

    return {
        "ok": True,
        **payload,
        "rotations_applied": len(rotations),
        "candidate_data_prep": prep_report,
        "rotation_ready_count": len(ranked),
        "phase3_instance_sync": phase3_sync,
    }
