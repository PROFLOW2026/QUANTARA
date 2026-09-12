"""Physical broker remaining stop risk from attributed lots."""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import text

from quantara_engine.broker.execution_service import PAPER_ACCOUNT_SLUG
from quantara_engine.domain.types import Direction
from quantara_engine.persistence.store import TradingStore
from quantara_engine.portfolio.currency import quote_currencies_for_instruments, resolve_dashboard_fx_rates
from quantara_engine.risk.sizing import _expected_risk_at_quantity


def compute_physical_broker_risk(store: TradingStore) -> dict:
    """Remaining SL risk and projected broker equity at stops using physical attribution."""
    from quantara_engine.broker.execution_service import BrokerExecutionService

    svc = BrokerExecutionService(store)
    snap = svc.load_account_snapshot()

    rows = store.session.execute(
        text(
            """
            SELECT l.remaining_qty, l.direction::text, l.strategy_position_id::text,
                   p.stop_loss, p.current_price, i.symbol, i.id::text AS instrument_id,
                   i.quote_currency, bp.mark_price
            FROM broker_attribution_lots l
            JOIN broker_accounts ba ON ba.id = l.broker_account_id
            JOIN instruments i ON i.symbol = l.symbol
            LEFT JOIN positions p ON p.id = l.strategy_position_id
            LEFT JOIN broker_positions bp ON bp.broker_account_id = ba.id
              AND bp.instrument_id = i.id
            WHERE ba.slug = :slug AND l.remaining_qty > 0
            """
        ),
        {"slug": PAPER_ACCOUNT_SLUG},
    ).mappings().all()

    if not rows:
        equity = snap.equity if snap.equity is not None else Decimal("0")
        return {
            "physical_remaining_sl_risk_usd": Decimal("0"),
            "projected_broker_equity_at_stops": equity.quantize(Decimal("0.01")),
            "physical_risk_complete": True,
            "physical_risk_missing_count": 0,
            "attributed_lot_count": 0,
        }

    instrument_ids = list({str(r["instrument_id"]) for r in rows})
    instruments = [store.get_instrument_by_id(iid) for iid in instrument_ids]
    instruments = [i for i in instruments if i]
    fx = resolve_dashboard_fx_rates(store, quote_currencies_for_instruments(instruments))

    total_risk = Decimal("0")
    missing_count = 0
    for r in rows:
        qty = Decimal(str(r["remaining_qty"]))
        stop_raw = r["stop_loss"]
        if stop_raw is None or Decimal(str(stop_raw)) <= 0:
            missing_count += 1
            continue
        stop = Decimal(str(stop_raw))
        mark_raw = r["mark_price"] or r["current_price"]
        if mark_raw is None or Decimal(str(mark_raw)) <= 0:
            missing_count += 1
            continue
        mark = Decimal(str(mark_raw))
        instrument = store.get_instrument_by_id(str(r["instrument_id"]))
        if not instrument:
            missing_count += 1
            continue
        direction = Direction.LONG if str(r["direction"]).lower() == "long" else Direction.SHORT
        try:
            risk = _expected_risk_at_quantity(
                qty,
                direction=direction,
                entry_reference=mark,
                stop_loss=stop,
                instrument=instrument,
                fx_rates=fx,
                execution_assumptions=None,
            )
            if risk is None:
                missing_count += 1
                continue
            total_risk += risk
        except (ValueError, ZeroDivisionError, TypeError):
            missing_count += 1
            continue

    risk_complete = missing_count == 0
    equity = snap.equity if snap.equity is not None else None
    projected = (equity - total_risk) if equity is not None and risk_complete else None

    return {
        "physical_remaining_sl_risk_usd": (
            total_risk.quantize(Decimal("0.01")) if risk_complete else None
        ),
        "projected_broker_equity_at_stops": (
            projected.quantize(Decimal("0.01")) if projected is not None else None
        ),
        "physical_risk_complete": risk_complete,
        "physical_risk_missing_count": missing_count,
        "attributed_lot_count": len(rows),
    }
