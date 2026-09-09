#!/usr/bin/env python3
"""Repair five missed BTC 5m SL exits at first valid trigger candle."""

from __future__ import annotations

import argparse
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
    PORTFOLIO_DEF_BY_ID,
)
from quantara_engine.db.session import engine, session_scope  # noqa: E402
from quantara_engine.domain.types import (  # noqa: E402
    DecisionLogEntry,
    DecisionType,
    ExecutionAssumptions,
    ExitReason,
    new_id,
)
from quantara_engine.execution.exit_triggers import find_first_exit_candle  # noqa: E402
from quantara_engine.execution.paper_broker import PaperBrokerAdapter  # noqa: E402
from quantara_engine.persistence.store import TradingStore  # noqa: E402

BTC_5M_PORTFOLIO_IDS = tuple(
    pid
    for pid, pdef in PORTFOLIO_DEF_BY_ID.items()
    if pdef.timeframe == "5m" and pid.startswith("00000000-0000-0000-0000-0000000013")
)


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


def forensic(store: TradingStore) -> list[dict]:
    btc = store.get_instrument_by_symbol("BTCUSD")
    if not btc:
        raise RuntimeError("BTCUSD instrument missing")

    rows: list[dict] = []
    entries = {e["portfolio"].id: e for e in store.list_competition_entries()}

    for pid in BTC_5M_PORTFOLIO_IDS:
        entry = entries.get(pid)
        if not entry:
            continue
        state = store.load_portfolio_state(pid)
        open_positions = [
            p
            for p in state.open_positions()
            if p.strategy_instance_id == entry["instance"].id
        ]
        if not open_positions:
            rows.append({"portfolio_id": pid, "status": "no_open_position"})
            continue
        position = open_positions[0]
        candles = store.list_candles(btc.id, "5m", since=position.opened_at)
        hit = find_first_exit_candle(position, candles, after_timestamp=position.opened_at)
        row = {
            "portfolio_id": pid,
            "position_id": position.id,
            "risk_slug": PORTFOLIO_DEF_BY_ID[pid].risk_slug,
            "entry": float(position.entry_price),
            "sl": float(position.stop_loss),
            "tp": float(position.take_profit) if position.take_profit else None,
            "first_sl_touch": None,
            "first_tp_touch": None,
            "canonical_exit": None,
        }
        if hit:
            candle, reason, price = hit
            key = "first_sl_touch" if reason == ExitReason.SL else "first_tp_touch"
            touch = {
                "candle": candle.timestamp.isoformat(),
                "price": float(price),
                "reason": reason.value,
            }
            row[key] = touch
            row["canonical_exit"] = touch
        rows.append(row)
    return rows


def repair(*, dry_run: bool = True) -> dict:
    report: dict = {"dry_run": dry_run, "forensic": [], "repairs": []}

    with engine.connect() as conn:
        report["before"] = _financial_summary(conn)

    with session_scope() as session:
        store = TradingStore(session)
        report["forensic"] = forensic(store)

        btc = store.get_instrument_by_symbol("BTCUSD")
        broker = PaperBrokerAdapter(btc.id, ExecutionAssumptions())
        entries = {e["portfolio"].id: e for e in store.list_competition_entries()}

        for pid in BTC_5M_PORTFOLIO_IDS:
            entry = entries.get(pid)
            if not entry:
                continue
            state = store.load_portfolio_state(pid)
            open_positions = [
                p
                for p in state.open_positions()
                if p.strategy_instance_id == entry["instance"].id
            ]
            if not open_positions:
                report["repairs"].append({"portfolio_id": pid, "status": "already_closed"})
                continue

            position = open_positions[0]
            position.strategy_version_id = entry["instance"].strategy_version_id
            candles = store.list_candles(btc.id, "5m", since=position.opened_at)
            hit = find_first_exit_candle(position, candles, after_timestamp=position.opened_at)
            if not hit:
                report["repairs"].append({"portfolio_id": pid, "status": "no_trigger_found"})
                continue

            exit_candle, exit_reason, trigger_price = hit
            order, fill = broker.execute_exit_at_trigger(
                position.direction,
                position.quantity,
                exit_candle,
                trigger_price,
                state.portfolio.id,
            )

            if dry_run:
                from quantara_engine.portfolio.pnl import gross_pnl, net_pnl

                g = gross_pnl(
                    position.direction, position.entry_price, fill.fill_price, position.quantity
                )
                realized = net_pnl(g, position.entry_fees, fill.fees)
                report["repairs"].append(
                    {
                        "portfolio_id": pid,
                        "position_id": position.id,
                        "risk_slug": PORTFOLIO_DEF_BY_ID[pid].risk_slug,
                        "status": "would_close",
                        "exit_candle": exit_candle.timestamp.isoformat(),
                        "exit_price": float(fill.fill_price),
                        "exit_reason": exit_reason.value,
                        "realized_pnl": float(realized),
                    }
                )
                continue

            trade = state.close_position(position, fill, exit_reason, exit_candle.timestamp)
            store.persist_exit_execution(
                order=order,
                fill=fill,
                position=position,
                trade=trade,
                portfolio_state=state,
                strategy_instance_id=entry["instance"].id,
                filled_at=exit_candle.timestamp,
            )
            decision_type = (
                DecisionType.SL_TRIGGERED
                if exit_reason == ExitReason.SL
                else DecisionType.TP_TRIGGERED
            )
            store.save_decision(
                DecisionLogEntry(
                    id=new_id(),
                    strategy_instance_id=entry["instance"].id,
                    instrument_id=btc.id,
                    candle_timestamp=exit_candle.timestamp,
                    decision_type=decision_type,
                    message=f"Missed SL repair — {exit_reason.value} at {trigger_price}",
                    signal_id=None,
                    metadata={"repair": True, "missed_sl_incident": "2026-09-09"},
                )
            )
            snap = state.create_snapshot(exit_candle.timestamp)
            store.save_snapshot(snap)
            store.update_portfolio(state.portfolio)
            report["repairs"].append(
                {
                    "portfolio_id": pid,
                    "position_id": position.id,
                    "risk_slug": PORTFOLIO_DEF_BY_ID[pid].risk_slug,
                    "status": "closed",
                    "exit_candle": exit_candle.timestamp.isoformat(),
                    "exit_price": float(trade.exit_price),
                    "exit_reason": exit_reason.value,
                    "realized_pnl": float(trade.realized_pnl),
                }
            )

        for pid in BTC_5M_PORTFOLIO_IDS:
            state = store.load_portfolio_state(pid)
            if state.open_positions():
                continue
            state.portfolio.unrealized_pnl = Decimal("0")
            state.portfolio.equity = state.portfolio.balance
            state.portfolio.exposure_notional = Decimal("0")
            state.portfolio.reserved_capital = Decimal("0")
            store.update_portfolio(state.portfolio)

    with engine.connect() as conn:
        report["after"] = _financial_summary(conn)

    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--forensic-only", action="store_true")
    args = parser.parse_args()
    if args.forensic_only:
        with session_scope() as session:
            print(json.dumps({"forensic": forensic(TradingStore(session))}, indent=2))
        return
    result = repair(dry_run=not args.apply)
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
