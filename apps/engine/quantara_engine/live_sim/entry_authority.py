"""Canonical Live Sim entry authority — physical fill required before shadow OPEN."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import text

from quantara_engine.broker.execution_service import BrokerExecutionResult
from quantara_engine.persistence.store import TradingStore


def live_sim_physical_entry_succeeded(result: BrokerExecutionResult | None) -> bool:
    """True only when a non-shadow physical entry fill is confirmed."""
    if result is None or not result.accepted:
        return False
    if result.shadow_only:
        return False
    if not result.broker_fill_id or not result.broker_order_id:
        return False
    fill_qty = Decimal(str(result.fill_quantity or 0))
    opened_qty = Decimal(str(result.physical_opened_qty or 0))
    if fill_qty <= 0 and opened_qty <= 0:
        return False
    return True


def create_live_sim_position_after_physical_entry(
    store: TradingStore,
    *,
    position_id: str,
    broker_account_id: str,
    instrument_id: str,
    strategy_slug: str,
    strategy_version: str,
    robot_label: str,
    timeframe: str,
    direction: str,
    quantity: Decimal,
    entry_price: Decimal,
    stop_loss: Decimal,
    take_profit: Decimal | None,
    planned_sl_risk_usd: Decimal,
    opportunity_key: str,
    canonical_opportunity_key: str,
    opened_at: datetime,
    allocation_log_id: str,
) -> None:
    """Insert live_sim_positions only after physical entry authority passes."""
    store.session.execute(
        text(
            """
            INSERT INTO live_sim_positions (
              id, broker_account_id, instrument_id, strategy_slug, strategy_version,
              robot_label, timeframe, direction, quantity, entry_price, stop_loss,
              take_profit, current_price, planned_sl_risk_usd, opportunity_key,
              canonical_opportunity_key, status, opened_at, allocation_log_id
            ) VALUES (
              :id, :aid, :iid, :slug, :ver, :robot, :tf, CAST(:dir AS direction),
              :qty, :entry, :sl, :tp, :mark, :risk, :opp, :canonical, 'open', :opened, :log_id
            )
            """
        ),
        {
            "id": position_id,
            "aid": broker_account_id,
            "iid": instrument_id,
            "slug": strategy_slug,
            "ver": strategy_version,
            "robot": robot_label,
            "tf": timeframe,
            "dir": direction,
            "qty": quantity,
            "entry": entry_price,
            "sl": stop_loss,
            "tp": take_profit,
            "mark": entry_price,
            "risk": planned_sl_risk_usd,
            "opp": opportunity_key,
            "canonical": canonical_opportunity_key,
            "opened": opened_at,
            "log_id": allocation_log_id,
        },
    )


def position_has_physical_entry_evidence(
    store: TradingStore,
    *,
    position_id: str,
    broker_account_id: str,
    symbol: str,
    opportunity_key: str | None = None,
) -> bool:
    """True when attribution lots or entry fills prove a physical Live Sim entry."""
    from quantara_engine.broker.accounts import LIVE_SIM_VIRTUAL_PORTFOLIO_ID

    row = store.session.execute(
        text(
            """
            SELECT 1
            FROM broker_attribution_lots
            WHERE broker_account_id = :aid
              AND symbol = :sym
              AND strategy_position_id = :pid
              AND remaining_qty > 0
            LIMIT 1
            """
        ),
        {"aid": broker_account_id, "sym": symbol.upper(), "pid": position_id},
    ).first()
    if row:
        return True

    if opportunity_key:
        row = store.session.execute(
            text(
                """
                SELECT 1
                FROM broker_attribution_lots
                WHERE broker_account_id = :aid
                  AND symbol = :sym
                  AND strategy_portfolio_id = :pid
                  AND opportunity_key = :opp
                  AND remaining_qty > 0
                LIMIT 1
                """
            ),
            {
                "aid": broker_account_id,
                "sym": symbol.upper(),
                "pid": LIVE_SIM_VIRTUAL_PORTFOLIO_ID,
                "opp": opportunity_key,
            },
        ).first()
        if row:
            return True

    row = store.session.execute(
        text(
            """
            SELECT 1
            FROM live_sim_allocation_log l
            JOIN broker_fills f ON f.broker_order_id = l.broker_order_id
            WHERE l.live_sim_position_id = CAST(:pid AS uuid)
              AND f.fill_quantity > 0
            LIMIT 1
            """
        ),
        {"pid": position_id},
    ).first()
    if row:
        return True

    if opportunity_key:
        row = store.session.execute(
            text(
                """
                SELECT 1
                FROM live_sim_allocation_log l
                JOIN broker_fills f ON f.broker_order_id = l.broker_order_id
                WHERE l.opportunity_key = :opp
                  AND l.broker_account_id = :aid
                  AND f.fill_quantity > 0
                LIMIT 1
                """
            ),
            {"opp": opportunity_key, "aid": broker_account_id},
        ).first()
        return row is not None

    return False
