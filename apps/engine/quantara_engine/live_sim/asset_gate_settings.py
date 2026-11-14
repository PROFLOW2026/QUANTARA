"""Asset-scoped gate settings for equal-asset Live Sim envelopes."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from quantara_engine.live_sim.risk_policy import (
    LiveSimRiskSettings,
    _dec,
    resolve_utc_daily_start,
)
from quantara_engine.owner_portfolio.asset_allocation import asset_equity
from quantara_engine.persistence.store import TradingStore


def reset_asset_gate_baselines_to_current_equity(store: TradingStore, asset_id: str) -> None:
    """Re-anchor HWM/daily scope when an equal-asset slot rotates symbol (ledger unchanged)."""
    from sqlalchemy import text

    row = store.session.execute(
        text(
            """
            SELECT current_cash, unrealized_pnl
            FROM owner_portfolio_asset_allocations
            WHERE id = CAST(:id AS uuid)
            """
        ),
        {"id": asset_id},
    ).mappings().first()
    if not row:
        return
    equity = asset_equity(dict(row))
    store.session.execute(
        text(
            """
            UPDATE owner_portfolio_asset_allocations
            SET high_water_mark = :eq,
                daily_start_equity = :eq,
                daily_start_date = (NOW() AT TIME ZONE 'UTC')::date,
                updated_at = NOW()
            WHERE id = CAST(:id AS uuid)
            """
        ),
        {"id": asset_id, "eq": equity},
    )


def reanchor_pre_experiment_asset_baseline(
    store: TradingStore,
    asset_row: dict[str, Any],
    *,
    broker_account_id: str | None,
) -> bool:
    """Self-heal: discard HWM / daily-start baselines stamped BEFORE the experiment anchor.

    Any reset path (hard wipe, manual SQL, profile switch) that forgets to re-anchor the
    per-asset risk baseline would otherwise let a previous experiment's equity peak or
    day-start keep gating (or loosening) the new run. If the allocation row has not been
    touched since the current experiment anchor, its baselines are by definition stale and
    are re-anchored to the row's current equity. Mutates ``asset_row`` in place.
    """
    from datetime import timezone

    from sqlalchemy import text

    if not broker_account_id or not asset_row.get("id"):
        return False
    anchor_raw = store.session.execute(
        text(
            """
            SELECT COALESCE(account_metadata->>'v32_observation_anchor',
                            account_metadata->>'baseline_reset_at')
            FROM broker_accounts WHERE id = CAST(:aid AS uuid)
            """
        ),
        {"aid": broker_account_id},
    ).scalar()
    if not anchor_raw:
        return False
    try:
        anchor = datetime.fromisoformat(str(anchor_raw).replace("Z", "+00:00"))
    except ValueError:
        return False
    if anchor.tzinfo is None:
        anchor = anchor.replace(tzinfo=timezone.utc)
    row = store.session.execute(
        text(
            """
            SELECT updated_at, current_cash, unrealized_pnl, high_water_mark,
                   daily_start_equity, daily_start_date
            FROM owner_portfolio_asset_allocations
            WHERE id = CAST(:id AS uuid)
            """
        ),
        {"id": asset_row["id"]},
    ).mappings().first()
    if not row or row["updated_at"] is None:
        return False
    updated = row["updated_at"]
    if updated.tzinfo is None:
        updated = updated.replace(tzinfo=timezone.utc)
    if updated >= anchor:
        return False
    reset_asset_gate_baselines_to_current_equity(store, str(asset_row["id"]))
    equity = asset_equity(dict(row))
    asset_row["high_water_mark"] = equity
    asset_row["daily_start_equity"] = equity
    asset_row["daily_start_date"] = datetime.now(timezone.utc).date().isoformat()
    return True


def load_asset_gate_settings(
    asset_row: dict[str, Any],
    broker_limits: LiveSimRiskSettings,
) -> LiveSimRiskSettings:
    """Merge asset HWM/daily scope with broker-local risk limit percentages."""
    starting = _dec(asset_row.get("starting_allocated_capital"), Decimal("1250"))
    equity = _dec(asset_row.get("current_cash"), starting) + _dec(
        asset_row.get("unrealized_pnl"), Decimal("0")
    )
    hwm = _dec(asset_row.get("high_water_mark"), max(equity, starting))
    daily_start = _dec(asset_row.get("daily_start_equity"), equity)
    return LiveSimRiskSettings(
        risk_per_trade_pct=broker_limits.risk_per_trade_pct,
        max_total_open_sl_risk_pct=broker_limits.max_total_open_sl_risk_pct,
        max_symbol_sl_risk_pct=broker_limits.max_symbol_sl_risk_pct,
        max_group_sl_risk_pct=broker_limits.max_group_sl_risk_pct,
        daily_loss_gate_pct=broker_limits.daily_loss_gate_pct,
        max_drawdown_gate_pct=broker_limits.max_drawdown_gate_pct,
        concentration_mode=broker_limits.concentration_mode,
        high_water_mark=hwm,
        daily_start_equity=daily_start,
        daily_start_date=str(asset_row.get("daily_start_date") or ""),
    )


def maybe_roll_asset_daily_start(
    store: TradingStore,
    asset_id: str,
    settings: LiveSimRiskSettings,
    equity: Decimal,
    now: datetime,
) -> LiveSimRiskSettings:
    baseline, today, status = resolve_utc_daily_start(
        stored_equity=settings.daily_start_equity,
        stored_date=settings.daily_start_date,
        scope_equity=equity,
        now=now,
    )
    if status != "verified_open":
        return settings
    from sqlalchemy import text

    store.session.execute(
        text(
            """
            UPDATE owner_portfolio_asset_allocations
            SET daily_start_equity = :equity,
                daily_start_date = :today,
                updated_at = NOW()
            WHERE id = CAST(:id AS uuid)
            """
        ),
        {"id": asset_id, "equity": baseline, "today": today},
    )
    return LiveSimRiskSettings(
        risk_per_trade_pct=settings.risk_per_trade_pct,
        max_total_open_sl_risk_pct=settings.max_total_open_sl_risk_pct,
        max_symbol_sl_risk_pct=settings.max_symbol_sl_risk_pct,
        max_group_sl_risk_pct=settings.max_group_sl_risk_pct,
        daily_loss_gate_pct=settings.daily_loss_gate_pct,
        max_drawdown_gate_pct=settings.max_drawdown_gate_pct,
        concentration_mode=settings.concentration_mode,
        high_water_mark=settings.high_water_mark,
        daily_start_equity=baseline,
        daily_start_date=today,
    )


def update_asset_high_water_mark(
    store: TradingStore,
    asset_id: str,
    equity: Decimal,
) -> Decimal:
    from sqlalchemy import text

    row = store.session.execute(
        text(
            """
            SELECT high_water_mark FROM owner_portfolio_asset_allocations
            WHERE id = CAST(:id AS uuid)
            """
        ),
        {"id": asset_id},
    ).scalar()
    hwm = Decimal(str(row or equity))
    if equity > hwm:
        hwm = equity
        store.session.execute(
            text(
                """
                UPDATE owner_portfolio_asset_allocations
                SET high_water_mark = :hwm, updated_at = NOW()
                WHERE id = CAST(:id AS uuid)
                """
            ),
            {"id": asset_id, "hwm": hwm},
        )
    return hwm
