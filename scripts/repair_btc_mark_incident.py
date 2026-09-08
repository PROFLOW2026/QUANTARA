#!/usr/bin/env python3
"""Repair invalid BTC 5m positions from mark-price + catch-up incident (2026-09-08)."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "engine"))

from sqlalchemy import text  # noqa: E402

from quantara_engine.competition.constants import (  # noqa: E402
    ACTIVE_COMPETITION_EXPERIMENT_ID,
    PORTFOLIO_DEF_BY_ID,
)
from quantara_engine.db.session import engine, session_scope  # noqa: E402
from quantara_engine.persistence.store import TradingStore  # noqa: E402
from quantara_engine.portfolio.reconciliation import reconcile_portfolio_peak_and_halt  # noqa: E402

BTC_5M_PORTFOLIO_IDS = tuple(
    pid
    for pid, pdef in PORTFOLIO_DEF_BY_ID.items()
    if pdef.timeframe == "5m" and pid.startswith("00000000-0000-0000-0000-0000000013")
)

CANONICAL_BALANCE = Decimal("30200.38")
CANONICAL_REALIZED = Decimal("200.38")
CANONICAL_CLOSED_TRADES = 5


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
            SELECT COUNT(*)::int FROM positions pos
            JOIN strategy_instances si ON si.portfolio_id = pos.portfolio_id
            WHERE si.experiment_id = :exp AND pos.status = 'open'
            """
        ),
        {"exp": ACTIVE_COMPETITION_EXPERIMENT_ID},
    ).scalar()
    closed = conn.execute(
        text(
            """
            SELECT COUNT(*)::int, COALESCE(SUM(t.realized_pnl), 0)::float
            FROM trades t
            JOIN strategy_instances si ON si.id = t.strategy_instance_id
            WHERE si.experiment_id = :exp
            """
        ),
        {"exp": ACTIVE_COMPETITION_EXPERIMENT_ID},
    ).one()
    return {
        "portfolios": row[0],
        "combined_balance": round(float(row[1]), 2),
        "combined_equity": round(float(row[2]), 2),
        "unrealized_pnl": round(float(row[3]), 2),
        "open_positions": int(open_pos or 0),
        "closed_trades": int(closed[0] or 0),
        "realized_pnl": round(float(closed[1] or 0), 2),
    }


def repair(*, dry_run: bool = True) -> dict:
    report: dict = {"dry_run": dry_run, "btc_5m_portfolios": list(BTC_5M_PORTFOLIO_IDS)}

    with engine.connect() as conn:
        report["before"] = _financial_summary(conn)

        position_ids = [
            str(r[0])
            for r in conn.execute(
                text(
                    """
                    SELECT id FROM positions
                    WHERE portfolio_id = ANY(CAST(:pids AS uuid[]))
                      AND status = 'open'
                      AND backtest_run_id IS NULL
                    """
                ),
                {"pids": list(BTC_5M_PORTFOLIO_IDS)},
            ).all()
        ]
        report["open_btc_positions"] = position_ids

        if dry_run:
            return report

        conn.execute(text("ALTER TABLE trades DISABLE TRIGGER trades_no_delete"))

        for position_id in position_ids:
            conn.execute(
                text(
                    """
                    DELETE FROM fills f
                    USING orders o
                    WHERE f.order_id = o.id
                      AND f.position_id = :pos_id
                    """
                ),
                {"pos_id": position_id},
            )
            conn.execute(
                text(
                    """
                    DELETE FROM fills f
                    USING orders o
                    WHERE f.order_id = o.id
                      AND o.portfolio_id IN (
                        SELECT portfolio_id FROM positions WHERE id = :pos_id
                      )
                      AND f.position_id IS NULL
                      AND o.backtest_run_id IS NULL
                    """
                ),
                {"pos_id": position_id},
            )

        for pid in BTC_5M_PORTFOLIO_IDS:
            conn.execute(
                text(
                    """
                    DELETE FROM fills f
                    USING orders o
                    WHERE f.order_id = o.id
                      AND o.portfolio_id = :pid
                      AND o.backtest_run_id IS NULL
                    """
                ),
                {"pid": pid},
            )
            conn.execute(
                text(
                    """
                    DELETE FROM orders
                    WHERE portfolio_id = :pid AND backtest_run_id IS NULL
                    """
                ),
                {"pid": pid},
            )
            conn.execute(
                text(
                    """
                    DELETE FROM order_intents
                    WHERE portfolio_id = :pid AND backtest_run_id IS NULL
                    """
                ),
                {"pid": pid},
            )

        for position_id in position_ids:
            conn.execute(
                text("DELETE FROM positions WHERE id = :pos_id"),
                {"pos_id": position_id},
            )

        for pid in BTC_5M_PORTFOLIO_IDS:
            conn.execute(
                text(
                    """
                    UPDATE portfolios
                    SET equity = balance,
                        unrealized_pnl = 0,
                        exposure_notional = 0,
                        reserved_capital = 0,
                        updated_at = NOW()
                    WHERE id = :pid
                    """
                ),
                {"pid": pid},
            )

        conn.execute(
            text(
                """
                UPDATE portfolios p
                SET equity = p.balance,
                    unrealized_pnl = 0,
                    exposure_notional = 0,
                    reserved_capital = 0,
                    updated_at = NOW()
                FROM strategy_instances si
                WHERE si.portfolio_id = p.id
                  AND si.experiment_id = :exp
                  AND NOT EXISTS (
                    SELECT 1 FROM positions pos
                    WHERE pos.portfolio_id = p.id AND pos.status = 'open'
                  )
                """
            ),
            {"exp": ACTIVE_COMPETITION_EXPERIMENT_ID},
        )

        conn.execute(text("ALTER TABLE trades ENABLE TRIGGER trades_no_delete"))
        conn.commit()

    with session_scope() as session:
        store = TradingStore(session)
        now = datetime.now(timezone.utc)
        peak_repair: list[dict] = []
        for pid in BTC_5M_PORTFOLIO_IDS:
            state = store.load_portfolio_state(pid)
            entry = next(
                e for e in store.list_competition_entries() if e["portfolio"].id == pid
            )
            repaired_pf, peak_result = reconcile_portfolio_peak_and_halt(
                state.portfolio,
                entry["risk_profile"],
                state.snapshots,
            )
            state.portfolio = repaired_pf
            snap = state.create_snapshot(now)
            store.save_snapshot(snap)
            store.update_portfolio(state.portfolio)
            peak_repair.append(
                {
                    "portfolio_id": pid,
                    "peak_equity": float(repaired_pf.peak_equity),
                    "status": repaired_pf.status.value,
                    "unhalt_reason": peak_result.unhalt_reason,
                }
            )
        report["peak_repair"] = peak_repair

    with engine.connect() as conn:
        report["after"] = _financial_summary(conn)

    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Apply repair (default: dry run)")
    args = parser.parse_args()
    result = repair(dry_run=not args.apply)
    import json

    print(json.dumps(result, indent=2))
    if args.apply:
        after = result.get("after", {})
        ok = (
            after.get("combined_balance") == float(CANONICAL_BALANCE)
            and after.get("realized_pnl") == float(CANONICAL_REALIZED)
            and after.get("open_positions") == 0
            and after.get("closed_trades") == CANONICAL_CLOSED_TRADES
            and after.get("unrealized_pnl") == 0.0
        )
        print("repair_ok", ok)


if __name__ == "__main__":
    main()
