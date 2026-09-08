"""Repair missed paper exits and clean stale competition artifacts."""

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
    COMPETITION_TOTAL_INITIAL,
    RISK_TIERS,
)
from quantara_engine.core.clock import BacktestClock  # noqa: E402
from quantara_engine.db.session import engine, session_scope  # noqa: E402
from quantara_engine.domain.types import (  # noqa: E402
    DecisionLogEntry,
    DecisionType,
    ExecutionAssumptions,
    Mode,
    new_id,
)
from quantara_engine.execution.exit_triggers import find_first_exit_candle  # noqa: E402
from quantara_engine.execution.paper_broker import PaperBrokerAdapter  # noqa: E402
from quantara_engine.persistence.store import TradingStore  # noqa: E402


def _competition_start(conn) -> datetime:
    raw = conn.execute(
        text("SELECT value::text FROM settings WHERE key = 'competition_started_at'")
    ).scalar()
    return datetime.fromisoformat(json.loads(raw).replace("Z", "+00:00"))


def repair_missed_exits(store: TradingStore, *, dry_run: bool = False) -> list[dict]:
    """Close open competition positions at first missed SL/TP candle."""
    instrument = store.get_instrument_by_symbol("XAUUSD")
    if not instrument:
        raise RuntimeError("XAUUSD missing")

    broker = PaperBrokerAdapter(instrument.id, ExecutionAssumptions())
    results: list[dict] = []

    entries_by_instance = {
        e["instance"].id: e["instance"] for e in store.list_competition_entries()
    }

    for entry in ACTIVE_COMPETITION_PORTFOLIOS:
        if entry.timeframe != "5m":
            continue
        state = store.load_portfolio_state(entry.portfolio_id)
        open_positions = [
            p for p in state.open_positions() if p.strategy_instance_id == entry.instance_id
        ]
        if not open_positions:
            continue

        position = open_positions[0]
        instance = entries_by_instance.get(entry.instance_id)
        if instance:
            position.strategy_version_id = instance.strategy_version_id
        candles = store.list_candles(instrument.id, "5m")
        candles = sorted(candles, key=lambda c: c.timestamp)
        hit = find_first_exit_candle(position, candles, after_timestamp=position.opened_at)
        if not hit:
            results.append(
                {
                    "risk_slug": entry.risk_slug,
                    "status": "no_trigger_found",
                    "portfolio_id": entry.portfolio_id,
                }
            )
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
            results.append(
                {
                    "risk_slug": entry.risk_slug,
                    "status": "would_close",
                    "entry_price": float(position.entry_price),
                    "exit_price": float(fill.fill_price),
                    "exit_time": exit_candle.timestamp.isoformat(),
                    "exit_reason": exit_reason.value,
                    "realized_pnl": float(realized),
                    "quantity": float(position.quantity),
                }
            )
            continue

        trade = state.close_position(position, fill, exit_reason, exit_candle.timestamp)
        decision_type = (
            DecisionType.SL_TRIGGERED if exit_reason.value == "sl" else DecisionType.TP_TRIGGERED
        )
        store.persist_exit_execution(
            order=order,
            fill=fill,
            position=position,
            trade=trade,
            portfolio_state=state,
            strategy_instance_id=entry.instance_id,
            filled_at=exit_candle.timestamp,
        )
        store.save_decision(
            DecisionLogEntry(
                id=new_id(),
                strategy_instance_id=entry.instance_id,
                instrument_id=instrument.id,
                candle_timestamp=exit_candle.timestamp,
                decision_type=decision_type,
                message=f"Missed exit repaired — {exit_reason.value} at {trigger_price}",
                signal_id=None,
                metadata={"repair": True},
            )
        )
        results.append(
            {
                "risk_slug": entry.risk_slug,
                "status": "closed",
                "entry_price": float(trade.entry_price),
                "exit_price": float(trade.exit_price),
                "exit_time": exit_candle.timestamp.isoformat(),
                "exit_reason": exit_reason.value,
                "realized_pnl": float(trade.realized_pnl),
                "quantity": float(trade.quantity),
            }
        )

    return results


def reconcile_portfolio_balances(store: TradingStore) -> list[dict]:
    """Set balance/equity = initial + sum(realized) for competition portfolios."""
    from quantara_engine.competition.constants import COMPETITION_INITIAL_CAPITAL

    rows: list[dict] = []
    for entry in ACTIVE_COMPETITION_PORTFOLIOS:
        state = store.load_portfolio_state(entry.portfolio_id)
        realized = store.sum_realized_pnl(entry.portfolio_id)
        expected_balance = COMPETITION_INITIAL_CAPITAL + Decimal(str(realized))
        before = float(state.portfolio.balance)
        state.portfolio.balance = expected_balance.quantize(Decimal("0.01"))
        state.portfolio.unrealized_pnl = Decimal("0")
        state.portfolio.equity = state.portfolio.balance
        state.portfolio.exposure_notional = Decimal("0")
        store.update_portfolio(state.portfolio)
        rows.append(
            {
                "risk_slug": entry.risk_slug,
                "timeframe": entry.timeframe,
                "before_balance": before,
                "after_balance": float(state.portfolio.balance),
                "realized_pnl": float(realized),
            }
        )
    return rows


def repair_all(*, dry_run: bool = False) -> dict:
    report: dict = {"dry_run": dry_run}

    with engine.connect() as conn:
        started_at = _competition_start(conn)
        report["experiment_started_at"] = started_at.isoformat()
        pending_before = conn.execute(
            text(
                """
                SELECT COUNT(*) FROM order_intents oi
                JOIN strategy_instances si ON si.id = oi.strategy_instance_id
                WHERE si.experiment_id = :exp
                  AND oi.status = 'pending_execution'
                  AND oi.backtest_run_id IS NULL
                """
            ),
            {"exp": ACTIVE_COMPETITION_EXPERIMENT_ID},
        ).scalar()
        report["pending_intents_before"] = int(pending_before or 0)

    with session_scope() as session:
        store = TradingStore(session)
        report["missed_exits"] = repair_missed_exits(store, dry_run=dry_run)
        if not dry_run:
            dup = store.cleanup_duplicate_pending_intents(ACTIVE_COMPETITION_EXPERIMENT_ID)
            report["duplicate_cleanup"] = dup
            stale = store.cancel_stale_pending_intents(
                ACTIVE_COMPETITION_EXPERIMENT_ID, datetime.now(timezone.utc)
            )
            report["stale_intents_cancelled"] = stale
            deleted = store.delete_invalid_competition_snapshots(
                ACTIVE_COMPETITION_EXPERIMENT_ID, started_at
            )
            report["invalid_snapshots_deleted"] = deleted
            report["balance_reconciliation"] = reconcile_portfolio_balances(store)

    with engine.connect() as conn:
        pending_after = conn.execute(
            text(
                """
                SELECT COUNT(*) FROM order_intents oi
                JOIN strategy_instances si ON si.id = oi.strategy_instance_id
                WHERE si.experiment_id = :exp
                  AND oi.status = 'pending_execution'
                  AND oi.backtest_run_id IS NULL
                """
            ),
            {"exp": ACTIVE_COMPETITION_EXPERIMENT_ID},
        ).scalar()
        report["pending_intents_after"] = int(pending_after or 0)
        combined = conn.execute(
            text(
                """
                SELECT COALESCE(SUM(p.equity), 0)::float,
                       COALESCE(SUM(p.balance), 0)::float,
                       COALESCE(SUM(p.unrealized_pnl), 0)::float
                FROM portfolios p
                JOIN strategy_instances si ON si.portfolio_id = p.id
                WHERE si.experiment_id = :exp
                """
            ),
            {"exp": ACTIVE_COMPETITION_EXPERIMENT_ID},
        ).one()
        report["combined_equity"] = float(combined[0])
        report["combined_balance"] = float(combined[1])
        report["combined_unrealized"] = float(combined[2])
        report["combined_realized"] = report["combined_equity"] - report["combined_unrealized"]
        report["combined_total_pnl"] = report["combined_equity"] - float(COMPETITION_TOTAL_INITIAL)

    return report


def main() -> None:
    dry_run = "--dry-run" in sys.argv
    report = repair_all(dry_run=dry_run)
    print("=== PAPER EXECUTION REPAIR ===")
    print(f"dry_run={dry_run}")
    print(f"pending_intents: {report['pending_intents_before']} -> {report.get('pending_intents_after')}")
    print(f"combined_equity={report.get('combined_equity', 0):.2f}")
    print(f"combined_realized_pnl={report.get('combined_realized', 0) - 30000 + report.get('combined_unrealized', 0):.2f}")
    print(f"combined_total_pnl={report.get('combined_total_pnl', 0):.2f}")
    print(f"invalid_snapshots_deleted={report.get('invalid_snapshots_deleted', 0)}")
    for row in report.get("missed_exits", []):
        slug = row.get("risk_slug", "?")
        if row.get("status") == "closed":
            print(
                f"{slug:18} entry={row['entry_price']:.2f} exit={row['exit_price']:.2f} "
                f"reason={row['exit_reason']} pnl={row['realized_pnl']:.2f} @ {row['exit_time']}"
            )
        else:
            print(f"{slug:18} {row.get('status')}")


if __name__ == "__main__":
    main()
