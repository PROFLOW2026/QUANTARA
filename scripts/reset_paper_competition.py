#!/usr/bin/env python3
"""Hard reset active paper competition (160 portfolios) — delete all trading history."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "engine"))

from sqlalchemy import text  # noqa: E402

from quantara_engine.competition.constants import (  # noqa: E402
    ACTIVE_COMPETITION_EXPERIMENT_ID,
    ACTIVE_COMPETITION_PORTFOLIOS,
    COMPETITION_INITIAL_CAPITAL,
)
from quantara_engine.competition.orb_constants import (  # noqa: E402
    ORB_COMPETITION_EXPERIMENT_ID,
    ORB_COMPETITION_PORTFOLIOS,
)
from quantara_engine.db.session import engine  # noqa: E402

EXPERIMENT_IDS = (
    ACTIVE_COMPETITION_EXPERIMENT_ID,
    ORB_COMPETITION_EXPERIMENT_ID,
)
PORTFOLIO_IDS = [
    *[p.portfolio_id for p in ACTIVE_COMPETITION_PORTFOLIOS],
    *[p.portfolio_id for p in ORB_COMPETITION_PORTFOLIOS],
]
INITIAL = Decimal(str(COMPETITION_INITIAL_CAPITAL))


def _count(conn, sql: str, **params) -> int:
    return int(conn.execute(text(sql), params).scalar() or 0)


def reset_paper_competition(*, dry_run: bool = False) -> dict:
    reset_at = datetime.now(timezone.utc)
    report: dict = {"dry_run": dry_run, "reset_at": reset_at.isoformat()}

    with engine.connect() as conn:
        portfolio_ids = list(PORTFOLIO_IDS)
        instance_rows = conn.execute(
            text(
                """
                SELECT id::text, portfolio_id::text
                FROM strategy_instances
                WHERE portfolio_id = ANY(CAST(:pids AS uuid[]))
                """
            ),
            {"pids": portfolio_ids},
        ).all()
        instance_ids = [r[0] for r in instance_rows]

        report["portfolios"] = len(portfolio_ids)
        report["instances"] = len(instance_ids)

        report["before"] = {
            "open_positions": _count(
                conn,
                """
                SELECT COUNT(*) FROM positions
                WHERE portfolio_id = ANY(CAST(:pids AS uuid[]))
                  AND backtest_run_id IS NULL
                  AND status = 'open'
                """,
                pids=portfolio_ids,
            ),
            "closed_trades": _count(
                conn,
                """
                SELECT COUNT(*) FROM trades
                WHERE portfolio_id = ANY(CAST(:pids AS uuid[]))
                  AND backtest_run_id IS NULL
                """,
                pids=portfolio_ids,
            ),
            "snapshots": _count(
                conn,
                """
                SELECT COUNT(*) FROM portfolio_snapshots
                WHERE portfolio_id = ANY(CAST(:pids AS uuid[]))
                  AND backtest_run_id IS NULL
                """,
                pids=portfolio_ids,
            ),
            "decisions": _count(
                conn,
                """
                SELECT COUNT(*) FROM decisions
                WHERE strategy_instance_id = ANY(CAST(:iids AS uuid[]))
                """,
                iids=instance_ids,
            ),
            "order_intents": _count(
                conn,
                """
                SELECT COUNT(*) FROM order_intents
                WHERE portfolio_id = ANY(CAST(:pids AS uuid[]))
                  AND backtest_run_id IS NULL
                """,
                pids=portfolio_ids,
            ),
            "combined_balance": float(
                conn.execute(
                    text(
                        """
                        SELECT COALESCE(SUM(balance), 0)
                        FROM portfolios
                        WHERE id = ANY(CAST(:pids AS uuid[]))
                        """
                    ),
                    {"pids": portfolio_ids},
                ).scalar()
                or 0
            ),
        }

        if dry_run:
            return report

        conn.execute(text("ALTER TABLE trades DISABLE TRIGGER trades_no_delete"))

        delete_stmts = [
            (
                "fills",
                """
                DELETE FROM fills f
                USING orders o
                WHERE f.order_id = o.id
                  AND o.portfolio_id = ANY(CAST(:pids AS uuid[]))
                  AND o.backtest_run_id IS NULL
                """,
            ),
            (
                "orders",
                """
                DELETE FROM orders
                WHERE portfolio_id = ANY(CAST(:pids AS uuid[]))
                  AND backtest_run_id IS NULL
                """,
            ),
            (
                "trades",
                """
                DELETE FROM trades
                WHERE portfolio_id = ANY(CAST(:pids AS uuid[]))
                  AND backtest_run_id IS NULL
                """,
            ),
            (
                "positions",
                """
                DELETE FROM positions
                WHERE portfolio_id = ANY(CAST(:pids AS uuid[]))
                  AND backtest_run_id IS NULL
                """,
            ),
            (
                "signals",
                """
                DELETE FROM signals
                WHERE strategy_instance_id = ANY(CAST(:iids AS uuid[]))
                  AND backtest_run_id IS NULL
                """,
            ),
            (
                "decisions",
                """
                DELETE FROM decisions
                WHERE strategy_instance_id = ANY(CAST(:iids AS uuid[]))
                  AND backtest_run_id IS NULL
                """,
            ),
            (
                "order_intents",
                """
                DELETE FROM order_intents
                WHERE portfolio_id = ANY(CAST(:pids AS uuid[]))
                  AND backtest_run_id IS NULL
                """,
            ),
            (
                "portfolio_snapshots",
                """
                DELETE FROM portfolio_snapshots
                WHERE portfolio_id = ANY(CAST(:pids AS uuid[]))
                  AND backtest_run_id IS NULL
                """,
            ),
        ]

        deleted: dict[str, int] = {}
        params = {"pids": portfolio_ids, "iids": instance_ids}
        for name, stmt in delete_stmts:
            result = conn.execute(text(stmt), params)
            deleted[name] = result.rowcount or 0

        reset_portfolios = conn.execute(
            text(
                """
                UPDATE portfolios
                SET balance = initial_capital,
                    equity = initial_capital,
                    unrealized_pnl = 0,
                    exposure_notional = 0,
                    reserved_capital = 0,
                    peak_equity = initial_capital,
                    status = 'active',
                    halt_reason = NULL,
                    updated_at = NOW()
                WHERE id = ANY(CAST(:pids AS uuid[]))
                """
            ),
            {"pids": portfolio_ids},
        )
        deleted["portfolios_reset"] = reset_portfolios.rowcount or 0

        conn.execute(
            text(
                """
                UPDATE experiments
                SET start_date = :reset_at
                WHERE id = ANY(CAST(:exp_ids AS uuid[]))
                """
            ),
            {"reset_at": reset_at, "exp_ids": list(EXPERIMENT_IDS)},
        )

        settings_updates = {
            "competition_started_at": reset_at.isoformat(),
            "position_management_cursors": {},
        }
        for key, value in settings_updates.items():
            conn.execute(
                text(
                    """
                    INSERT INTO settings (key, value, updated_at)
                    VALUES (:key, CAST(:value AS jsonb), NOW())
                    ON CONFLICT (key) DO UPDATE
                    SET value = EXCLUDED.value, updated_at = NOW()
                    """
                ),
                {"key": key, "value": json.dumps(value)},
            )

        conn.execute(text("ALTER TABLE trades ENABLE TRIGGER trades_no_delete"))
        conn.commit()
        report["deleted"] = deleted

        after = conn.execute(
            text(
                """
                SELECT COUNT(DISTINCT p.id)::int,
                       COALESCE(SUM(p.initial_capital), 0)::float,
                       COALESCE(SUM(p.balance), 0)::float,
                       COALESCE(SUM(p.equity), 0)::float,
                       COALESCE(SUM(p.unrealized_pnl), 0)::float,
                       (SELECT COUNT(*) FROM trades t
                        WHERE t.portfolio_id = ANY(CAST(:pids AS uuid[]))
                          AND t.backtest_run_id IS NULL)::int,
                       (SELECT COUNT(*) FROM positions pos
                        WHERE pos.portfolio_id = ANY(CAST(:pids AS uuid[]))
                          AND pos.backtest_run_id IS NULL
                          AND pos.status = 'open')::int,
                       (SELECT COUNT(*) FROM portfolio_snapshots ps
                        WHERE ps.portfolio_id = ANY(CAST(:pids AS uuid[]))
                          AND ps.backtest_run_id IS NULL)::int
                FROM portfolios p
                WHERE p.id = ANY(CAST(:pids AS uuid[]))
                """
            ),
            {"pids": portfolio_ids},
        ).one()
        report["after"] = {
            "portfolios": int(after[0]),
            "initial_capital": float(after[1]),
            "balance": float(after[2]),
            "equity": float(after[3]),
            "unrealized": float(after[4]),
            "closed_trades": int(after[5]),
            "open_positions": int(after[6]),
            "snapshots": int(after[7]),
        }

    return report


def main() -> None:
    dry_run = "--dry-run" in sys.argv
    report = reset_paper_competition(dry_run=dry_run)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
