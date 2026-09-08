#!/usr/bin/env python3
"""Void invalid BTC 15m catch-up Paper positions; preserve clean 5m positions."""

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
BTC_15M_PORTFOLIO_IDS = tuple(
    pid
    for pid, pdef in PORTFOLIO_DEF_BY_ID.items()
    if pdef.timeframe == "15m" and pid.startswith("00000000-0000-0000-0000-0000000012")
)

CANONICAL_BALANCE = Decimal("30200.38")
CANONICAL_REALIZED = Decimal("200.38")
CANONICAL_CLOSED_TRADES = 5
EXPECTED_OPEN_5M = 5


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
    open_by_tf = conn.execute(
        text(
            """
            SELECT si.timeframe, COUNT(*)::int
            FROM positions pos
            JOIN strategy_instances si ON si.portfolio_id = pos.portfolio_id
            WHERE si.experiment_id = :exp AND pos.status = 'open'
            GROUP BY si.timeframe
            """
        ),
        {"exp": ACTIVE_COMPETITION_EXPERIMENT_ID},
    ).all()
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
        "open_by_timeframe": {str(tf): int(cnt) for tf, cnt in open_by_tf},
        "closed_trades": int(closed[0] or 0),
        "realized_pnl": round(float(closed[1] or 0), 2),
    }


def _open_position_ids(conn, portfolio_ids: tuple[str, ...]) -> list[str]:
    rows = conn.execute(
        text(
            """
            SELECT id, portfolio_id FROM positions
            WHERE portfolio_id = ANY(CAST(:pids AS uuid[]))
              AND status = 'open'
              AND backtest_run_id IS NULL
            ORDER BY portfolio_id
            """
        ),
        {"pids": list(portfolio_ids)},
    ).all()
    return [str(r[0]) for r in rows]


def repair(*, dry_run: bool = True) -> dict:
    report: dict = {
        "dry_run": dry_run,
        "btc_5m_preserve": list(BTC_5M_PORTFOLIO_IDS),
        "btc_15m_void": list(BTC_15M_PORTFOLIO_IDS),
    }

    with engine.connect() as conn:
        report["before"] = _financial_summary(conn)
        report["open_5m_positions"] = _open_position_ids(conn, BTC_5M_PORTFOLIO_IDS)
        report["open_15m_positions"] = _open_position_ids(conn, BTC_15M_PORTFOLIO_IDS)

        if dry_run:
            return report

        conn.execute(text("ALTER TABLE trades DISABLE TRIGGER trades_no_delete"))

        position_ids = report["open_15m_positions"]
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
                text("DELETE FROM positions WHERE id = :pos_id"),
                {"pos_id": position_id},
            )

        for pid in BTC_15M_PORTFOLIO_IDS:
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

        conn.execute(text("ALTER TABLE trades ENABLE TRIGGER trades_no_delete"))
        conn.commit()

    with session_scope() as session:
        store = TradingStore(session)
        now = datetime.now(timezone.utc)
        peak_repair: list[dict] = []
        for pid in BTC_15M_PORTFOLIO_IDS:
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
        report["open_5m_positions_after"] = _open_position_ids(conn, BTC_5M_PORTFOLIO_IDS)
        report["open_15m_positions_after"] = _open_position_ids(conn, BTC_15M_PORTFOLIO_IDS)

    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Apply repair (default: dry run)")
    args = parser.parse_args()
    result = repair(dry_run=not args.apply)
    import json

    print(json.dumps(result, indent=2, default=str))
    if args.apply:
        after = result.get("after", {})
        ok = (
            after.get("combined_balance") == float(CANONICAL_BALANCE)
            and after.get("realized_pnl") == float(CANONICAL_REALIZED)
            and after.get("closed_trades") == CANONICAL_CLOSED_TRADES
            and after.get("open_by_timeframe", {}).get("5m") == EXPECTED_OPEN_5M
            and after.get("open_by_timeframe", {}).get("15m", 0) == 0
            and len(result.get("open_15m_positions_after", [])) == 0
            and len(result.get("open_5m_positions_after", [])) == EXPECTED_OPEN_5M
        )
        print("repair_ok", ok)


if __name__ == "__main__":
    main()
