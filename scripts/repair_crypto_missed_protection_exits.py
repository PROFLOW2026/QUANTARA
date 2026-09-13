#!/usr/bin/env python3
"""Recover Research BTC/ETH positions that missed SL/TP due to crypto_fast_protection failure.

Closes only when a completed 1m (preferred) or 5m candle proves the first trigger.
Uses the normal PaperBrokerAdapter exit model — never current market price.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "engine"))

from sqlalchemy import text  # noqa: E402

from quantara_engine.competition.constants import ACTIVE_COMPETITION_EXPERIMENT_ID  # noqa: E402
from quantara_engine.db.session import engine, session_scope  # noqa: E402
from quantara_engine.execution.crypto_missed_exit_recovery import (  # noqa: E402
    recover_missed_research_crypto_exits,
)
from quantara_engine.persistence.store import TradingStore  # noqa: E402


def _crypto_open_counts(conn) -> dict:
    rows = conn.execute(
        text(
            """
            SELECT UPPER(REPLACE(i.symbol, '/', '')) AS sym, COUNT(*)::int
            FROM positions pos
            JOIN instruments i ON i.id = pos.instrument_id
            WHERE pos.status = 'open'
              AND UPPER(REPLACE(i.symbol, '/', '')) IN ('BTCUSD', 'ETHUSD')
            GROUP BY 1
            """
        )
    ).all()
    out = {"BTCUSD": 0, "ETHUSD": 0}
    for sym, cnt in rows:
        out[str(sym)] = int(cnt)
    return out


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
    return {
        "portfolios": int(row[0] or 0),
        "combined_balance": round(float(row[1]), 2),
        "combined_equity": round(float(row[2]), 2),
        "unrealized_pnl": round(float(row[3]), 2),
        "open_positions": int(open_pos or 0),
        "closed_trades": int(closed[0] or 0),
        "realized_pnl": round(float(closed[1] or 0), 2),
    }


def _ledger_diff(conn) -> float:
    """Trade ledger vs portfolio balance consistency check (Research competition)."""
    row = conn.execute(
        text(
            """
            WITH trade_sum AS (
              SELECT t.portfolio_id, COALESCE(SUM(t.realized_pnl), 0) AS rpnl
              FROM trades t
              JOIN strategy_instances si ON si.id = t.strategy_instance_id
              WHERE si.experiment_id = :exp
              GROUP BY t.portfolio_id
            )
            SELECT COALESCE(SUM(
              ABS(
                (p.balance - p.initial_capital)
                - COALESCE(ts.rpnl, 0)
              )
            ), 0)::float
            FROM portfolios p
            JOIN strategy_instances si ON si.portfolio_id = p.id
            LEFT JOIN trade_sum ts ON ts.portfolio_id = p.id
            WHERE si.experiment_id = :exp
            """
        ),
        {"exp": ACTIVE_COMPETITION_EXPERIMENT_ID},
    ).scalar()
    return round(float(row or 0), 2)


def _duplicate_exits(conn) -> dict:
    multi_trades = conn.execute(
        text(
            """
            SELECT COUNT(*)::int FROM (
              SELECT t.position_id
              FROM trades t
              JOIN strategy_instances si ON si.id = t.strategy_instance_id
              WHERE si.experiment_id = :exp
              GROUP BY t.position_id
              HAVING COUNT(*) > 1
            ) x
            """
        ),
        {"exp": ACTIVE_COMPETITION_EXPERIMENT_ID},
    ).scalar()
    return {"duplicate_trades_on_position": int(multi_trades or 0)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Persist recovery closes")
    parser.add_argument(
        "--symbols",
        default="BTCUSD",
        help="Comma-separated symbols to recover (default BTCUSD only)",
    )
    args = parser.parse_args()
    symbols = {s.strip().upper().replace("/", "") for s in args.symbols.split(",") if s.strip()}

    report: dict = {
        "dry_run": not args.apply,
        "symbols": sorted(symbols),
    }
    with engine.connect() as conn:
        report["open_before"] = _crypto_open_counts(conn)
        report["financial_before"] = _financial_summary(conn)

    with session_scope() as session:
        store = TradingStore(session)
        recovery = recover_missed_research_crypto_exits(
            store,
            symbols=symbols,
            dry_run=not args.apply,
        )
        report["recovery"] = recovery

    with engine.connect() as conn:
        report["open_after"] = _crypto_open_counts(conn)
        report["financial_after"] = _financial_summary(conn)
        report["trade_ledger_diff"] = _ledger_diff(conn)
        report["duplicates"] = _duplicate_exits(conn)

    print(json.dumps(report, indent=2, default=str))


if __name__ == "__main__":
    main()
