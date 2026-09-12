"""Live-sim account metrics for API and Home dashboard."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import text

from quantara_engine.broker.accounts import LIVE_SIM_10K_ACCOUNT_SLUG
from quantara_engine.broker.execution_service import BrokerExecutionService
from quantara_engine.broker.state_builder import build_live_sim_broker_account
from quantara_engine.live_sim.candidate_log import list_recent_allocations
from quantara_engine.live_sim.risk_policy import compute_open_sl_risk, load_risk_settings
from quantara_engine.persistence.store import TradingStore


def _runtime_duration(activated_at) -> str | None:
    if not activated_at:
        return None
    if isinstance(activated_at, str):
        activated_at = datetime.fromisoformat(activated_at.replace("Z", "+00:00"))
    delta = datetime.now(timezone.utc) - activated_at.astimezone(timezone.utc)
    days = delta.days
    hours, rem = divmod(delta.seconds, 3600)
    minutes = rem // 60
    if days > 0:
        return f"{days} ימים, {hours} שעות"
    if hours > 0:
        return f"{hours} שעות, {minutes} דקות"
    return f"{minutes} דקות"


def build_live_sim_summary(store: TradingStore) -> dict:
    row = store.session.execute(
        text(
            """
            SELECT id::text, starting_cash, equity, cash, balance, realized_pnl,
                   unrealized_pnl, gross_exposure, net_exposure, available_margin,
                   activated_at, risk_settings, fees_paid
            FROM broker_accounts WHERE slug = :slug
            """
        ),
        {"slug": LIVE_SIM_10K_ACCOUNT_SLUG},
    ).mappings().first()
    if not row:
        return {"available": False}

    account_id = row["id"]
    starting = Decimal(str(row["starting_cash"]))
    equity = Decimal(str(row["equity"]))
    settings = load_risk_settings(dict(row))
    hwm = settings.high_water_mark
    current_dd = float((hwm - equity) / hwm * 100) if hwm > 0 and equity < hwm else 0.0
    total_return = float((equity - starting) / starting * 100) if starting > 0 else 0.0

    open_risk = compute_open_sl_risk(store, account_id)
    sl_pct = float(open_risk.total_sl_risk_usd / equity * 100) if equity > 0 else 0.0

    positions = store.session.execute(
        text(
            """
            SELECT p.id::text, i.symbol, p.direction::text, p.robot_label, p.strategy_slug,
                   p.timeframe, p.quantity, p.entry_price, p.current_price, p.stop_loss,
                   p.take_profit, p.planned_sl_risk_usd, p.unrealized_pnl, p.opened_at
            FROM live_sim_positions p
            JOIN instruments i ON i.id = p.instrument_id
            WHERE p.broker_account_id = :aid AND p.status = 'open'
            ORDER BY p.opened_at DESC
            """
        ),
        {"aid": account_id},
    ).mappings().all()

    closed = store.session.execute(
        text(
            """
            SELECT COUNT(*) AS cnt,
                   SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) AS wins
            FROM (
              SELECT (f.realized_pnl) AS realized_pnl
              FROM broker_fills f
              JOIN broker_orders o ON o.id = f.broker_order_id
              WHERE o.broker_account_id = :aid AND o.order_purpose IN ('sl', 'tp', 'close')
            ) t
            """
        ),
        {"aid": account_id},
    ).mappings().first()

    closed_count = int(closed["cnt"] or 0) if closed else 0
    wins = int(closed["wins"] or 0) if closed else 0
    win_rate = float(wins / closed_count * 100) if closed_count > 0 else 0.0

    stats = store.session.execute(
        text(
            """
            SELECT
              COUNT(*) FILTER (WHERE accepted) AS accepted,
              COUNT(*) FILTER (WHERE NOT accepted) AS rejected,
              COUNT(*) AS total
            FROM live_sim_allocation_log WHERE broker_account_id = :aid
            """
        ),
        {"aid": account_id},
    ).mappings().first()

    svc = BrokerExecutionService(store, account_slug=LIVE_SIM_10K_ACCOUNT_SLUG)
    snap = build_live_sim_broker_account(store)
    recent = list_recent_allocations(store, account_id, limit=30)

    daily_pnl = equity - settings.daily_start_equity

    return {
        "available": True,
        "slug": LIVE_SIM_10K_ACCOUNT_SLUG,
        "label_he": "סימולציית $10,000",
        "starting_capital": float(starting),
        "equity": float(equity),
        "cash": float(row["cash"]),
        "balance": float(row["balance"]),
        "available_margin": float(row["available_margin"] or 0),
        "realized_pnl": float(row["realized_pnl"] or 0),
        "unrealized_pnl": float(row["unrealized_pnl"] or 0),
        "daily_pnl": float(daily_pnl),
        "total_return_pct": total_return,
        "high_water_mark": float(hwm),
        "current_drawdown_pct": current_dd,
        "max_drawdown_pct": current_dd,
        "open_sl_risk_usd": float(open_risk.total_sl_risk_usd),
        "open_sl_risk_pct": sl_pct,
        "gross_exposure": float(row["gross_exposure"] or 0),
        "net_exposure": float(row["net_exposure"] or 0),
        "fees_paid": float(row["fees_paid"] or 0),
        "started_at": row["activated_at"].isoformat() if row["activated_at"] else None,
        "runtime_duration_he": _runtime_duration(row["activated_at"]),
        "open_positions": [
            {
                "id": p["id"],
                "symbol": p["symbol"],
                "direction": p["direction"],
                "robot": p["robot_label"],
                "strategy_slug": p["strategy_slug"],
                "timeframe": p["timeframe"],
                "quantity": float(p["quantity"]),
                "entry_price": float(p["entry_price"]),
                "current_price": float(p["current_price"] or p["entry_price"]),
                "stop_loss": float(p["stop_loss"]),
                "take_profit": float(p["take_profit"]) if p["take_profit"] else None,
                "planned_sl_risk_usd": float(p["planned_sl_risk_usd"]),
                "unrealized_pnl": float(p["unrealized_pnl"] or 0),
                "opened_at": p["opened_at"].isoformat() if p["opened_at"] else None,
            }
            for p in positions
        ],
        "closed_trades_count": closed_count,
        "win_rate_pct": win_rate,
        "candidates": {
            "total": int(stats["total"] or 0),
            "accepted": int(stats["accepted"] or 0),
            "rejected": int(stats["rejected"] or 0),
            "acceptance_rate_pct": (
                float(stats["accepted"] / stats["total"] * 100)
                if stats and stats["total"]
                else 0.0
            ),
        },
        "recent_decisions": recent,
        "risk_settings": {
            "risk_per_trade_pct": float(settings.risk_per_trade_pct),
            "max_total_open_sl_risk_pct": float(settings.max_total_open_sl_risk_pct),
            "max_symbol_sl_risk_pct": float(settings.max_symbol_sl_risk_pct),
            "max_group_sl_risk_pct": float(settings.max_group_sl_risk_pct),
            "daily_loss_gate_pct": float(settings.daily_loss_gate_pct),
            "max_drawdown_gate_pct": float(settings.max_drawdown_gate_pct),
            "concentration_mode": settings.concentration_mode,
            "risk_per_trade_usd_approx": float(
                equity * settings.risk_per_trade_pct / Decimal("100")
            ),
        },
        "broker_positions": [
            {
                "symbol": p.symbol,
                "net_quantity": float(p.net_quantity),
                "average_price": float(p.average_price),
                "mark_price": float(p.mark_price),
                "unrealized_pnl": float(p.unrealized_pnl),
            }
            for p in snap.positions.values()
        ],
    }


def build_comparison_summary(store: TradingStore) -> dict:
    from quantara_engine.broker.physical_risk import compute_physical_broker_risk
    from quantara_engine.broker.state_builder import build_competition_broker_account

    research = build_competition_broker_account(store)
    research_row = BrokerExecutionService(store).get_account_row() or {}
    research_start = Decimal("320000")
    research_equity = research.equity or Decimal("0")
    research_return = float((research_equity - research_start) / research_start * 100)

    live = build_live_sim_summary(store)
    if not live.get("available"):
        return {"available": False}

    research_risk = compute_physical_broker_risk(store)
    research_sl_pct = (
        float(research_risk["physical_remaining_sl_risk_usd"] / research_equity * 100)
        if research_risk.get("physical_remaining_sl_risk_usd") and research_equity > 0
        else 0.0
    )

    def _norm(side: dict, equity: float, starting: float) -> dict:
        gross = side.get("gross_exposure") or 0
        return {
            "return_pct": side.get("total_return_pct", 0),
            "current_drawdown_pct": side.get("current_drawdown_pct", 0),
            "max_drawdown_pct": side.get("max_drawdown_pct", 0),
            "win_rate_pct": side.get("win_rate_pct", 0),
            "closed_trades": side.get("closed_trades_count", 0),
            "open_positions": len(side.get("open_positions") or []),
            "sl_risk_pct": side.get("open_sl_risk_pct", 0),
            "gross_exposure_pct": float(gross / equity * 100) if equity > 0 else 0,
            "realized_pnl": side.get("realized_pnl", 0),
            "unrealized_pnl": side.get("unrealized_pnl", 0),
            "fees_paid": side.get("fees_paid", 0),
            "equity": equity,
            "starting_capital": starting,
        }

    return {
        "available": True,
        "research": {
            "label_he": "חשבון המחקר",
            **_norm(
                {
                    "total_return_pct": research_return,
                    "current_drawdown_pct": 0,
                    "max_drawdown_pct": 0,
                    "win_rate_pct": 0,
                    "closed_trades_count": 0,
                    "open_positions": list(research.positions.values()),
                    "open_sl_risk_pct": research_sl_pct,
                    "gross_exposure": float(research.gross_exposure or 0),
                    "realized_pnl": float(research.realized_pnl),
                    "unrealized_pnl": float(research.unrealized_pnl),
                    "fees_paid": float(research_row.get("fees_paid") or 0),
                },
                float(research_equity),
                float(research_start),
            ),
        },
        "live_sim": {
            "label_he": "סימולציית $10,000",
            **_norm(live, live["equity"], live["starting_capital"]),
            "candidates_total": live["candidates"]["total"],
            "candidates_accepted": live["candidates"]["accepted"],
            "candidates_rejected": live["candidates"]["rejected"],
            "acceptance_rate_pct": live["candidates"]["acceptance_rate_pct"],
        },
    }
