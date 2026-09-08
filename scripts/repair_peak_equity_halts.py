#!/usr/bin/env python3
"""Repair stale peak_equity and phantom drawdown halts from BTC mark incident."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "engine"))

from sqlalchemy import select, text  # noqa: E402

from quantara_engine.competition.constants import (  # noqa: E402
    ACTIVE_COMPETITION_EXPERIMENT_ID,
    PORTFOLIO_DEF_BY_ID,
)
from quantara_engine.db.session import engine, session_scope  # noqa: E402
from quantara_engine.domain.types import PortfolioStatus  # noqa: E402
from quantara_engine.models.enums import DecisionType as OrmDecisionType  # noqa: E402
from quantara_engine.models.trading import Decision as OrmDecision  # noqa: E402
from quantara_engine.persistence.store import TradingStore, _uuid  # noqa: E402
from quantara_engine.portfolio.reconciliation import reconcile_portfolio_peak_and_halt  # noqa: E402

BTC_5M_PORTFOLIO_IDS = tuple(
    pid
    for pid, pdef in PORTFOLIO_DEF_BY_ID.items()
    if pdef.timeframe == "5m" and pid.startswith("00000000-0000-0000-0000-0000000013")
)

INCIDENT_START = datetime(2026, 9, 8, 20, 30, 0, tzinfo=timezone.utc)


def _drawdown_pct(peak: Decimal, equity: Decimal) -> float:
    if peak <= 0:
        return 0.0
    return float((peak - equity) / peak * Decimal("100"))


def _financial_summary(conn) -> dict:
    row = conn.execute(
        text(
            """
            SELECT COUNT(DISTINCT p.id)::int,
                   COALESCE(SUM(p.balance), 0)::float,
                   COALESCE(SUM(p.equity), 0)::float,
                   COALESCE(SUM(p.unrealized_pnl), 0)::float
            FROM portfolios p
            JOIN strategy_instances si ON si.portfolio_id = p.id
            WHERE si.experiment_id = :exp
            """
        ),
        {"exp": ACTIVE_COMPETITION_EXPERIMENT_ID},
    ).one()
    open_pos = conn.execute(
        text(
            """
            SELECT COUNT(*)::int FROM positions
            WHERE status = 'open' AND backtest_run_id IS NULL
            """
        )
    ).scalar()
    closed = conn.execute(
        text(
            """
            SELECT COUNT(*)::int, COALESCE(SUM(t.realized_pnl), 0)::float
            FROM trades t
            JOIN strategy_instances si ON si.id = t.strategy_instance_id
            WHERE si.experiment_id = :exp AND t.backtest_run_id IS NULL
            """
        ),
        {"exp": ACTIVE_COMPETITION_EXPERIMENT_ID},
    ).one()
    pending = conn.execute(
        text(
            """
            SELECT COUNT(*)::int FROM order_intents oi
            JOIN strategy_instances si ON si.id = oi.strategy_instance_id
            WHERE si.experiment_id = :exp AND oi.status = 'pending_execution'
            """
        ),
        {"exp": ACTIVE_COMPETITION_EXPERIMENT_ID},
    ).scalar()
    return {
        "portfolios": row[0],
        "balance": round(float(row[1]), 2),
        "equity": round(float(row[2]), 2),
        "unrealized": round(float(row[3]), 2),
        "open_positions": int(open_pos or 0),
        "closed_trades": int(closed[0] or 0),
        "realized": round(float(closed[1]), 2),
        "pending_intents": int(pending or 0),
    }


def _halt_trigger_ts(session, instance_id: str) -> str | None:
    row = session.scalar(
        select(OrmDecision)
        .where(
            OrmDecision.strategy_instance_id == _uuid(instance_id),
            OrmDecision.created_at >= INCIDENT_START,
            OrmDecision.decision_type.in_(
                [
                    OrmDecisionType.RISK_DENIED,
                    OrmDecisionType.TRADING_HALTED,
                ]
            ),
        )
        .order_by(OrmDecision.created_at)
        .limit(1)
    )
    return row.created_at.isoformat() if row else None


def repair(*, dry_run: bool = True) -> dict:
    report: dict = {"dry_run": dry_run, "portfolios": []}

    with engine.connect() as conn:
        report["financial_before"] = _financial_summary(conn)

    with session_scope() as session:
        store = TradingStore(session)
        entries = [e for e in store.list_competition_entries() if e["portfolio"].id in BTC_5M_PORTFOLIO_IDS]

        for entry in sorted(entries, key=lambda e: e["risk_profile"].risk_per_trade_pct):
            pid = entry["portfolio"].id
            pdef = PORTFOLIO_DEF_BY_ID[pid]
            state = store.load_portfolio_state(pid)
            pf = state.portfolio
            rp = entry["risk_profile"]

            max_valid_equity = max(
                (s.equity for s in state.snapshots if s.equity <= pf.equity + Decimal("0.01")),
                default=pf.equity,
            )
            halt_ts = _halt_trigger_ts(session, entry["instance"].id)

            row = {
                "portfolio_id": pid,
                "risk_tier": pdef.risk_slug,
                "balance": float(pf.balance),
                "equity": float(pf.equity),
                "old_peak_equity": float(pf.peak_equity),
                "old_drawdown_pct": round(_drawdown_pct(pf.peak_equity, pf.equity), 2),
                "old_status": pf.status.value,
                "old_halt_reason": pf.halt_reason,
                "halt_triggered_at": halt_ts,
                "max_legitimate_snapshot_equity": float(max_valid_equity),
                "max_drawdown_limit_pct": float(rp.max_drawdown_pct),
            }

            repaired_pf, result = reconcile_portfolio_peak_and_halt(pf, rp, state.snapshots)
            row.update(
                {
                    "canonical_peak_equity": float(result.canonical_peak_equity),
                    "new_drawdown_pct": float(result.current_drawdown_pct),
                    "new_status": repaired_pf.status.value,
                    "unhalt_reason": result.unhalt_reason,
                    "excluded_phantom_snapshots": result.excluded_phantom_snapshots,
                }
            )
            report["portfolios"].append(row)

            if not dry_run:
                store.update_portfolio(repaired_pf)

        session.commit()

    with engine.connect() as conn:
        report["financial_after"] = _financial_summary(conn)

    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    result = repair(dry_run=not args.apply)
    print(json.dumps(result, indent=2))
    if args.apply:
        after = result["financial_after"]
        ok = (
            after["balance"] == result["financial_before"]["balance"]
            and after["equity"] == result["financial_before"]["equity"]
            and after["open_positions"] == 0
            and all(p["new_status"] == "active" for p in result["portfolios"])
        )
        print("repair_ok", ok)


if __name__ == "__main__":
    main()
