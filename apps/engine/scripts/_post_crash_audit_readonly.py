"""Read-only post-crash audit — DO NOT modify data."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import func, select, text

from quantara_engine.db.session import session_scope
from quantara_engine.market_data.polling import STRATEGY_MIN_CANDLES, is_market_data_fresh
from quantara_engine.market_data.registry import list_target_assets
from quantara_engine.persistence.store import TradingStore

NOW = datetime.now(timezone.utc)
HOURS_24 = NOW - timedelta(hours=24)
HOURS_6 = NOW - timedelta(hours=6)


def _dec(v) -> Decimal:
    if v is None:
        return Decimal("0")
    return Decimal(str(v))


def main() -> None:
    out: dict = {}

    with session_scope() as session:
        store = TradingStore(session)

        # Settings / worker status
        settings = store.get_settings_dict()
        out["worker_status"] = {
            k.replace("worker_status:", ""): v
            for k, v in settings.items()
            if k.startswith("worker_status:")
        }
        out["scheduler_events"] = settings.get("worker_status:scheduler_events", [])[-60:]

        # Portfolio counts
        robot_a, robot_b, combined = store.list_all_competition_entries()
        out["robot_a_portfolios"] = len(robot_a)
        out["robot_b_portfolios"] = len(robot_b)
        out["total_competition_entries"] = len(combined)

        # Financial aggregates via SQL for speed
        fin = session.execute(
            text(
                """
                SELECT
                  COUNT(*) AS active_portfolios,
                  COALESCE(SUM(initial_capital), 0) AS initial_capital,
                  COALESCE(SUM(balance), 0) AS total_balance,
                  COALESCE(SUM(equity), 0) AS total_equity,
                  COALESCE(SUM(unrealized_pnl), 0) AS total_unrealized
                FROM portfolios
                WHERE mode = 'paper' AND status = 'active'
                """
            )
        ).mappings().first()
        out["financial"] = {k: str(v) for k, v in fin.items()}

        realized = session.execute(
            text(
                """
                SELECT COALESCE(SUM(realized_pnl), 0) AS realized_pnl, COUNT(*) AS closed_trades
                FROM trades
                WHERE mode = 'paper'
                """
            )
        ).mappings().first()
        out["realized"] = {k: str(v) for k, v in realized.items()}

        open_pos = session.execute(
            text(
                """
                SELECT COUNT(*) AS open_positions
                FROM positions
                WHERE status = 'open' AND mode = 'paper'
                """
            )
        ).scalar()
        out["open_positions"] = open_pos

        counts = session.execute(
            text(
                """
                SELECT
                  (SELECT COUNT(*) FROM signals WHERE mode = 'paper') AS signals,
                  (SELECT COUNT(*) FROM order_intents WHERE mode = 'paper') AS intents,
                  (SELECT COUNT(*) FROM fills WHERE mode = 'paper') AS fills,
                  (SELECT COUNT(*) FROM portfolio_snapshots WHERE mode = 'paper') AS snapshots
                """
            )
        ).mappings().first()
        out["counts"] = {k: int(v) for k, v in counts.items()}

        recent = session.execute(
            text(
                """
                SELECT
                  (SELECT COUNT(*) FROM signals WHERE mode='paper' AND created_at >= :since) AS signals_24h,
                  (SELECT COUNT(*) FROM order_intents WHERE mode='paper' AND created_at >= :since) AS intents_24h,
                  (SELECT COUNT(*) FROM fills WHERE mode='paper' AND created_at >= :since) AS fills_24h,
                  (SELECT COUNT(*) FROM trades WHERE mode='paper' AND closed_at >= :since) AS trades_24h,
                  (SELECT COUNT(*) FROM decisions WHERE created_at >= :since) AS decisions_24h,
                  (SELECT COUNT(*) FROM worker_runs WHERE started_at >= :since) AS worker_runs_24h
                """
            ),
            {"since": HOURS_24},
        ).mappings().first()
        out["recent_24h"] = {k: int(v) for k, v in recent.items()}

        # Duplicate detection
        dup_fills = session.execute(
            text(
                """
                SELECT order_id, COUNT(*) AS c
                FROM fills
                WHERE mode = 'paper'
                GROUP BY order_id
                HAVING COUNT(*) > 1
                LIMIT 20
                """
            )
        ).fetchall()
        out["duplicate_fills_by_order"] = len(dup_fills)

        dup_intents = session.execute(
            text(
                """
                SELECT portfolio_id, instrument_id, execution_candle_timestamp, direction, COUNT(*) AS c
                FROM order_intents
                WHERE mode = 'paper' AND status IN ('pending_execution','filled','partially_filled')
                GROUP BY portfolio_id, instrument_id, execution_candle_timestamp, direction
                HAVING COUNT(*) > 1
                LIMIT 20
                """
            )
        ).fetchall()
        out["duplicate_intent_groups"] = len(dup_intents)

        # Assets
        assets_out = []
        for asset in list_target_assets():
            inst = store.get_instrument_by_symbol(asset.db_symbol)
            if not inst:
                assets_out.append({"symbol": asset.db_symbol, "missing": True})
                continue
            c5 = store.count_candles(inst.id, "5m")
            c15 = store.count_candles(inst.id, "15m")
            c1h = store.count_candles(inst.id, "1h")
            latest = store.latest_candle_timestamp(inst.id, "5m")
            fresh = is_market_data_fresh(latest, "5m", NOW) if latest else False
            pos = session.execute(
                text(
                    """
                    SELECT COUNT(*) FILTER (WHERE status='open') AS open_p,
                           COUNT(*) FILTER (WHERE status='closed') AS closed_p
                    FROM positions p
                    JOIN instruments i ON i.id = p.instrument_id
                    WHERE i.symbol = :sym AND p.mode = 'paper'
                    """
                ),
                {"sym": asset.db_symbol},
            ).mappings().first()
            assets_out.append(
                {
                    "symbol": asset.db_symbol,
                    "provider": asset.primary_provider.value,
                    "5m": c5,
                    "15m": c15,
                    "1h": c1h,
                    "latest_5m": latest.isoformat() if latest else None,
                    "fresh_5m": fresh,
                    "ready_5m": c5 >= STRATEGY_MIN_CANDLES,
                    "ready_15m": c15 >= STRATEGY_MIN_CANDLES,
                    "ready_1h": c1h >= STRATEGY_MIN_CANDLES,
                    "open_positions": int(pos["open_p"] or 0),
                    "closed_positions": int(pos["closed_p"] or 0),
                }
            )
        out["assets"] = assets_out

        # Robot activity by timeframe (24h)
        robot_tf = session.execute(
            text(
                """
                SELECT si.timeframe, COUNT(DISTINCT d.id) AS decisions,
                       COUNT(DISTINCT oi.id) AS intents,
                       COUNT(DISTINCT f.id) AS fills
                FROM strategy_instances si
                LEFT JOIN decisions d ON d.strategy_instance_id = si.id AND d.created_at >= :since
                LEFT JOIN order_intents oi ON oi.strategy_instance_id = si.id AND oi.created_at >= :since
                LEFT JOIN fills f ON f.created_at >= :since
                WHERE si.mode = 'paper'
                GROUP BY si.timeframe
                ORDER BY si.timeframe
                """
            ),
            {"since": HOURS_24},
        ).mappings().all()
        out["activity_by_timeframe_24h"] = [dict(r) for r in robot_tf]

        # Worker runs timeline
        runs = session.execute(
            text(
                """
                SELECT worker_name, started_at, finished_at, status, jobs_processed
                FROM worker_runs
                WHERE started_at >= :since
                ORDER BY started_at DESC
                LIMIT 80
                """
            ),
            {"since": HOURS_24},
        ).mappings().all()
        out["worker_runs_24h"] = [
            {k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in dict(r).items()}
            for r in runs
        ]

        # Reconciliation
        initial = _dec(fin["initial_capital"])
        balance = _dec(fin["total_balance"])
        equity = _dec(fin["total_equity"])
        unreal = _dec(fin["total_unrealized"])
        real = _dec(realized["realized_pnl"])
        out["reconciliation"] = {
            "initial_capital": str(initial),
            "total_balance": str(balance),
            "total_equity": str(equity),
            "realized_pnl_trades_table": str(real),
            "unrealized_pnl_portfolios": str(unreal),
            "balance_minus_initial": str(balance - initial),
            "equity_minus_balance_minus_unreal": str(equity - balance - unreal),
            "initial_plus_realized_plus_unreal": str(initial + real + unreal),
            "equity_minus_expected": str(equity - (initial + real + unreal)),
        }

    print(json.dumps(out, indent=2, default=str))


if __name__ == "__main__":
    main()
