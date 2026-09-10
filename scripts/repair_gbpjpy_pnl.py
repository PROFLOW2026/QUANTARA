#!/usr/bin/env python3
"""Repair GBPJPY open-position unrealized P&L after multi-currency fix."""

from __future__ import annotations

import argparse
import json
import sys
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "engine"))

from sqlalchemy import text  # noqa: E402

from quantara_engine.db.session import session_scope  # noqa: E402
from quantara_engine.persistence.batch_summary import batch_instruments_by_id, batch_latest_candle_closes
from quantara_engine.persistence.store import TradingStore  # noqa: E402
from quantara_engine.portfolio.currency import build_currency_context
from quantara_engine.portfolio.service import PortfolioState


def repair(*, apply: bool = False) -> dict:
    report: dict = {
        "dry_run": not apply,
        "gbpjpy_metadata_fixed": False,
        "positions_remarked": 0,
        "portfolios_synced": 0,
        "old_aggregate_unrealized": 0.0,
        "new_aggregate_unrealized": 0.0,
        "closed_gbpjpy_trades_found": 0,
        "jpy_per_usd": None,
        "details": [],
    }

    with session_scope() as session:
        store = TradingStore(session)
        report["gbpjpy_metadata_fixed"] = store.fix_gbpjpy_instrument_metadata()
        store.ensure_usdjpy_conversion_instrument()

        gbp = store.get_instrument_by_symbol("GBPJPY")
        if not gbp:
            report["error"] = "GBPJPY instrument not found"
            return report

        jpy_per_usd = float(store.resolve_jpy_per_usd())
        report["jpy_per_usd"] = jpy_per_usd

        closed = session.execute(
            text(
                """
                SELECT COUNT(*) FROM trades t
                JOIN instruments i ON i.id = t.instrument_id
                WHERE i.symbol = 'GBPJPY' AND t.backtest_run_id IS NULL
                """
            )
        ).scalar()
        report["closed_gbpjpy_trades_found"] = int(closed or 0)

        rows = session.execute(
            text(
                """
                SELECT p.id, p.portfolio_id, p.entry_price, p.current_price, p.quantity,
                       p.direction::text, p.unrealized_pnl, pf.name
                FROM positions p
                JOIN instruments i ON i.id = p.instrument_id
                JOIN portfolios pf ON pf.id = p.portfolio_id
                WHERE i.symbol = 'GBPJPY' AND p.status = 'open'
                """
            )
        ).mappings().all()

        if not rows:
            report["message"] = "No open GBPJPY positions"
            if apply:
                session.commit()
            return report

        instrument_ids = {gbp.id}
        instruments = batch_instruments_by_id(store, list(instrument_ids))
        currency_ctx = build_currency_context(store, instruments.values())

        marks = batch_latest_candle_closes(store, [gbp.id], "5m")
        mark_price = marks.get(gbp.id) or Decimal(str(rows[0]["current_price"]))

        portfolio_ids: set[str] = set()
        old_sum = Decimal("0")
        for row in rows:
            old_sum += Decimal(str(row["unrealized_pnl"]))

        mark_updates = []
        for row in rows:
            pos_domain = store.load_portfolio_state(str(row["portfolio_id"]))
            open_pos = next(p for p in pos_domain.open_positions() if p.id == str(row["id"]))
            state = PortfolioState(portfolio=pos_domain.portfolio, positions=[open_pos])
            state.recalculate_equity({gbp.id: mark_price}, currency_ctx)
            mark_updates.append((str(row["id"]), mark_price, open_pos.unrealized_pnl))
            portfolio_ids.add(str(row["portfolio_id"]))
            report["details"].append(
                {
                    "position_id": str(row["id"]),
                    "portfolio": row["name"],
                    "old_unrealized": float(row["unrealized_pnl"]),
                    "new_unrealized": float(open_pos.unrealized_pnl),
                }
            )

        new_sum = sum(Decimal(str(d["new_unrealized"])) for d in report["details"])
        report["old_aggregate_unrealized"] = float(old_sum)
        report["new_aggregate_unrealized"] = float(new_sum)
        report["positions_remarked"] = len(mark_updates)

        if apply:
            store.update_open_position_marks_batch(mark_updates)
            portfolios = []
            for pid in portfolio_ids:
                ps = store.load_portfolio_state(pid)
                ps.recalculate_equity({gbp.id: mark_price}, currency_ctx)
                portfolios.append(ps.portfolio)
            store.sync_portfolios_financial_state_from_ledger(portfolios, flush=True)
            report["portfolios_synced"] = len(portfolios)
            session.commit()

    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    print(json.dumps(repair(apply=args.apply), indent=2))


if __name__ == "__main__":
    main()
