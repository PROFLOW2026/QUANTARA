"""Live-sim account metrics for API and Home dashboard."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import text

from quantara_engine.broker.accounts import LIVE_SIM_10K_ACCOUNT_SLUG
from quantara_engine.broker.execution_service import BrokerExecutionService
from quantara_engine.broker.state_builder import build_live_sim_broker_account
from quantara_engine.broker.vendor import vendor_label_he
from quantara_engine.live_sim.audit_scope import resolve_live_sim_audit_since
from quantara_engine.live_sim.candidate_log import list_recent_allocations
from quantara_engine.live_sim.execution_routing import (
    is_multi_broker_live_sim_active,
    list_active_live_sim_broker_account_ids,
    list_active_live_sim_broker_account_slugs,
)
from quantara_engine.live_sim.risk_policy import compute_open_sl_risk, load_risk_settings
from quantara_engine.owner_portfolio.aggregation import aggregate_owner_portfolio
from quantara_engine.owner_portfolio.service import LIVE_SIM_OWNER_SLUG, OwnerPortfolioService
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


def _list_closed_live_sim_trades(store: TradingStore, account_ids: list[str], *, limit: int = 50) -> list[dict]:
    if not account_ids:
        return []
    rows = store.session.execute(
        text(
            """
            SELECT
              p.id::text AS position_id,
              i.symbol,
              p.robot_label,
              p.strategy_slug,
              p.timeframe,
              p.direction::text AS direction,
              p.opened_at,
              p.closed_at,
              p.entry_price,
              p.quantity,
              p.stop_loss,
              p.take_profit,
              p.opportunity_key,
              ba.slug AS broker_slug,
              ba.broker_vendor::text AS broker_vendor,
              (
                SELECT COALESCE(SUM(l.realized_pnl), 0)
                FROM broker_attribution_ledger l
                WHERE l.strategy_position_id = p.id
                  AND l.exit_price IS NOT NULL
              ) AS realized_pnl,
              (
                SELECT MAX(l.exit_price)
                FROM broker_attribution_ledger l
                WHERE l.strategy_position_id = p.id
                  AND l.exit_price IS NOT NULL
              ) AS exit_price,
              (
                SELECT o.order_purpose::text
                FROM broker_orders o
                JOIN broker_fills f ON f.broker_order_id = o.id
                JOIN instruments ii ON ii.id = o.instrument_id
                WHERE o.broker_account_id = p.broker_account_id
                  AND ii.symbol = i.symbol
                  AND o.order_purpose IN ('sl', 'tp', 'close')
                  AND p.closed_at IS NOT NULL
                  AND f.filled_at BETWEEN p.closed_at - INTERVAL '2 minutes'
                                      AND p.closed_at + INTERVAL '2 minutes'
                ORDER BY f.filled_at DESC
                LIMIT 1
              ) AS close_reason,
              (
                SELECT COALESCE(SUM(f.fees), 0)
                FROM broker_orders o
                JOIN broker_fills f ON f.broker_order_id = o.id
                JOIN instruments ii ON ii.id = o.instrument_id
                WHERE o.broker_account_id = p.broker_account_id
                  AND ii.symbol = i.symbol
                  AND (
                    (p.opportunity_key IS NOT NULL AND o.idempotency_key LIKE '%' || p.opportunity_key || '%')
                    OR (
                      p.closed_at IS NOT NULL
                      AND f.filled_at BETWEEN p.opened_at - INTERVAL '1 minute'
                                          AND p.closed_at + INTERVAL '2 minutes'
                    )
                  )
              ) AS fees,
              (
                SELECT o.execution_product::text
                FROM broker_orders o
                JOIN broker_fills f ON f.broker_order_id = o.id
                JOIN instruments ii ON ii.id = o.instrument_id
                WHERE o.broker_account_id = p.broker_account_id
                  AND ii.symbol = i.symbol
                  AND o.order_purpose IN ('sl', 'tp', 'close')
                  AND p.closed_at IS NOT NULL
                  AND f.filled_at BETWEEN p.closed_at - INTERVAL '2 minutes'
                                      AND p.closed_at + INTERVAL '2 minutes'
                ORDER BY f.filled_at DESC
                LIMIT 1
              ) AS execution_product
            FROM live_sim_positions p
            JOIN instruments i ON i.id = p.instrument_id
            JOIN broker_accounts ba ON ba.id = p.broker_account_id
            WHERE p.status = 'closed'
              AND p.broker_account_id = ANY(CAST(:aids AS uuid[]))
            ORDER BY p.closed_at DESC NULLS LAST
            LIMIT :lim
            """
        ),
        {"aids": account_ids, "lim": limit},
    ).mappings().all()
    out: list[dict] = []
    for r in rows:
        realized = float(r["realized_pnl"] or 0)
        fees = float(r["fees"] or 0)
        out.append(
            {
                "id": r["position_id"],
                "symbol": r["symbol"],
                "robot": r["robot_label"],
                "strategy_slug": r["strategy_slug"],
                "timeframe": r["timeframe"],
                "direction": r["direction"],
                "opened_at": r["opened_at"].isoformat() if r["opened_at"] else None,
                "closed_at": r["closed_at"].isoformat() if r["closed_at"] else None,
                "entry_price": float(r["entry_price"]),
                "exit_price": float(r["exit_price"]) if r["exit_price"] is not None else None,
                "quantity": float(r["quantity"]),
                "stop_loss": float(r["stop_loss"]) if r["stop_loss"] is not None else None,
                "take_profit": float(r["take_profit"]) if r["take_profit"] is not None else None,
                "close_reason": r["close_reason"],
                "gross_pnl": realized + fees,
                "fees": fees,
                "funding": 0.0,
                "net_realized_pnl": realized,
                "broker": r["broker_slug"],
                "broker_vendor": r["broker_vendor"],
                "execution_product": r["execution_product"],
            }
        )
    return out


def _funnel_counters(store: TradingStore, account_ids: list[str], audit_since) -> dict:
    if not account_ids:
        return {
            "total": 0,
            "accepted": 0,
            "rejected": 0,
            "orders_sent": 0,
            "fills": 0,
            "positions_opened": 0,
            "acceptance_rate_pct": 0.0,
        }
    since_clause = "AND created_at >= :since" if audit_since is not None else ""
    stats = store.session.execute(
        text(
            f"""
            SELECT
              COUNT(*) FILTER (WHERE accepted) AS accepted,
              COUNT(*) FILTER (WHERE NOT accepted) AS rejected,
              COUNT(*) AS total
            FROM live_sim_allocation_log
            WHERE broker_account_id = ANY(CAST(:aids AS uuid[]))
            {since_clause}
            """
        ),
        {"aids": account_ids, "since": audit_since},
    ).mappings().first()

    order_since = "AND o.created_at >= :since" if audit_since is not None else ""
    orders = store.session.execute(
        text(
            f"""
            SELECT COUNT(*) AS cnt
            FROM broker_orders o
            WHERE o.broker_account_id = ANY(CAST(:aids AS uuid[]))
              AND o.order_purpose = 'entry'
            {order_since}
            """
        ),
        {"aids": account_ids, "since": audit_since},
    ).scalar()
    fills = store.session.execute(
        text(
            f"""
            SELECT COUNT(*) AS cnt
            FROM broker_fills f
            JOIN broker_orders o ON o.id = f.broker_order_id
            WHERE o.broker_account_id = ANY(CAST(:aids AS uuid[]))
              AND o.order_purpose = 'entry'
            {order_since}
            """
        ),
        {"aids": account_ids, "since": audit_since},
    ).scalar()
    opened = store.session.execute(
        text(
            f"""
            SELECT COUNT(*) AS cnt
            FROM live_sim_positions p
            WHERE p.broker_account_id = ANY(CAST(:aids AS uuid[]))
            {"AND p.opened_at >= :since" if audit_since is not None else ""}
            """
        ),
        {"aids": account_ids, "since": audit_since},
    ).scalar()
    total = int(stats["total"] or 0) if stats else 0
    accepted = int(stats["accepted"] or 0) if stats else 0
    rejected = int(stats["rejected"] or 0) if stats else 0
    return {
        "total": total,
        "accepted": accepted,
        "rejected": rejected,
        "passed_risk": accepted,
        "orders_sent": int(orders or 0),
        "fills": int(fills or 0),
        "positions_opened": int(opened or 0),
        "acceptance_rate_pct": float(accepted / total * 100) if total else 0.0,
    }


def build_live_sim_summary(store: TradingStore) -> dict:
    portfolio_svc = OwnerPortfolioService(store)
    portfolio_row = portfolio_svc.get_portfolio_row(LIVE_SIM_OWNER_SLUG)
    multi_broker = is_multi_broker_live_sim_active(store)
    active_ids = list_active_live_sim_broker_account_ids(store)
    active_slugs = list_active_live_sim_broker_account_slugs(store)

    # Legacy row kept for activated_at / risk_settings fallback only.
    legacy = store.session.execute(
        text(
            """
            SELECT id::text, starting_cash, equity, cash, balance, realized_pnl,
                   unrealized_pnl, gross_exposure, net_exposure, available_margin,
                   activated_at, risk_settings, fees_paid, account_metadata,
                   initial_margin_used
            FROM broker_accounts WHERE slug = :slug
            """
        ),
        {"slug": LIVE_SIM_10K_ACCOUNT_SLUG},
    ).mappings().first()

    if multi_broker and active_ids:
        owner_snapshot = aggregate_owner_portfolio(store, slug=LIVE_SIM_OWNER_SLUG)
        if not owner_snapshot:
            return {"available": False}
        # Refresh asset MTM so attribution rows track broker truth before UI read.
        try:
            from quantara_engine.owner_portfolio.asset_ledger import (
                refresh_all_asset_states_from_positions,
            )

            refresh_all_asset_states_from_positions(store, LIVE_SIM_OWNER_SLUG)
            owner_snapshot = aggregate_owner_portfolio(store, slug=LIVE_SIM_OWNER_SLUG)
        except Exception:
            pass

        starting = owner_snapshot.target_capital
        equity = owner_snapshot.total_equity
        cash = owner_snapshot.total_cash
        balance = owner_snapshot.total_balance
        realized = owner_snapshot.total_realized_pnl
        unrealized = owner_snapshot.total_unrealized_pnl
        gross = owner_snapshot.total_gross_exposure
        net = owner_snapshot.total_net_exposure
        available_margin = owner_snapshot.total_available_capital
        initial_margin = owner_snapshot.total_initial_margin_used
        fees = Decimal("0")
        activated_at = None
        risk_settings_raw = {}
        for slug in active_slugs:
            row = store.session.execute(
                text(
                    """
                    SELECT fees_paid, activated_at, risk_settings
                    FROM broker_accounts WHERE slug = :slug
                    """
                ),
                {"slug": slug},
            ).mappings().first()
            if not row:
                continue
            fees += Decimal(str(row["fees_paid"] or 0))
            if row["activated_at"] and (activated_at is None or row["activated_at"] < activated_at):
                activated_at = row["activated_at"]
            if not risk_settings_raw and row.get("risk_settings"):
                risk_settings_raw = dict(row["risk_settings"] or {})
        if not risk_settings_raw and legacy:
            risk_settings_raw = dict(legacy.get("risk_settings") or {})
            activated_at = activated_at or legacy.get("activated_at")

        # Synthetic account dict for risk settings helpers.
        account_for_risk = {
            "equity": equity,
            "starting_cash": starting,
            "risk_settings": risk_settings_raw,
        }
        settings = load_risk_settings(account_for_risk)
        hwm = max(settings.high_water_mark, starting, equity)
        current_dd = float((hwm - equity) / hwm * 100) if hwm > 0 and equity < hwm else 0.0
        total_return = float((equity - starting) / starting * 100) if starting > 0 else 0.0
        daily_pnl = equity - settings.daily_start_equity

        open_risk_usd = Decimal("0")
        for aid in active_ids:
            open_risk_usd += compute_open_sl_risk(store, aid).total_sl_risk_usd
        sl_pct = float(open_risk_usd / equity * 100) if equity > 0 else 0.0

        positions = store.session.execute(
            text(
                """
                SELECT p.id::text, i.symbol, p.direction::text, p.robot_label, p.strategy_slug,
                       p.timeframe, p.quantity, p.entry_price, p.current_price, p.stop_loss,
                       p.take_profit, p.planned_sl_risk_usd, p.unrealized_pnl, p.opened_at,
                       ba.slug AS broker_slug
                FROM live_sim_positions p
                JOIN instruments i ON i.id = p.instrument_id
                JOIN broker_accounts ba ON ba.id = p.broker_account_id
                WHERE p.broker_account_id = ANY(CAST(:aids AS uuid[]))
                  AND p.status = 'open'
                ORDER BY p.opened_at DESC
                """
            ),
            {"aids": active_ids},
        ).mappings().all()

        closed = store.session.execute(
            text(
                """
                SELECT COUNT(*) AS cnt,
                       SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) AS wins
                FROM (
                  SELECT f.realized_pnl AS realized_pnl
                  FROM broker_fills f
                  JOIN broker_orders o ON o.id = f.broker_order_id
                  WHERE o.broker_account_id = ANY(CAST(:aids AS uuid[]))
                    AND o.order_purpose IN ('sl', 'tp', 'close')
                ) t
                """
            ),
            {"aids": active_ids},
        ).mappings().first()
        closed_count = int(closed["cnt"] or 0) if closed else 0
        wins = int(closed["wins"] or 0) if closed else 0
        win_rate = float(wins / closed_count * 100) if closed_count > 0 else 0.0

        audit_since = resolve_live_sim_audit_since(
            store,
            account_metadata=dict((legacy or {}).get("account_metadata") or {}),
            activated_at=activated_at,
        )
        funnel = _funnel_counters(store, active_ids, audit_since)
        recent: list[dict] = []
        for aid in active_ids:
            recent.extend(list_recent_allocations(store, aid, limit=20, since=audit_since))
        recent.sort(key=lambda r: r.get("created_at") or "", reverse=True)
        recent = recent[:40]

        closed_trades = _list_closed_live_sim_trades(store, active_ids)

        owner_payload = {
            "slug": owner_snapshot.slug,
            "target_capital": float(owner_snapshot.target_capital),
            "total_equity": float(owner_snapshot.total_equity),
            "total_cash": float(owner_snapshot.total_cash),
            "total_realized_pnl": float(owner_snapshot.total_realized_pnl),
            "total_unrealized_pnl": float(owner_snapshot.total_unrealized_pnl),
            "total_gross_exposure": float(owner_snapshot.total_gross_exposure),
            "total_net_exposure": float(owner_snapshot.total_net_exposure),
            "allocated_capital_sum": float(owner_snapshot.allocated_capital_sum),
            "allocation_remaining": float(owner_snapshot.allocation_remaining),
            "multi_broker_mode_enabled": True,
            "global_execution_halted": owner_snapshot.global_execution_halted,
            "asset_slices": [
                {
                    "canonical_symbol": a.canonical_symbol,
                    "label_he": a.label_he,
                    "broker_vendor": a.broker_vendor,
                    "starting_allocated_capital": float(a.starting_allocated_capital),
                    "current_cash": float(a.current_cash),
                    "current_equity": float(a.current_equity),
                    "realized_pnl": float(a.realized_pnl),
                    "unrealized_pnl": float(a.unrealized_pnl),
                    "fees_paid": float(a.fees_paid),
                    "gross_exposure": float(a.gross_exposure),
                    "open_sl_risk_usd": float(a.open_sl_risk_usd),
                    "enabled": a.enabled,
                }
                for a in owner_snapshot.asset_slices
            ],
        }
        broker_breakdown = [
            {
                "slug": s.slug,
                "vendor": s.broker_vendor,
                "label_he": s.label_he or vendor_label_he(s.broker_vendor),
                "allocated_capital": float(s.allocated_capital or 0),
                "cash": float(s.cash),
                "equity": float(s.equity),
                "available_margin": float(s.available_margin),
                "realized_pnl": float(s.realized_pnl),
                "unrealized_pnl": float(s.unrealized_pnl),
                "gross_exposure": float(s.gross_exposure),
                "connection_state": s.connection_state,
                "enabled": s.enabled,
            }
            for s in owner_snapshot.broker_slices
            if s.enabled and s.slug != LIVE_SIM_10K_ACCOUNT_SLUG
        ]

        broker_positions: list[dict] = []
        for slug in active_slugs:
            pos_rows = store.session.execute(
                text(
                    """
                    SELECT i.symbol, bp.net_quantity, bp.average_price, bp.mark_price,
                           bp.unrealized_pnl
                    FROM broker_positions bp
                    JOIN instruments i ON i.id = bp.instrument_id
                    JOIN broker_accounts ba ON ba.id = bp.broker_account_id
                    WHERE ba.slug = :slug AND bp.net_quantity <> 0
                    """
                ),
                {"slug": slug},
            ).mappings().all()
            for p in pos_rows:
                broker_positions.append(
                    {
                        "symbol": p["symbol"],
                        "net_quantity": float(p["net_quantity"]),
                        "average_price": float(p["average_price"]),
                        "mark_price": float(p["mark_price"] or 0),
                        "unrealized_pnl": float(p["unrealized_pnl"] or 0),
                        "broker": slug,
                    }
                )

        return {
            "available": True,
            "slug": "live-sim-owner",
            "label_he": "סימולציית $10,000",
            "authority": "active_owner_portfolio",
            "multi_broker_mode": True,
            "legacy_excluded": True,
            "owner_portfolio": owner_payload,
            "broker_breakdown": broker_breakdown,
            "starting_capital": float(starting),
            "equity": float(equity),
            "cash": float(cash),
            "balance": float(balance),
            "available_margin": float(available_margin),
            "initial_margin": float(initial_margin),
            "realized_pnl": float(realized),
            "unrealized_pnl": float(unrealized),
            "total_pnl": float(realized + unrealized),
            "daily_pnl": float(daily_pnl),
            "total_return_pct": total_return,
            "high_water_mark": float(hwm),
            "current_drawdown_pct": current_dd,
            "max_drawdown_pct": current_dd,
            "open_sl_risk_usd": float(open_risk_usd),
            "open_sl_risk_pct": sl_pct,
            "gross_exposure": float(gross),
            "net_exposure": float(net),
            "fees_paid": float(fees),
            "started_at": activated_at.isoformat() if activated_at else None,
            "runtime_duration_he": _runtime_duration(activated_at),
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
                    "broker": p["broker_slug"],
                }
                for p in positions
            ],
            "closed_trades": closed_trades,
            "closed_trades_count": closed_count,
            "win_rate_pct": win_rate,
            "candidates": funnel,
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
            "broker_positions": broker_positions,
        }

    # Legacy single-account path (multi-broker off).
    if not legacy:
        return {"available": False}

    account_id = legacy["id"]
    starting = Decimal(str(legacy["starting_cash"]))
    equity = Decimal(str(legacy["equity"]))
    settings = load_risk_settings(dict(legacy))
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

    audit_since = resolve_live_sim_audit_since(
        store,
        account_metadata=dict(legacy.get("account_metadata") or {}),
        activated_at=legacy.get("activated_at"),
    )
    funnel = _funnel_counters(store, [account_id], audit_since)
    svc = BrokerExecutionService(store, account_slug=LIVE_SIM_10K_ACCOUNT_SLUG)
    snap = build_live_sim_broker_account(store)
    recent = list_recent_allocations(store, account_id, limit=30, since=audit_since)
    daily_pnl = equity - settings.daily_start_equity
    closed_trades = _list_closed_live_sim_trades(store, [account_id])

    owner_snapshot = aggregate_owner_portfolio(store, slug=LIVE_SIM_OWNER_SLUG)
    owner_payload = None
    broker_breakdown = None
    if owner_snapshot:
        owner_payload = {
            "slug": owner_snapshot.slug,
            "target_capital": float(owner_snapshot.target_capital),
            "total_equity": float(owner_snapshot.total_equity),
            "total_cash": float(owner_snapshot.total_cash),
            "total_realized_pnl": float(owner_snapshot.total_realized_pnl),
            "total_unrealized_pnl": float(owner_snapshot.total_unrealized_pnl),
            "total_gross_exposure": float(owner_snapshot.total_gross_exposure),
            "total_net_exposure": float(owner_snapshot.total_net_exposure),
            "allocated_capital_sum": float(owner_snapshot.allocated_capital_sum),
            "allocation_remaining": float(owner_snapshot.allocation_remaining),
            "multi_broker_mode_enabled": False,
            "global_execution_halted": owner_snapshot.global_execution_halted,
        }

    return {
        "available": True,
        "slug": LIVE_SIM_10K_ACCOUNT_SLUG,
        "label_he": "סימולציית $10,000",
        "authority": "legacy_live_sim_10k",
        "multi_broker_mode": False,
        "legacy_excluded": False,
        "owner_portfolio": owner_payload,
        "broker_breakdown": broker_breakdown,
        "starting_capital": float(starting),
        "equity": float(equity),
        "cash": float(legacy["cash"]),
        "balance": float(legacy["balance"]),
        "available_margin": float(legacy["available_margin"] or 0),
        "initial_margin": float(legacy.get("initial_margin_used") or 0),
        "realized_pnl": float(legacy["realized_pnl"] or 0),
        "unrealized_pnl": float(legacy["unrealized_pnl"] or 0),
        "total_pnl": float(Decimal(str(legacy["realized_pnl"] or 0)) + Decimal(str(legacy["unrealized_pnl"] or 0))),
        "daily_pnl": float(daily_pnl),
        "total_return_pct": total_return,
        "high_water_mark": float(hwm),
        "current_drawdown_pct": current_dd,
        "max_drawdown_pct": current_dd,
        "open_sl_risk_usd": float(open_risk.total_sl_risk_usd),
        "open_sl_risk_pct": sl_pct,
        "gross_exposure": float(legacy["gross_exposure"] or 0),
        "net_exposure": float(legacy["net_exposure"] or 0),
        "fees_paid": float(legacy["fees_paid"] or 0),
        "started_at": legacy["activated_at"].isoformat() if legacy["activated_at"] else None,
        "runtime_duration_he": _runtime_duration(legacy["activated_at"]),
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
        "closed_trades": closed_trades,
        "closed_trades_count": closed_count,
        "win_rate_pct": win_rate,
        "candidates": funnel,
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


def _aggregate_competition_trade_metrics(store: TradingStore) -> dict:
    """Closed strategy trades across all competition research portfolios."""
    from sqlalchemy import func, select

    from quantara_engine.competition.paper_run import trade_scope_clause
    from quantara_engine.models.trading import Trade as OrmTrade
    from quantara_engine.persistence.batch_summary import _uuids

    portfolio_ids = [str(e["portfolio"].id) for e in store.list_competition_entries()]
    ids = _uuids(portfolio_ids)
    if not ids:
        return {
            "closed_trades_count": 0,
            "wins": 0,
            "losses": 0,
            "win_rate_pct": None,
        }

    wins_expr = func.count().filter(OrmTrade.realized_pnl > 0)
    losses_expr = func.count().filter(OrmTrade.realized_pnl <= 0)
    row = store.session.execute(
        select(
            func.count().label("closed"),
            wins_expr.label("wins"),
            losses_expr.label("losses"),
        ).where(
            OrmTrade.portfolio_id.in_(ids),
            trade_scope_clause(store),
        )
    ).one()
    closed = int(row.closed or 0)
    wins = int(row.wins or 0)
    losses = int(row.losses or 0)
    win_rate = round(wins / closed * 100, 2) if closed > 0 else None
    return {
        "closed_trades_count": closed,
        "wins": wins,
        "losses": losses,
        "win_rate_pct": win_rate,
    }


def _broker_exit_fill_stats(store: TradingStore, account_id: str) -> dict:
    """Exit fills on a physical broker account (sl/tp/close)."""
    if not account_id:
        return {"closed_trades_count": 0, "wins": 0, "losses": 0, "win_rate_pct": None}
    row = store.session.execute(
        text(
            """
            SELECT COUNT(*) AS cnt,
                   SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) AS wins,
                   SUM(CASE WHEN realized_pnl <= 0 THEN 1 ELSE 0 END) AS losses
            FROM (
              SELECT f.realized_pnl AS realized_pnl
              FROM broker_fills f
              JOIN broker_orders o ON o.id = f.broker_order_id
              WHERE o.broker_account_id = CAST(:aid AS uuid)
                AND o.order_purpose IN ('sl', 'tp', 'close')
            ) t
            """
        ),
        {"aid": account_id},
    ).mappings().first()
    closed = int(row["cnt"] or 0) if row else 0
    wins = int(row["wins"] or 0) if row else 0
    losses = int(row["losses"] or 0) if row else 0
    win_rate = round(wins / closed * 100, 2) if closed > 0 else None
    return {
        "closed_trades_count": closed,
        "wins": wins,
        "losses": losses,
        "win_rate_pct": win_rate,
    }


def _account_drawdown_pct(account_row: dict, equity: Decimal, starting: Decimal) -> float:
    settings = load_risk_settings(
        {
            "equity": equity,
            "starting_cash": starting,
            "risk_settings": account_row.get("risk_settings") or {},
        }
    )
    hwm = max(settings.high_water_mark, starting, equity)
    if hwm <= 0 or equity >= hwm:
        return 0.0
    return float((hwm - equity) / hwm * 100)


def _research_strategy_trade_stats(store: TradingStore, broker_equity: Decimal) -> dict:
    from quantara_engine.market_data.active_universe import ACTIVE_DB_SYMBOLS

    portfolio_ids = [str(e["portfolio"].id) for e in store.list_competition_entries()]
    trade_stats = _aggregate_competition_trade_metrics(store)
    symbol_by_instrument_id = {
        str(inst.id): inst.symbol
        for sym in ACTIVE_DB_SYMBOLS
        if (inst := store.get_instrument_by_symbol(sym)) is not None
    }
    exposure_summary, _ = store.batch_competition_exposure_risk_summary(
        portfolio_ids,
        symbol_by_instrument_id=symbol_by_instrument_id,
    )
    sl_usd = exposure_summary.total_remaining_sl_risk_usd
    sl_pct = exposure_summary.open_risk_pct
    if sl_usd is None or exposure_summary.risk_missing_count > 0:
        sl_pct = None
    elif broker_equity > 0 and sl_usd is not None:
        sl_pct = float(sl_usd / broker_equity * 100)
    return {
        "open_positions": exposure_summary.open_position_count,
        "closed_trades_count": trade_stats["closed_trades_count"],
        "wins": trade_stats["wins"],
        "losses": trade_stats["losses"],
        "win_rate_pct": trade_stats["win_rate_pct"],
        "open_sl_risk_pct": sl_pct,
        "open_sl_risk_usd": float(sl_usd) if sl_usd is not None else None,
        "open_sl_risk_unavailable_he": (
            f"חסרים {exposure_summary.risk_missing_count} מחירי SL/סימון"
            if exposure_summary.risk_missing_count > 0
            else None
        ),
    }


def _research_broker_position_stats(
    store: TradingStore,
    research,
    *,
    account_id: str,
    broker_equity: Decimal,
) -> dict:
    from quantara_engine.broker.physical_risk import compute_physical_broker_risk

    broker_positions = [p for p in research.positions.values() if p.net_quantity != 0]
    unique_symbols = len({p.symbol for p in broker_positions})
    fill_stats = _broker_exit_fill_stats(store, account_id)
    physical = compute_physical_broker_risk(store)
    sl_usd = physical.get("physical_remaining_sl_risk_usd")
    sl_pct: float | None
    unavailable_he: str | None = None
    if not physical.get("physical_risk_complete"):
        sl_pct = None
        missing = int(physical.get("physical_risk_missing_count") or 0)
        lots = int(physical.get("attributed_lot_count") or 0)
        if lots > 0 and missing > 0:
            unavailable_he = f"נתוני SL פיזי לא שלמים ({missing} מתוך {lots} לוטים)"
        elif lots == 0:
            unavailable_he = "אין לוטים מיוחסים לברוקר"
        else:
            unavailable_he = "סיכון SL פיזי לא זמין"
    elif sl_usd is not None and broker_equity > 0:
        sl_pct = float(sl_usd / broker_equity * 100)
    else:
        sl_pct = 0.0
    return {
        "open_positions": len(broker_positions),
        "unique_symbols_open": unique_symbols,
        "closed_trades_count": fill_stats["closed_trades_count"],
        "wins": fill_stats["wins"],
        "losses": fill_stats["losses"],
        "win_rate_pct": fill_stats["win_rate_pct"],
        "open_sl_risk_pct": sl_pct,
        "open_sl_risk_usd": float(sl_usd) if sl_usd is not None else None,
        "open_sl_risk_unavailable_he": unavailable_he,
    }


def build_comparison_summary(store: TradingStore) -> dict:
    from quantara_engine.broker.accounts import RESEARCH_PAPER_ACCOUNT
    from quantara_engine.broker.state_builder import build_competition_broker_account

    research = build_competition_broker_account(store)
    research_svc = BrokerExecutionService(store)
    research_row = research_svc.get_account_row() or {}
    research_account_id = str(research_row.get("id") or "")
    research_start = RESEARCH_PAPER_ACCOUNT.starting_cash
    research_equity = research.equity or Decimal("0")
    research_return = float((research_equity - research_start) / research_start * 100)
    research_dd = _account_drawdown_pct(research_row, research_equity, research_start)

    live = build_live_sim_summary(store)
    if not live.get("available"):
        return {"available": False}

    research_strategy = _research_strategy_trade_stats(store, research_equity)
    research_broker = _research_broker_position_stats(
        store,
        research,
        account_id=research_account_id,
        broker_equity=research_equity,
    )

    def _norm(
        side: dict,
        equity: float,
        starting: float,
        *,
        trade_stats: dict,
        financial_scope_he: str,
        trade_stats_scope_he: str,
    ) -> dict:
        gross = side.get("gross_exposure") or 0
        closed = int(trade_stats.get("closed_trades_count") or 0)
        win_rate = trade_stats.get("win_rate_pct")
        if closed == 0:
            win_rate = None
        return {
            "financial_scope_he": financial_scope_he,
            "trade_stats_scope_he": trade_stats_scope_he,
            "return_pct": side.get("total_return_pct", 0),
            "current_drawdown_pct": side.get("current_drawdown_pct", 0),
            "max_drawdown_pct": side.get("max_drawdown_pct", 0),
            "win_rate_pct": win_rate,
            "closed_trades": closed,
            "open_positions": int(trade_stats.get("open_positions") or 0),
            "sl_risk_pct": trade_stats.get("open_sl_risk_pct"),
            "sl_risk_unavailable_he": trade_stats.get("open_sl_risk_unavailable_he"),
            "gross_exposure_pct": float(gross / equity * 100) if equity > 0 else 0,
            "realized_pnl": side.get("realized_pnl", 0),
            "unrealized_pnl": side.get("unrealized_pnl", 0),
            "fees_paid": side.get("fees_paid", 0),
            "equity": equity,
            "starting_capital": starting,
        }

    live_trade_stats = {
        "open_positions": len(live.get("open_positions") or []),
        "closed_trades_count": live.get("closed_trades_count", 0),
        "win_rate_pct": live.get("win_rate_pct"),
        "open_sl_risk_pct": live.get("open_sl_risk_pct"),
        "open_sl_risk_unavailable_he": None,
    }

    return {
        "available": True,
        "scope_notes_he": {
            "financial": "מדדים כספיים — אמת חשבון ברוקר (equity, PnL, חשיפה)",
            "trade_stats": "סטטיסטיקות עסקאות — שכבת אסטרטגיות (עסקאות/פוזיציות שנסגרו)",
        },
        "research": {
            "label_he": "חשבון ברוקר מחקרי",
            **_norm(
                {
                    "total_return_pct": research_return,
                    "current_drawdown_pct": research_dd,
                    "max_drawdown_pct": research_dd,
                    "gross_exposure": float(research.gross_exposure or 0),
                    "realized_pnl": float(research.realized_pnl or 0),
                    "unrealized_pnl": float(research.unrealized_pnl or 0),
                    "fees_paid": float(research_row.get("fees_paid") or 0),
                },
                float(research_equity),
                float(research_start),
                trade_stats=research_strategy,
                financial_scope_he="חשבון ברוקר מחקרי",
                trade_stats_scope_he="עסקאות אסטרטגיה מחקר",
            ),
            "scopes": {
                "strategy": {
                    "label_he": "שכבת אסטרטגיות מחקר",
                    **research_strategy,
                },
                "broker": {
                    "label_he": "פוזיציות נטו בברוקר",
                    **research_broker,
                },
            },
        },
        "live_sim": {
            "label_he": "סימולציית $10,000",
            **_norm(
                live,
                live["equity"],
                live["starting_capital"],
                trade_stats=live_trade_stats,
                financial_scope_he="חשבון Live Sim (ברוקר)",
                trade_stats_scope_he="פוזיציות Live Sim",
            ),
            "scopes": {
                "strategy": {
                    "label_he": "פוזיציות Live Sim",
                    "open_positions": live_trade_stats["open_positions"],
                    "closed_trades_count": live_trade_stats["closed_trades_count"],
                    "win_rate_pct": live_trade_stats["win_rate_pct"],
                    "open_sl_risk_pct": live_trade_stats["open_sl_risk_pct"],
                    "open_sl_risk_unavailable_he": None,
                },
                "broker": {
                    "label_he": "חשבון ברוקר Live Sim",
                    "open_positions": len(live.get("broker_positions") or []),
                    "closed_trades_count": live_trade_stats["closed_trades_count"],
                    "win_rate_pct": live_trade_stats["win_rate_pct"],
                    "open_sl_risk_pct": live_trade_stats["open_sl_risk_pct"],
                },
            },
            "candidates_total": live["candidates"]["total"],
            "candidates_accepted": live["candidates"]["accepted"],
            "candidates_rejected": live["candidates"]["rejected"],
            "acceptance_rate_pct": live["candidates"]["acceptance_rate_pct"],
            "orders_sent": live["candidates"].get("orders_sent"),
            "fills": live["candidates"].get("fills"),
            "positions_opened": live["candidates"].get("positions_opened"),
        },
    }
