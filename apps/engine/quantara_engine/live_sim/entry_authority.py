"""Canonical Live Sim entry authority — physical fill required before shadow OPEN."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import text

from quantara_engine.broker.execution_service import BrokerExecutionResult
from quantara_engine.persistence.store import TradingStore


def live_sim_physical_entry_succeeded(result: BrokerExecutionResult | None) -> bool:
    """True only when a non-shadow physical entry fill opened broker exposure."""
    if result is None or not result.accepted:
        return False
    if result.shadow_only:
        return False
    if not result.broker_fill_id or not result.broker_order_id:
        return False
    opened_qty = Decimal(str(result.physical_opened_qty or 0))
    if opened_qty <= 0:
        return False
    return True


def physical_opened_qty_for_fill(
    store: TradingStore,
    *,
    broker_fill_id: str,
    strategy_position_id: str | None = None,
) -> Decimal:
    """Opened quantity attributed to a broker entry fill (not close slices)."""
    row = store.session.execute(
        text(
            """
            SELECT COALESCE(SUM(l.remaining_qty), 0) AS rem,
                   COALESCE(SUM(
                     CASE WHEN bl.quantity IS NOT NULL THEN bl.quantity ELSE 0 END
                   ), 0) AS opened
            FROM broker_attribution_lots l
            LEFT JOIN broker_attribution_ledger bl
              ON bl.broker_fill_id = l.broker_fill_id
             AND bl.strategy_position_id IS NOT DISTINCT FROM l.strategy_position_id
            WHERE l.broker_fill_id = CAST(:fid AS uuid)
              AND (
                :spid IS NULL
                OR l.strategy_position_id = CAST(:spid AS uuid)
              )
            """
        ),
        {"fid": broker_fill_id, "spid": strategy_position_id},
    ).mappings().first()
    if not row:
        return Decimal("0")
    rem = Decimal(str(row["rem"] or 0))
    opened = Decimal(str(row["opened"] or 0))
    return rem if rem > 0 else opened


def broker_signed_net_quantity(
    store: TradingStore,
    *,
    broker_account_id: str,
    symbol: str,
) -> Decimal:
    row = store.session.execute(
        text(
            """
            SELECT bp.net_quantity
            FROM broker_positions bp
            JOIN instruments i ON i.id = bp.instrument_id
            WHERE bp.broker_account_id = CAST(:aid AS uuid)
              AND i.symbol = :sym
            """
        ),
        {"aid": broker_account_id, "sym": symbol.upper()},
    ).scalar()
    return Decimal(str(row or 0))


def live_sim_entry_broker_exposure_ok(
    store: TradingStore,
    *,
    broker_account_id: str,
    symbol: str,
    direction: str,
    minimum_qty: Decimal,
) -> bool:
    """Broker book must still carry exposure in the entry direction at create time."""
    net = broker_signed_net_quantity(
        store, broker_account_id=broker_account_id, symbol=symbol
    )
    min_q = max(Decimal("0"), minimum_qty)
    if direction.lower() == "long":
        return net >= min_q
    return net <= -min_q


def resolve_live_sim_entry_quantity(
    store: TradingStore,
    *,
    broker_res: BrokerExecutionResult,
    strategy_position_id: str,
    requested_qty: Decimal,
) -> Decimal:
    """Prefer physically opened quantity over requested sizing."""
    opened = Decimal(str(broker_res.physical_opened_qty or 0))
    if opened <= 0 and broker_res.broker_fill_id:
        opened = physical_opened_qty_for_fill(
            store,
            broker_fill_id=broker_res.broker_fill_id,
            strategy_position_id=strategy_position_id,
        )
    if opened > 0:
        return opened
    return requested_qty


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
