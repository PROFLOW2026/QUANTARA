"""Daily-loss counterfactual shadow — does NOT halt active Research portfolios."""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from uuid import uuid4

from sqlalchemy import text

logger = logging.getLogger(__name__)


def _as_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def compute_daily_loss_shadow_for_portfolio(
    store,
    *,
    portfolio_id: str,
    trade_date: date,
    daily_loss_limit_pct: Decimal,
    baseline_id: str | None,
) -> dict[str, Any]:
    """
    Observational counterfactual:
    - Find when equity drawdown from start-of-day would hit daily_loss_limit_pct.
    - ACTUAL: all realized PnL that day.
    - SHADOW STOP: ignore NEW entries after halt; keep exits of positions opened before halt.
    """
    day_start = datetime(trade_date.year, trade_date.month, trade_date.day, tzinfo=timezone.utc)
    day_end = day_start + timedelta(days=1)

    # Start equity: latest snapshot at/before day_start, else portfolio equity
    start_row = store.session.execute(
        text(
            """
            SELECT equity
            FROM portfolio_snapshots
            WHERE portfolio_id = CAST(:pid AS uuid)
              AND created_at <= :day_start
            ORDER BY created_at DESC
            LIMIT 1
            """
        ),
        {"pid": portfolio_id, "day_start": day_start},
    ).scalar()
    if start_row is None:
        start_row = store.session.execute(
            text("SELECT equity FROM portfolios WHERE id = CAST(:pid AS uuid)"),
            {"pid": portfolio_id},
        ).scalar()
    start_equity = Decimal(str(start_row or 0))
    if start_equity <= 0:
        result = {
            "portfolio_id": portfolio_id,
            "trade_date": trade_date.isoformat(),
            "daily_loss_limit_pct": float(daily_loss_limit_pct),
            "start_equity": float(start_equity),
            "shadow_halt": False,
            "shadow_halt_time": None,
            "threshold_pct": float(daily_loss_limit_pct),
            "equity_at_halt": None,
            "actual_eod_pnl": 0.0,
            "shadow_stop_eod_pnl": 0.0,
            "difference": 0.0,
        }
        _upsert(store, baseline_id, result)
        return result

    trades = store.session.execute(
        text(
            """
            SELECT id::text, realized_pnl, opened_at, closed_at, exit_reason
            FROM trades
            WHERE portfolio_id = CAST(:pid AS uuid)
              AND closed_at >= :day_start AND closed_at < :day_end
            ORDER BY closed_at ASC
            """
        ),
        {"pid": portfolio_id, "day_start": day_start, "day_end": day_end},
    ).mappings().all()

    # Positions opened during the day (for counterfactual entry cutoff)
    opens = store.session.execute(
        text(
            """
            SELECT id::text, opened_at
            FROM positions
            WHERE portfolio_id = CAST(:pid AS uuid)
              AND opened_at >= :day_start AND opened_at < :day_end
            """
        ),
        {"pid": portfolio_id, "day_start": day_start, "day_end": day_end},
    ).mappings().all()
    open_times = {r["id"]: _as_utc(r["opened_at"]) for r in opens if r["opened_at"]}

    # Also map trade.position via trades if needed — trades have position_id
    trade_pos = store.session.execute(
        text(
            """
            SELECT t.id::text AS trade_id, t.position_id::text AS position_id,
                   t.realized_pnl, t.opened_at, t.closed_at
            FROM trades t
            WHERE t.portfolio_id = CAST(:pid AS uuid)
              AND t.closed_at >= :day_start AND t.closed_at < :day_end
            ORDER BY t.closed_at ASC
            """
        ),
        {"pid": portfolio_id, "day_start": day_start, "day_end": day_end},
    ).mappings().all()

    actual_pnl = sum((Decimal(str(t["realized_pnl"] or 0)) for t in trade_pos), Decimal("0"))

    # Reconstruct equity path using snapshots if available, else cumulative trade PnL
    snapshots = store.session.execute(
        text(
            """
            SELECT equity, created_at
            FROM portfolio_snapshots
            WHERE portfolio_id = CAST(:pid AS uuid)
              AND created_at >= :day_start AND created_at < :day_end
            ORDER BY created_at ASC
            """
        ),
        {"pid": portfolio_id, "day_start": day_start, "day_end": day_end},
    ).mappings().all()

    shadow_halt = False
    shadow_halt_time = None
    equity_at_halt = None
    threshold = daily_loss_limit_pct

    running = start_equity
    if snapshots:
        for snap in snapshots:
            eq = Decimal(str(snap["equity"]))
            dd_pct = (start_equity - eq) / start_equity * Decimal("100")
            if dd_pct >= daily_loss_limit_pct:
                shadow_halt = True
                shadow_halt_time = _as_utc(snap["created_at"])
                equity_at_halt = eq
                break
            running = eq
    else:
        for t in trade_pos:
            running += Decimal(str(t["realized_pnl"] or 0))
            dd_pct = (start_equity - running) / start_equity * Decimal("100")
            if dd_pct >= daily_loss_limit_pct:
                shadow_halt = True
                shadow_halt_time = _as_utc(t["closed_at"])
                equity_at_halt = running
                break

    # Counterfactual: exclude PnL from trades whose position opened AFTER halt
    shadow_pnl = Decimal("0")
    for t in trade_pos:
        pos_id = t["position_id"]
        opened = open_times.get(pos_id) or (_as_utc(t["opened_at"]) if t["opened_at"] else None)
        if shadow_halt and shadow_halt_time and opened and opened > shadow_halt_time:
            continue  # ignore NEW entries after halt
        shadow_pnl += Decimal(str(t["realized_pnl"] or 0))

    result = {
        "portfolio_id": portfolio_id,
        "trade_date": trade_date.isoformat(),
        "daily_loss_limit_pct": float(daily_loss_limit_pct),
        "start_equity": float(start_equity),
        "shadow_halt": shadow_halt,
        "shadow_halt_time": shadow_halt_time.isoformat() if shadow_halt_time else None,
        "threshold_pct": float(threshold),
        "equity_at_halt": float(equity_at_halt) if equity_at_halt is not None else None,
        "actual_eod_pnl": float(actual_pnl),
        "shadow_stop_eod_pnl": float(shadow_pnl),
        "difference": float(shadow_pnl - actual_pnl),
    }
    _upsert(store, baseline_id, result)
    return result


def _upsert(store, baseline_id: str | None, result: dict[str, Any]) -> None:
    store.session.execute(
        text(
            """
            INSERT INTO learning_daily_loss_shadow (
              id, baseline_id, portfolio_id, trade_date, daily_loss_limit_pct,
              start_equity, shadow_halt, shadow_halt_time, threshold_pct, equity_at_halt,
              actual_eod_pnl, shadow_stop_eod_pnl, difference, metadata
            ) VALUES (
              CAST(:id AS uuid),
              CASE WHEN :baseline_id IS NULL THEN NULL ELSE CAST(:baseline_id AS uuid) END,
              CAST(:portfolio_id AS uuid),
              CAST(:trade_date AS date), :daily_loss_limit_pct,
              :start_equity, :shadow_halt, :shadow_halt_time, :threshold_pct, :equity_at_halt,
              :actual_eod_pnl, :shadow_stop_eod_pnl, :difference, CAST(:metadata AS jsonb)
            )
            ON CONFLICT (portfolio_id, trade_date) DO UPDATE SET
              shadow_halt = EXCLUDED.shadow_halt,
              shadow_halt_time = EXCLUDED.shadow_halt_time,
              threshold_pct = EXCLUDED.threshold_pct,
              equity_at_halt = EXCLUDED.equity_at_halt,
              actual_eod_pnl = EXCLUDED.actual_eod_pnl,
              shadow_stop_eod_pnl = EXCLUDED.shadow_stop_eod_pnl,
              difference = EXCLUDED.difference,
              metadata = EXCLUDED.metadata,
              computed_at = NOW()
            """
        ),
        {
            "id": str(uuid4()),
            "baseline_id": baseline_id,
            "portfolio_id": result["portfolio_id"],
            "trade_date": result["trade_date"],
            "daily_loss_limit_pct": result["daily_loss_limit_pct"],
            "start_equity": result["start_equity"],
            "shadow_halt": result["shadow_halt"],
            "shadow_halt_time": result["shadow_halt_time"],
            "threshold_pct": result["threshold_pct"],
            "equity_at_halt": result["equity_at_halt"],
            "actual_eod_pnl": result["actual_eod_pnl"],
            "shadow_stop_eod_pnl": result["shadow_stop_eod_pnl"],
            "difference": result["difference"],
            "metadata": json.dumps({"observational_only": True, "active_halt": False}),
        },
    )


def refresh_daily_loss_shadows(store, *, baseline_id: str | None, trade_date: date | None = None) -> int:
    """Recompute shadow daily-loss for competition portfolios. Never sets portfolio.halt."""
    trade_date = trade_date or datetime.now(timezone.utc).date()
    rows = store.session.execute(
        text(
            """
            SELECT DISTINCT p.id::text AS portfolio_id, rp.daily_loss_limit_pct
            FROM portfolios p
            JOIN strategy_instances si ON si.portfolio_id = p.id AND si.is_active = TRUE
            JOIN risk_profiles rp ON rp.id = si.risk_profile_id
            WHERE si.experiment_id IS NOT NULL
            """
        )
    ).mappings().all()
    n = 0
    for row in rows:
        compute_daily_loss_shadow_for_portfolio(
            store,
            portfolio_id=row["portfolio_id"],
            trade_date=trade_date,
            daily_loss_limit_pct=Decimal(str(row["daily_loss_limit_pct"])),
            baseline_id=baseline_id,
        )
        n += 1
    return n
