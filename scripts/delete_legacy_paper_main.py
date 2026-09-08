#!/usr/bin/env python3
"""Hard-delete Legacy Paper Main (portfolio ...010 / instance ...020) from live DB."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "engine"))

from sqlalchemy import text  # noqa: E402

from quantara_engine.competition.constants import (  # noqa: E402
    ACTIVE_COMPETITION_EXPERIMENT_ID,
    COMPETITION_TOTAL_INITIAL,
)
from quantara_engine.db.session import engine  # noqa: E402

LEGACY_PORTFOLIO = "00000000-0000-0000-0000-000000000010"
LEGACY_INSTANCE = "00000000-0000-0000-0000-000000000020"


def _count(conn, sql: str, **params) -> int:
    return int(conn.execute(text(sql), params).scalar() or 0)


def delete_legacy_paper_main(*, dry_run: bool = False) -> dict:
    report: dict = {}

    with engine.connect() as conn:
        report["before"] = {
            "legacy_portfolios": _count(
                conn, "SELECT COUNT(*) FROM portfolios WHERE id = :id", id=LEGACY_PORTFOLIO
            ),
            "legacy_instances": _count(
                conn,
                "SELECT COUNT(*) FROM strategy_instances WHERE id = :id OR portfolio_id = :pid",
                id=LEGACY_INSTANCE,
                pid=LEGACY_PORTFOLIO,
            ),
            "legacy_positions": _count(
                conn, "SELECT COUNT(*) FROM positions WHERE portfolio_id = :pid", pid=LEGACY_PORTFOLIO
            ),
            "legacy_trades": _count(
                conn, "SELECT COUNT(*) FROM trades WHERE portfolio_id = :pid", pid=LEGACY_PORTFOLIO
            ),
            "legacy_intents": _count(
                conn,
                """
                SELECT COUNT(*) FROM order_intents
                WHERE portfolio_id = :pid OR strategy_instance_id = :iid
                """,
                pid=LEGACY_PORTFOLIO,
                iid=LEGACY_INSTANCE,
            ),
            "legacy_snapshots": _count(
                conn,
                "SELECT COUNT(*) FROM portfolio_snapshots WHERE portfolio_id = :pid",
                pid=LEGACY_PORTFOLIO,
            ),
            "legacy_decisions": _count(
                conn,
                "SELECT COUNT(*) FROM decisions WHERE strategy_instance_id = :iid",
                iid=LEGACY_INSTANCE,
            ),
            "legacy_signals": _count(
                conn,
                "SELECT COUNT(*) FROM signals WHERE strategy_instance_id = :iid",
                iid=LEGACY_INSTANCE,
            ),
        }

        active = conn.execute(
            text(
                """
                SELECT COUNT(DISTINCT p.id)::int,
                       COALESCE(SUM(p.equity), 0)::float
                FROM portfolios p
                JOIN strategy_instances si ON si.portfolio_id = p.id
                WHERE si.experiment_id = :exp
                """
            ),
            {"exp": ACTIVE_COMPETITION_EXPERIMENT_ID},
        ).one()
        report["active_before"] = {
            "portfolios": active[0],
            "combined_equity": float(active[1]),
        }

        if dry_run:
            report["legacy_backtests"] = _count(
                conn,
                "SELECT COUNT(*) FROM backtest_runs WHERE strategy_instance_id = :iid",
                iid=LEGACY_INSTANCE,
            )
            return report

        backtest_ids = [
            str(row[0])
            for row in conn.execute(
                text("SELECT id FROM backtest_runs WHERE strategy_instance_id = :iid"),
                {"iid": LEGACY_INSTANCE},
            ).all()
        ]

        conn.execute(text("ALTER TABLE trades DISABLE TRIGGER trades_no_delete"))

        stmts = [
            # Legacy backtests tied to Paper Main instance (ephemeral portfolio_id = run id).
            """
            DELETE FROM fills f
            USING orders o
            WHERE f.order_id = o.id
              AND o.backtest_run_id IN (
                SELECT id FROM backtest_runs WHERE strategy_instance_id = :iid
              )
            """,
            """
            DELETE FROM orders
            WHERE backtest_run_id IN (
              SELECT id FROM backtest_runs WHERE strategy_instance_id = :iid
            )
            """,
            """
            DELETE FROM trades
            WHERE backtest_run_id IN (
              SELECT id FROM backtest_runs WHERE strategy_instance_id = :iid
            )
            """,
            """
            DELETE FROM positions
            WHERE backtest_run_id IN (
              SELECT id FROM backtest_runs WHERE strategy_instance_id = :iid
            )
            """,
            """
            DELETE FROM decisions
            WHERE backtest_run_id IN (
              SELECT id FROM backtest_runs WHERE strategy_instance_id = :iid
            )
            """,
            """
            DELETE FROM order_intents
            WHERE backtest_run_id IN (
              SELECT id FROM backtest_runs WHERE strategy_instance_id = :iid
            )
            """,
            """
            DELETE FROM signals
            WHERE backtest_run_id IN (
              SELECT id FROM backtest_runs WHERE strategy_instance_id = :iid
            )
            """,
            """
            DELETE FROM portfolio_snapshots
            WHERE backtest_run_id IN (
              SELECT id FROM backtest_runs WHERE strategy_instance_id = :iid
            )
            """,
            "DELETE FROM backtest_runs WHERE strategy_instance_id = :iid",
            # Paper Main live rows.
            """
            DELETE FROM fills f
            USING orders o
            WHERE f.order_id = o.id
              AND (o.portfolio_id = :pid OR o.intent_id IN (
                SELECT id FROM order_intents
                WHERE portfolio_id = :pid OR strategy_instance_id = :iid
              ))
            """,
            """
            DELETE FROM orders
            WHERE portfolio_id = :pid
               OR intent_id IN (
                 SELECT id FROM order_intents
                 WHERE portfolio_id = :pid OR strategy_instance_id = :iid
               )
            """,
            "DELETE FROM trades WHERE portfolio_id = :pid AND backtest_run_id IS NULL",
            "DELETE FROM positions WHERE portfolio_id = :pid AND backtest_run_id IS NULL",
            "DELETE FROM decisions WHERE strategy_instance_id = :iid",
            """
            DELETE FROM order_intents
            WHERE portfolio_id = :pid OR strategy_instance_id = :iid
            """,
            "DELETE FROM signals WHERE strategy_instance_id = :iid",
            "DELETE FROM portfolio_snapshots WHERE portfolio_id = :pid AND backtest_run_id IS NULL",
            "DELETE FROM backtest_runs WHERE portfolio_id = :pid",
            "DELETE FROM strategy_instances WHERE id = :iid OR portfolio_id = :pid",
            "DELETE FROM portfolios WHERE id = :pid",
        ]
        params = {"pid": LEGACY_PORTFOLIO, "iid": LEGACY_INSTANCE}
        deleted: dict[str, int] = {}
        for stmt in stmts:
            result = conn.execute(text(stmt), params)
            deleted[stmt.split()[2]] = result.rowcount or 0
        if backtest_ids:
            result = conn.execute(
                text("DELETE FROM portfolios WHERE id = ANY(CAST(:ids AS uuid[]))"),
                {"ids": backtest_ids},
            )
            deleted["ephemeral_backtest_portfolios"] = result.rowcount or 0
        conn.execute(text("ALTER TABLE trades ENABLE TRIGGER trades_no_delete"))
        conn.commit()
        report["deleted"] = deleted

        report["after"] = {
            "legacy_portfolios": _count(
                conn, "SELECT COUNT(*) FROM portfolios WHERE id = :id", id=LEGACY_PORTFOLIO
            ),
            "legacy_instances": _count(
                conn,
                "SELECT COUNT(*) FROM strategy_instances WHERE id = :id OR portfolio_id = :pid",
                id=LEGACY_INSTANCE,
                pid=LEGACY_PORTFOLIO,
            ),
            "legacy_positions": _count(
                conn, "SELECT COUNT(*) FROM positions WHERE portfolio_id = :pid", pid=LEGACY_PORTFOLIO
            ),
            "legacy_trades": _count(
                conn, "SELECT COUNT(*) FROM trades WHERE portfolio_id = :pid", pid=LEGACY_PORTFOLIO
            ),
            "legacy_intents": _count(
                conn,
                """
                SELECT COUNT(*) FROM order_intents
                WHERE portfolio_id = :pid OR strategy_instance_id = :iid
                """,
                pid=LEGACY_PORTFOLIO,
                iid=LEGACY_INSTANCE,
            ),
            "legacy_snapshots": _count(
                conn,
                "SELECT COUNT(*) FROM portfolio_snapshots WHERE portfolio_id = :pid",
                pid=LEGACY_PORTFOLIO,
            ),
            "legacy_decisions": _count(
                conn,
                "SELECT COUNT(*) FROM decisions WHERE strategy_instance_id = :iid",
                iid=LEGACY_INSTANCE,
            ),
            "legacy_signals": _count(
                conn,
                "SELECT COUNT(*) FROM signals WHERE strategy_instance_id = :iid",
                iid=LEGACY_INSTANCE,
            ),
        }

        active_after = conn.execute(
            text(
                """
                SELECT COUNT(DISTINCT p.id)::int,
                       COALESCE(SUM(p.equity), 0)::float,
                       COALESCE(SUM(p.balance), 0)::float,
                       COALESCE(SUM(p.unrealized_pnl), 0)::float,
                       (SELECT COUNT(*) FROM trades t
                        JOIN strategy_instances si ON si.id = t.strategy_instance_id
                        WHERE si.experiment_id = :exp AND t.backtest_run_id IS NULL)::int,
                       (SELECT COUNT(*) FROM positions pos
                        JOIN strategy_instances si ON si.id = pos.strategy_instance_id
                        WHERE si.experiment_id = :exp AND pos.status = 'open')::int
                FROM portfolios p
                JOIN strategy_instances si ON si.portfolio_id = p.id
                WHERE si.experiment_id = :exp
                """
            ),
            {"exp": ACTIVE_COMPETITION_EXPERIMENT_ID},
        ).one()
        report["active_after"] = {
            "portfolios": active_after[0],
            "combined_equity": float(active_after[1]),
            "combined_balance": float(active_after[2]),
            "combined_unrealized": float(active_after[3]),
            "closed_trades": int(active_after[4]),
            "open_positions": int(active_after[5]),
            "expected_initial": float(COMPETITION_TOTAL_INITIAL),
        }

    return report


def main() -> None:
    dry_run = "--dry-run" in sys.argv
    report = delete_legacy_paper_main(dry_run=dry_run)
    print("=== DELETE LEGACY PAPER MAIN ===")
    print(f"dry_run={dry_run}")
    print("before", report.get("before"))
    if not dry_run:
        print("after", report.get("after"))
        print("active_after", report.get("active_after"))


if __name__ == "__main__":
    main()
