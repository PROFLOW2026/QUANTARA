"""Live Sim strategic-reset risk profile (simulation learning mode)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import text

from quantara_engine.broker.accounts import LIVE_SIM_10K_ACCOUNT_SLUG
from quantara_engine.owner_portfolio.service import LIVE_SIM_OWNER_SLUG
from quantara_engine.persistence.store import TradingStore

EXPERIMENT_ID = "live_sim_strategic_reset_2026_10"

# Per-trade / portfolio caps for active learning (not production capital preservation).
RISK_PER_TRADE_PCT = Decimal("2.50")
MAX_TOTAL_OPEN_SL_RISK_PCT = Decimal("15.00")
MAX_SYMBOL_SL_RISK_PCT = Decimal("7.50")
MAX_GROUP_SL_RISK_PCT = Decimal("12.00")
DAILY_LOSS_GATE_PCT = Decimal("10.00")
# Drawdown from HWM: looser than legacy 10% so a losing streak still generates data.
MAX_DRAWDOWN_GATE_PCT = Decimal("25.00")
MAX_CONCURRENT_POSITIONS = 8

BROKER_RISK_SLUGS = (
    LIVE_SIM_10K_ACCOUNT_SLUG,
    "live-sim-ibkr-like",
    "live-sim-kraken-like",
)


def risk_settings_patch() -> dict[str, str]:
    return {
        "risk_per_trade_pct": str(RISK_PER_TRADE_PCT),
        "max_total_open_sl_risk_pct": str(MAX_TOTAL_OPEN_SL_RISK_PCT),
        "max_symbol_sl_risk_pct": str(MAX_SYMBOL_SL_RISK_PCT),
        "max_group_sl_risk_pct": str(MAX_GROUP_SL_RISK_PCT),
        "daily_loss_gate_pct": str(DAILY_LOSS_GATE_PCT),
        "max_drawdown_gate_pct": str(MAX_DRAWDOWN_GATE_PCT),
        "concentration_mode": "ENFORCE",
        "live_sim_experiment_id": EXPERIMENT_ID,
    }


def apply_experimental_risk_profile(
    store: TradingStore,
    *,
    reset_high_water_marks: bool = True,
    observation_anchor: datetime | None = None,
) -> dict[str, Any]:
    """Persist experimental risk JSON on broker + owner rows; optionally reset HWM baselines."""
    anchor = observation_anchor or datetime.now(timezone.utc)
    anchor_iso = anchor.isoformat()
    patch = risk_settings_patch()
    updated_brokers: list[str] = []

    for slug in BROKER_RISK_SLUGS:
        row = store.session.execute(
            text(
                """
                SELECT id::text, equity, account_metadata, risk_settings
                FROM broker_accounts WHERE slug = :slug
                """
            ),
            {"slug": slug},
        ).mappings().first()
        if not row:
            continue
        equity = Decimal(str(row.get("equity") or 0))
        meta = dict(row.get("account_metadata") or {})
        meta["live_sim_experiment"] = EXPERIMENT_ID
        meta["strategic_reset_anchor"] = anchor_iso
        meta["v32_observation_anchor"] = anchor_iso
        meta["baseline_reset_at"] = anchor_iso
        risk = {**dict(row.get("risk_settings") or {}), **patch}
        if reset_high_water_marks and equity > 0:
            risk["high_water_mark"] = float(equity)
            risk["daily_start_equity"] = float(equity)
            risk["daily_start_date"] = anchor.astimezone(timezone.utc).strftime("%Y-%m-%d")
        store.session.execute(
            text(
                """
                UPDATE broker_accounts
                SET risk_settings = CAST(:risk AS jsonb),
                    account_metadata = CAST(:meta AS jsonb),
                    updated_at = NOW()
                WHERE id = CAST(:id AS uuid)
                """
            ),
            {"id": row["id"], "risk": json.dumps(risk), "meta": json.dumps(meta)},
        )
        updated_brokers.append(slug)

    owner = store.session.execute(
        text(
            """
            SELECT id::text, risk_settings
            FROM owner_trading_portfolios WHERE slug = :slug
            """
        ),
        {"slug": LIVE_SIM_OWNER_SLUG},
    ).mappings().first()
    owner_updated = False
    if owner:
        from quantara_engine.owner_portfolio.asset_ledger import aggregate_owner_from_assets

        owner_risk = {**dict(owner.get("risk_settings") or {}), **patch}
        agg = aggregate_owner_from_assets(store, LIVE_SIM_OWNER_SLUG)
        eq = Decimal(str(agg.get("equity") or 0))
        if reset_high_water_marks and eq > 0:
            owner_risk["high_water_mark"] = float(eq)
            owner_risk["daily_start_equity"] = float(eq)
            owner_risk["daily_start_date"] = anchor.astimezone(timezone.utc).strftime("%Y-%m-%d")
        store.session.execute(
            text(
                """
                UPDATE owner_trading_portfolios
                SET risk_settings = CAST(:risk AS jsonb), updated_at = NOW()
                WHERE id = CAST(:id AS uuid)
                """
            ),
            {"id": owner["id"], "risk": json.dumps(owner_risk)},
        )
        owner_updated = True

        if reset_high_water_marks:
            from quantara_engine.owner_portfolio.asset_allocation import asset_equity, get_asset_allocation_row

            today = anchor.astimezone(timezone.utc).strftime("%Y-%m-%d")
            rows = store.session.execute(
                text(
                    """
                    SELECT id::text, canonical_symbol
                    FROM owner_portfolio_asset_allocations
                    WHERE owner_portfolio_id = CAST(:pid AS uuid)
                    """
                ),
                {"pid": owner["id"]},
            ).mappings().all()
            for row in rows:
                alloc = get_asset_allocation_row(
                    store,
                    owner_slug=LIVE_SIM_OWNER_SLUG,
                    canonical_symbol=str(row["canonical_symbol"]),
                )
                if not alloc:
                    continue
                eq = asset_equity(alloc)
                store.session.execute(
                    text(
                        """
                        UPDATE owner_portfolio_asset_allocations
                        SET high_water_mark = :hwm,
                            daily_start_equity = :dse,
                            daily_start_date = :today,
                            updated_at = NOW()
                        WHERE id = CAST(:id AS uuid)
                        """
                    ),
                    {
                        "id": row["id"],
                        "hwm": float(eq),
                        "dse": float(eq),
                        "today": today,
                    },
                )

    return {
        "experiment_id": EXPERIMENT_ID,
        "brokers_updated": updated_brokers,
        "owner_updated": owner_updated,
        "risk_patch": patch,
        "anchor_utc": anchor_iso,
        "reset_high_water_marks": reset_high_water_marks,
    }
