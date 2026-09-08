#!/usr/bin/env python3
"""Focused live verification for unattended test readiness."""

from __future__ import annotations

import json
import sys
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "engine"))

from sqlalchemy import text  # noqa: E402

from quantara_engine.competition.constants import (  # noqa: E402
    ACTIVE_COMPETITION_EXPERIMENT_ID,
    COMPETITION_TOTAL_INITIAL,
)
from quantara_engine.db.session import engine  # noqa: E402
from quantara_engine.db.session import session_scope  # noqa: E402
from quantara_engine.persistence.store import TradingStore  # noqa: E402

EXP = ACTIVE_COMPETITION_EXPERIMENT_ID
LEGACY_PORTFOLIO = "00000000-0000-0000-0000-000000000010"


def _money(value) -> float:
    return round(float(value or 0), 2)


def verify_db() -> dict:
    report: dict = {"checks": [], "timeframes": {}, "portfolios": []}

    with engine.connect() as conn:
        legacy = conn.execute(
            text("SELECT COUNT(*) FROM portfolios WHERE id = :id"),
            {"id": LEGACY_PORTFOLIO},
        ).scalar()
        report["legacy_portfolios"] = int(legacy or 0)

        pending = conn.execute(
            text(
                """
                SELECT COUNT(*) FROM order_intents oi
                JOIN strategy_instances si ON si.id = oi.strategy_instance_id
                WHERE si.experiment_id = :exp
                  AND oi.status = 'pending_execution'
                """
            ),
            {"exp": EXP},
        ).scalar()
        report["pending_intents"] = int(pending or 0)

        dupes = conn.execute(
            text(
                """
                SELECT COUNT(*) FROM (
                  SELECT idempotency_key, COUNT(*) AS c
                  FROM order_intents oi
                  JOIN strategy_instances si ON si.id = oi.strategy_instance_id
                  WHERE si.experiment_id = :exp
                  GROUP BY idempotency_key
                  HAVING COUNT(*) > 1
                ) d
                """
            ),
            {"exp": EXP},
        ).scalar()
        report["duplicate_intent_keys"] = int(dupes or 0)

        rows = conn.execute(
            text(
                """
                SELECT p.id, si.timeframe, rp.slug,
                       p.initial_capital, p.balance, p.equity, p.unrealized_pnl,
                       si.id AS instance_id, si.is_active,
                       (SELECT COUNT(*) FROM trades t
                        WHERE t.portfolio_id = p.id AND t.backtest_run_id IS NULL) AS closed,
                       (SELECT COUNT(*) FROM positions pos
                        WHERE pos.portfolio_id = p.id AND pos.status = 'open') AS open_pos,
                       (SELECT COALESCE(SUM(t.realized_pnl), 0) FROM trades t
                        WHERE t.portfolio_id = p.id AND t.backtest_run_id IS NULL) AS realized
                FROM portfolios p
                JOIN strategy_instances si ON si.portfolio_id = p.id
                JOIN risk_profiles rp ON rp.id = si.risk_profile_id
                WHERE si.experiment_id = :exp AND si.is_active = true
                ORDER BY si.timeframe, rp.slug
                """
            ),
            {"exp": EXP},
        ).all()

        report["portfolio_count"] = len(rows)
        report["active_instances"] = len(rows)

        combined_equity = Decimal("0")
        combined_balance = Decimal("0")
        combined_unrealized = Decimal("0")
        combined_realized = Decimal("0")
        closed_total = 0
        open_total = 0
        tf_stats: dict[str, dict] = {}

        for row in rows:
            pid, tf, risk, init, bal, eq, unreal, iid, active, closed, open_pos, realized = row
            init = Decimal(str(init))
            bal = Decimal(str(bal))
            eq = Decimal(str(eq))
            unreal = Decimal(str(unreal))
            realized = Decimal(str(realized))
            expected_bal = init + realized
            bal_ok = abs(bal - expected_bal) <= Decimal("0.02")
            eq_ok = abs(eq - (bal + unreal)) <= Decimal("0.02")

            report["portfolios"].append(
                {
                    "id": str(pid),
                    "timeframe": tf,
                    "risk": risk,
                    "initial": _money(init),
                    "balance": _money(bal),
                    "equity": _money(eq),
                    "realized": _money(realized),
                    "unrealized": _money(unreal),
                    "closed_trades": int(closed),
                    "open_positions": int(open_pos),
                    "balance_reconciles": bal_ok,
                    "equity_reconciles": eq_ok,
                    "instance_active": bool(active),
                }
            )

            combined_equity += eq
            combined_balance += bal
            combined_unrealized += unreal
            combined_realized += realized
            closed_total += int(closed)
            open_total += int(open_pos)

            bucket = tf_stats.setdefault(
                tf,
                {
                    "closed_trades": 0,
                    "open_positions": 0,
                    "realized_pnl": Decimal("0"),
                    "equity": Decimal("0"),
                },
            )
            bucket["closed_trades"] += int(closed)
            bucket["open_positions"] += int(open_pos)
            bucket["realized_pnl"] += realized
            bucket["equity"] += eq

        experiments = conn.execute(
            text(
                """
                SELECT COUNT(*) FROM experiments e
                WHERE e.status = 'running'
                  AND EXISTS (
                    SELECT 1 FROM strategy_instances si
                    WHERE si.experiment_id = e.id AND si.is_active = true
                  )
                """
            ),
        ).scalar()

        report["active_experiments"] = int(experiments or 0)
        report["combined_equity"] = _money(combined_equity)
        report["combined_balance"] = _money(combined_balance)
        report["combined_unrealized"] = _money(combined_unrealized)
        report["combined_realized"] = _money(combined_realized)
        report["combined_pnl"] = _money(combined_equity - COMPETITION_TOTAL_INITIAL)
        report["closed_trades"] = closed_total
        report["open_positions"] = open_total
        report["initial_capital"] = _money(COMPETITION_TOTAL_INITIAL)

        for tf, stats in tf_stats.items():
            report["timeframes"][tf] = {
                "closed_trades": stats["closed_trades"],
                "open_positions": stats["open_positions"],
                "realized_pnl": _money(stats["realized_pnl"]),
                "equity": _money(stats["equity"]),
            }

        pnl_identity = abs(
            combined_equity - COMPETITION_TOTAL_INITIAL - combined_realized - combined_unrealized
        ) <= Decimal("0.05")
        report["financial_reconciliation"] = (
            report["portfolio_count"] == 15
            and report["active_instances"] == 15
            and report["active_experiments"] == 1
            and report["legacy_portfolios"] == 0
            and pnl_identity
            and all(p["balance_reconciles"] and p["equity_reconciles"] for p in report["portfolios"])
        )

    with session_scope() as session:
        store = TradingStore(session)
        instrument = store.get_instrument_by_symbol("XAUUSD")
        now = __import__("datetime").datetime.now(__import__("datetime").timezone.utc)
        tf_status = {}
        backlog_total = 0
        if instrument:
            by_tf: dict[str, list[str]] = {}
            for entry in store.list_competition_entries():
                by_tf.setdefault(entry["instance"].timeframe, []).append(entry["instance"].id)
            for tf in ("5m", "15m", "1h"):
                instance_ids = by_tf.get(tf, [])
                if not instance_ids:
                    continue
                status = store.get_timeframe_execution_status(
                    instrument.id, tf, instance_ids, now
                )
                tf_status[tf] = status
                backlog_total += int(status.get("backlog", 0) or 0)
        report["timeframe_execution"] = tf_status
        report["backlog_total"] = backlog_total

    return report


def main() -> None:
    report = verify_db()
    print(json.dumps(report, indent=2, default=str))
    ok = report.get("financial_reconciliation") and report.get("legacy_portfolios") == 0
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
