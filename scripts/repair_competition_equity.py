#!/usr/bin/env python3
"""One-time repair for competition equity corrupted by stale snapshot mark prices.

Background: snapshot_job previously used list_candles(..., limit=1), which returned
the oldest 1h candle (~$2,636) instead of the latest mark. That inflated negative
unrealized P&L on open competition positions while balance stayed at $2,000.

Usage (owner / ops, after fixing snapshot.py and restarting workers):
  python scripts/repair_competition_equity.py --dry-run
  python scripts/repair_competition_equity.py

Recalculates all 15 competition portfolios from live positions + recent mark,
updates open position marks, and deletes invalid snapshot rows from the bad window.
Safe to re-run: idempotent when DB is already healthy.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
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
from quantara_engine.db.session import engine, session_scope  # noqa: E402
from quantara_engine.persistence.store import TradingStore  # noqa: E402


def _competition_start(conn) -> datetime:
    raw = conn.execute(
        text("SELECT value::text FROM settings WHERE key = 'competition_started_at'")
    ).scalar()
    value = json.loads(raw) if raw else None
    if not value:
        raise RuntimeError("competition_started_at missing in settings")
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _count_invalid_snapshots(conn, started_at: datetime) -> int:
    result = conn.execute(
        text(
            """
            SELECT COUNT(*)
            FROM portfolio_snapshots ps
            JOIN portfolios p ON p.id = ps.portfolio_id
            JOIN strategy_instances si ON si.portfolio_id = p.id
            WHERE si.experiment_id = :exp
              AND ps.timestamp >= :started_at
              AND (
                (ps.open_positions_count = 0 AND ps.equity <> p.initial_capital)
                OR (ps.open_positions_count > 0 AND ps.unrealized_pnl <= -1000)
              )
            """
        ),
        {"exp": ACTIVE_COMPETITION_EXPERIMENT_ID, "started_at": started_at},
    ).scalar()
    return int(result or 0)


def repair(*, dry_run: bool = False) -> dict:
    portfolio_ids = [p.portfolio_id for p in ACTIVE_COMPETITION_PORTFOLIOS]
    report: dict = {"repaired_portfolios": [], "invalid_snapshots_deleted": 0}

    with engine.connect() as conn:
        started_at = _competition_start(conn)
        invalid_before = _count_invalid_snapshots(conn, started_at)
        report["invalid_snapshots_before"] = invalid_before

    with session_scope() as session:
        store = TradingStore(session)
        instrument = store.get_instrument_by_symbol("XAUUSD")
        if not instrument:
            raise RuntimeError("XAUUSD instrument missing")

        candles = store.list_recent_candles(instrument.id, "5m", limit=1)
        if not candles:
            candles = store.list_recent_candles(instrument.id, "1h", limit=1)
        if not candles:
            raise RuntimeError("No recent candles available for mark price")
        mark = candles[-1].close

        for entry in ACTIVE_COMPETITION_PORTFOLIOS:
            pid = entry.portfolio_id
            state = store.load_portfolio_state(pid)
            before = {
                "balance": float(state.portfolio.balance),
                "equity": float(state.portfolio.equity),
                "unrealized_pnl": float(state.portfolio.unrealized_pnl),
                "open_positions": len(state.open_positions()),
                "closed_trades": len(state.trades),
            }

            if before["closed_trades"] == 0 and before["open_positions"] == 0:
                state.portfolio.balance = COMPETITION_INITIAL_CAPITAL
                state.portfolio.unrealized_pnl = Decimal("0")
                state.portfolio.equity = COMPETITION_INITIAL_CAPITAL
                state.portfolio.exposure_notional = Decimal("0")
                state.portfolio.reserved_capital = Decimal("0")
                if state.portfolio.peak_equity < COMPETITION_INITIAL_CAPITAL:
                    state.portfolio.peak_equity = COMPETITION_INITIAL_CAPITAL
            else:
                state.recalculate_equity(mark)

            if not dry_run:
                store.update_portfolio(state.portfolio)
                for pos in state.open_positions():
                    store.update_open_position_mark(
                        pos.id, pos.current_price, pos.unrealized_pnl
                    )

            after = {
                "balance": float(state.portfolio.balance),
                "equity": float(state.portfolio.equity),
                "unrealized_pnl": float(state.portfolio.unrealized_pnl),
                "open_positions": len(state.open_positions()),
                "closed_trades": len(state.trades),
            }
            report["repaired_portfolios"].append(
                {
                    "portfolio_id": pid,
                    "timeframe": entry.timeframe,
                    "risk_slug": entry.risk_slug,
                    "before": before,
                    "after": after,
                }
            )

        if not dry_run:
            deleted = session.execute(
                text(
                    """
                    DELETE FROM portfolio_snapshots ps
                    USING portfolios p, strategy_instances si
                    WHERE ps.portfolio_id = p.id
                      AND si.portfolio_id = p.id
                      AND si.experiment_id = :exp
                      AND ps.timestamp >= :started_at
                      AND (
                        (ps.open_positions_count = 0 AND ps.equity <> p.initial_capital)
                        OR (ps.open_positions_count > 0 AND ps.unrealized_pnl <= -1000)
                      )
                    """
                ),
                {"exp": ACTIVE_COMPETITION_EXPERIMENT_ID, "started_at": started_at},
            )
            report["invalid_snapshots_deleted"] = deleted.rowcount or 0

    with engine.connect() as conn:
        report["invalid_snapshots_after"] = _count_invalid_snapshots(conn, started_at)
        combined = conn.execute(
            text(
                """
                SELECT COALESCE(SUM(p.equity), 0)::float
                FROM portfolios p
                JOIN strategy_instances si ON si.portfolio_id = p.id
                WHERE si.experiment_id = :exp
                """
            ),
            {"exp": ACTIVE_COMPETITION_EXPERIMENT_ID},
        ).scalar()
        report["combined_equity"] = float(combined or 0)

    return report


def main() -> None:
    dry_run = "--dry-run" in sys.argv
    report = repair(dry_run=dry_run)
    print("=== REPAIR REPORT ===")
    print(f"dry_run={dry_run}")
    print(f"mark_used=via list_recent_candles")
    print(f"invalid_snapshots_before={report['invalid_snapshots_before']}")
    print(f"invalid_snapshots_deleted={report.get('invalid_snapshots_deleted', 0)}")
    print(f"invalid_snapshots_after={report['invalid_snapshots_after']}")
    print(f"combined_equity={report['combined_equity']:.2f}")
    for row in report["repaired_portfolios"]:
        if row["timeframe"] == "5m":
            print(
                f"{row['risk_slug']:18} equity {row['before']['equity']:.2f} -> {row['after']['equity']:.2f} "
                f"(open={row['after']['open_positions']} trades={row['after']['closed_trades']})"
            )


if __name__ == "__main__":
    main()
