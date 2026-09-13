"""Trading-week baseline snapshot — audit/reference only."""

from __future__ import annotations

import logging
import subprocess
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import text

logger = logging.getLogger(__name__)


def _iso_week_label(now: datetime) -> str:
    iso = now.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def _git_commit_sha() -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if result.returncode == 0:
            return result.stdout.strip() or None
    except Exception:
        logger.debug("Unable to resolve git commit SHA for baseline", exc_info=True)
    return None


def get_active_baseline(store) -> dict[str, Any] | None:
    row = store.session.execute(
        text(
            """
            SELECT id::text, week_label, activated_at, commit_sha, snapshot, is_active
            FROM trading_week_baselines
            WHERE is_active = TRUE
            ORDER BY activated_at DESC
            LIMIT 1
            """
        )
    ).mappings().first()
    return dict(row) if row else None


def build_baseline_snapshot(store) -> dict[str, Any]:
    """Collect current configuration without mutating it."""
    strategies = store.session.execute(
        text(
            """
            SELECT s.slug, sv.version, si.id::text AS instance_id,
                   si.timeframe, si.parameter_overrides, si.is_active,
                   si.experiment_id, rp.slug AS risk_profile_slug,
                   rp.risk_per_trade_pct, rp.max_drawdown_pct, rp.daily_loss_limit_pct
            FROM strategy_instances si
            JOIN strategy_versions sv ON sv.id = si.strategy_version_id
            JOIN strategies s ON s.id = sv.strategy_id
            LEFT JOIN risk_profiles rp ON rp.id = si.risk_profile_id
            WHERE si.is_active = TRUE
            ORDER BY s.slug, si.timeframe, rp.slug
            """
        )
    ).mappings().all()

    instruments = store.session.execute(
        text(
            """
            SELECT symbol, asset_class, quote_currency, is_active
            FROM instruments
            WHERE COALESCE(is_active, TRUE) = TRUE
            ORDER BY symbol
            """
        )
    ).mappings().all()

    paper_run = store.session.execute(
        text(
            """
            SELECT id::text, status, starting_broker_cash, metadata, created_at
            FROM paper_runs
            WHERE status = 'active'
            ORDER BY created_at DESC
            LIMIT 1
            """
        )
    ).mappings().first()

    portfolio_count = store.session.execute(
        text(
            """
            SELECT COUNT(DISTINCT si.portfolio_id)::int AS n
            FROM strategy_instances si
            WHERE si.is_active = TRUE
              AND si.experiment_id IS NOT NULL
            """
        )
    ).scalar()

    owner = store.session.execute(
        text(
            """
            SELECT id::text, slug, name, target_capital, risk_settings, metadata,
                   equal_asset_allocation_enabled, updated_at
            FROM owner_trading_portfolios
            WHERE slug = 'live-sim-owner' OR status = 'active'
            ORDER BY CASE WHEN slug = 'live-sim-owner' THEN 0 ELSE 1 END, updated_at DESC
            LIMIT 1
            """
        )
    ).mappings().first()

    brokers = store.session.execute(
        text(
            """
            SELECT id::text, slug, starting_cash, equity, cash, risk_settings,
                   account_state::text AS account_state, is_active
            FROM broker_accounts
            WHERE slug LIKE 'live-sim%'
            ORDER BY slug
            """
        )
    ).mappings().all()

    asset_allocs = store.session.execute(
        text(
            """
            SELECT canonical_symbol AS symbol,
                   starting_allocated_capital,
                   current_cash AS allocated_capital,
                   high_water_mark, daily_start_equity, enabled
            FROM owner_portfolio_asset_allocations
            ORDER BY canonical_symbol
            """
        )
    ).mappings().all()

    timeframes = sorted(
        {str(r["timeframe"]) for r in strategies if r.get("timeframe")}
    )

    return {
        "active_strategy_versions": [
            {
                "slug": r["slug"],
                "version": r["version"],
                "instance_id": r["instance_id"],
                "timeframe": r["timeframe"],
                "parameter_overrides": r["parameter_overrides"] or {},
                "experiment_id": r["experiment_id"],
                "risk_profile_slug": r["risk_profile_slug"],
                "risk_per_trade_pct": float(r["risk_per_trade_pct"]) if r["risk_per_trade_pct"] is not None else None,
                "max_drawdown_pct": float(r["max_drawdown_pct"]) if r["max_drawdown_pct"] is not None else None,
                "daily_loss_limit_pct": float(r["daily_loss_limit_pct"]) if r["daily_loss_limit_pct"] is not None else None,
            }
            for r in strategies
        ],
        "active_instruments": [dict(r) for r in instruments],
        "active_timeframes": timeframes,
        "research_run_id": paper_run["id"] if paper_run else None,
        "research_run": dict(paper_run) if paper_run else None,
        "research_portfolio_count": int(portfolio_count or 0),
        "live_sim_owner": dict(owner) if owner else None,
        "broker_allocations": [
            {
                **{k: v for k, v in dict(r).items() if k != "risk_settings"},
                "risk_settings": r["risk_settings"] or {},
                "starting_cash": float(r["starting_cash"]) if r["starting_cash"] is not None else None,
                "equity": float(r["equity"]) if r["equity"] is not None else None,
                "cash": float(r["cash"]) if r["cash"] is not None else None,
            }
            for r in brokers
        ],
        "per_asset_allocations": [
            {
                **dict(r),
                "starting_allocated_capital": float(r["starting_allocated_capital"]) if r["starting_allocated_capital"] is not None else None,
                "allocated_capital": float(r["allocated_capital"]) if r["allocated_capital"] is not None else None,
                "high_water_mark": float(r["high_water_mark"]) if r["high_water_mark"] is not None else None,
                "daily_start_equity": float(r["daily_start_equity"]) if r["daily_start_equity"] is not None else None,
            }
            for r in asset_allocs
        ],
        "execution_assumptions": {
            "fill_timing": "next_open",
            "sl_wins_intrabar": True,
            "note": "Uses existing ExecutionAssumptions / cost profiles per symbol",
        },
    }


def ensure_trading_week_baseline(store, *, now: datetime | None = None) -> dict[str, Any]:
    """Create active baseline once; subsequent calls return existing row."""
    existing = get_active_baseline(store)
    if existing:
        return existing

    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    snapshot = build_baseline_snapshot(store)
    commit_sha = _git_commit_sha()
    baseline_id = str(uuid4())
    week_label = _iso_week_label(now)

    import json

    store.session.execute(
        text(
            """
            INSERT INTO trading_week_baselines (
              id, week_label, activated_at, commit_sha, snapshot, is_active
            ) VALUES (
              CAST(:id AS uuid), :week_label, :activated_at, :commit_sha,
              CAST(:snapshot AS jsonb), TRUE
            )
            """
        ),
        {
            "id": baseline_id,
            "week_label": week_label,
            "activated_at": now,
            "commit_sha": commit_sha,
            "snapshot": json.dumps(snapshot, default=str),
        },
    )
    store.session.flush()
    logger.info(
        "Trading week baseline activated id=%s week=%s sha=%s",
        baseline_id,
        week_label,
        commit_sha,
    )
    return get_active_baseline(store) or {
        "id": baseline_id,
        "week_label": week_label,
        "activated_at": now,
        "commit_sha": commit_sha,
        "snapshot": snapshot,
        "is_active": True,
    }
