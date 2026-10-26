"""Keep Phase 3 strategy instances aligned with Dynamic Live Sim active symbols."""

from __future__ import annotations

import json
import uuid
from typing import Any

from sqlalchemy import text

from quantara_engine.broker.vendor import BrokerVendor
from quantara_engine.competition.constants import OWNER_ID
from quantara_engine.live_sim.phase3_constants import (
    EXPERIMENT_ID_BY_SLUG,
    PHASE3_STRATEGY_SLUGS,
)
from quantara_engine.persistence.store import TradingStore

PHASE3_LIVE_SIM_ENABLED_KEY = "phase3_live_sim_enabled"
PHASE3_TIMEFRAME = "5m"
# Phase 3 edge tests target IBKR names in the rotating book (not core crypto slots).
PHASE3_EXCLUDED_SYMBOLS: frozenset[str] = frozenset(
    {"BTCUSD", "ETHUSD", "XAUUSD", "GBPJPY"}
)


def _sym(symbol: str) -> str:
    return symbol.upper().replace("/", "")


def phase3_eligible_symbols(store: TradingStore) -> set[str]:
    from quantara_engine.live_sim.dynamic_universe import (
        _vendor_for_symbol,
        list_live_sim_active_trading_symbols,
    )

    active = list_live_sim_active_trading_symbols(store)
    out: set[str] = set()
    for sym in active:
        s = _sym(sym)
        if s in PHASE3_EXCLUDED_SYMBOLS:
            continue
        if _vendor_for_symbol(s) != BrokerVendor.IBKR:
            continue
        if not store.get_instrument_by_symbol(s):
            continue
        out.add(s)
    return out


def _instance_uuid(slug: str, symbol: str) -> uuid.UUID:
    return uuid.uuid5(uuid.NAMESPACE_DNS, f"quantara-phase3-live-sim:{slug}:{symbol}")


def _portfolio_uuid(slug: str, symbol: str) -> uuid.UUID:
    return uuid.uuid5(uuid.NAMESPACE_DNS, f"quantara-phase3-portfolio:{slug}:{symbol}")


def sync_phase3_live_sim_instances(store: TradingStore) -> dict[str, Any]:
    """
    Upsert Phase 3 paper instances for each (slug × active dynamic symbol).
    Deactivate instances for symbols that left the active universe.
    """
    eligible = phase3_eligible_symbols(store)
    report: dict[str, Any] = {
        "eligible_symbols": sorted(eligible),
        "activated_instances": 0,
        "deactivated_instances": 0,
        "errors": [],
    }

    risk_row = store.session.execute(
        text("SELECT id FROM risk_profiles WHERE slug = 'aggressive'")
    ).first()
    if not risk_row:
        report["errors"].append("aggressive risk profile missing")
        return report
    risk_id = risk_row[0]

    version_by_slug: dict[str, str] = {}
    for slug in PHASE3_STRATEGY_SLUGS:
        row = store.session.execute(
            text(
                """
                SELECT sv.id::text
                FROM strategy_versions sv
                JOIN strategies s ON s.id = sv.strategy_id
                WHERE s.slug = :slug AND sv.version = '1.0.0'
                """
            ),
            {"slug": slug},
        ).scalar()
        if not row:
            report["errors"].append(f"missing strategy version for {slug}")
            continue
        version_by_slug[slug] = str(row)

    for slug in version_by_slug:
        exp_uuid = uuid.uuid5(uuid.NAMESPACE_DNS, EXPERIMENT_ID_BY_SLUG[slug])
        nvda = store.get_instrument_by_symbol("NVDA")
        if nvda:
            store.session.execute(
                text(
                    """
                    INSERT INTO experiments (id, name, description, instrument_id, status, start_date)
                    VALUES (:id, :name, :desc, :iid, 'running', NOW())
                    ON CONFLICT (id) DO UPDATE SET status = 'running', end_date = NULL
                    """
                ),
                {
                    "id": exp_uuid,
                    "name": f"Phase3 {slug}",
                    "desc": EXPERIMENT_ID_BY_SLUG[slug],
                    "iid": nvda.id,
                },
            )

    for slug in version_by_slug:
        for symbol in eligible:
            inst = store.get_instrument_by_symbol(symbol)
            if not inst:
                continue
            pid = _portfolio_uuid(slug, symbol)
            iid = _instance_uuid(slug, symbol)
            exp_uuid = uuid.uuid5(uuid.NAMESPACE_DNS, EXPERIMENT_ID_BY_SLUG[slug])
            store.session.execute(
                text(
                    """
                    INSERT INTO portfolios (
                      id, owner_id, name, mode, initial_capital, balance, unrealized_pnl,
                      equity, exposure_notional, reserved_capital, currency, status, peak_equity
                    ) VALUES (
                      :id, :owner, :name, 'paper', 2000, 2000, 0, 2000, 0, 0, 'USD', 'active', 2000
                    )
                    ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name, status = 'active'
                    """
                ),
                {
                    "id": pid,
                    "owner": uuid.UUID(OWNER_ID),
                    "name": f"Phase3 {slug} {symbol}",
                },
            )
            store.session.execute(
                text(
                    """
                    INSERT INTO strategy_instances (
                      id, portfolio_id, strategy_version_id, instrument_id,
                      timeframe, risk_profile_id, parameter_overrides, is_active, experiment_id
                    ) VALUES (
                      :id, :pid, CAST(:sv AS uuid), :iid, :tf, :risk,
                      CAST(:overrides AS jsonb), TRUE, :exp
                    )
                    ON CONFLICT (id) DO UPDATE SET
                      is_active = TRUE,
                      instrument_id = EXCLUDED.instrument_id,
                      timeframe = EXCLUDED.timeframe,
                      strategy_version_id = EXCLUDED.strategy_version_id
                    """
                ),
                {
                    "id": iid,
                    "pid": pid,
                    "sv": version_by_slug[slug],
                    "iid": inst.id,
                    "tf": PHASE3_TIMEFRAME,
                    "risk": risk_id,
                    "overrides": json.dumps(
                        {"phase3_experiment_id": EXPERIMENT_ID_BY_SLUG[slug]}
                    ),
                    "exp": exp_uuid,
                },
            )
            report["activated_instances"] += 1
            # One canonical instance per (slug, symbol); seed IDs may duplicate UUID5 rows.
            store.session.execute(
                text(
                    """
                    UPDATE strategy_instances si
                    SET is_active = FALSE
                    FROM strategy_versions sv, strategies s
                    WHERE si.strategy_version_id = sv.id
                      AND sv.strategy_id = s.id
                      AND s.slug = :slug
                      AND si.instrument_id = :iid
                      AND si.timeframe = :tf
                      AND si.is_active = TRUE
                      AND si.id <> CAST(:canonical AS uuid)
                    """
                ),
                {
                    "slug": slug,
                    "iid": inst.id,
                    "tf": PHASE3_TIMEFRAME,
                    "canonical": str(iid),
                },
            )

    # Deactivate stale (symbol left active universe) — never delete rows.
    slug_list = tuple(version_by_slug.keys())
    if slug_list:
        rows = store.session.execute(
            text(
                """
                SELECT si.id::text, upper(replace(i.symbol, '/', '')) AS sym, s.slug
                FROM strategy_instances si
                JOIN instruments i ON i.id = si.instrument_id
                JOIN strategy_versions sv ON sv.id = si.strategy_version_id
                JOIN strategies s ON s.id = sv.strategy_id
                WHERE s.slug = ANY(:slugs)
                  AND si.timeframe = :tf
                  AND si.is_active = TRUE
                """
            ),
            {"slugs": list(slug_list), "tf": PHASE3_TIMEFRAME},
        ).all()
        eligible_set = eligible
        for inst_id, sym, _slug in rows:
            if _sym(str(sym)) not in eligible_set:
                store.session.execute(
                    text("UPDATE strategy_instances SET is_active = FALSE WHERE id = CAST(:id AS uuid)"),
                    {"id": inst_id},
                )
                report["deactivated_instances"] += 1

    store.update_settings(PHASE3_LIVE_SIM_ENABLED_KEY, True, flush=False)
    return report


