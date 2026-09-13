"""Deterministic funding/financing/borrow cost accrual for simulated leveraged positions."""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import text

from quantara_engine.broker.execution_product import EXECUTION_SIM_DEFAULTS, ExecutionProduct
from quantara_engine.broker.instruments import get_instrument_spec
from quantara_engine.broker.margin import quote_notional_usd
from quantara_engine.persistence.store import TradingStore


def _hours_since(last: datetime | None, now: datetime) -> Decimal:
    if last is None:
        return Decimal("1")  # first accrual tick
    delta = (now - last).total_seconds() / 3600
    return Decimal(str(max(0.0, delta))).quantize(Decimal("0.0001"))


def accrue_position_costs(
    store: TradingStore,
    *,
    broker_account_id: str,
    broker_position_id: str,
    symbol: str,
    net_quantity: Decimal,
    mark_price: Decimal,
    execution_product: str | None,
    fx_rates: dict[str, Decimal],
    now: datetime | None = None,
) -> Decimal:
    """Accrue and persist costs; returns total USD debited this tick."""
    if net_quantity == 0 or not execution_product:
        return Decimal("0")

    now = now or datetime.utcnow()
    spec = get_instrument_spec(symbol)
    notional = quote_notional_usd(net_quantity, mark_price, spec, fx_rates)
    if notional <= 0:
        return Decimal("0")

    product = execution_product
    total = Decimal("0")
    cost_type: str | None = None
    rate: Decimal = Decimal("0")

    row = store.session.execute(
        text(
            """
            SELECT accumulated_funding, accumulated_borrow_fee, updated_at
            FROM broker_positions WHERE id = :id
            """
        ),
        {"id": broker_position_id},
    ).mappings().first()
    if not row:
        return Decimal("0")

    hours = _hours_since(row.get("updated_at"), now)

    if product == ExecutionProduct.CRYPTO_DERIVATIVE.value:
        rate = EXECUTION_SIM_DEFAULTS["crypto_derivative_funding_rate_8h"]
        periods = hours / Decimal("8")
        total = (notional * rate * periods).quantize(Decimal("0.0001"))
        cost_type = "crypto_funding"
    elif product == ExecutionProduct.MARGIN_FX.value:
        bps = EXECUTION_SIM_DEFAULTS["fx_overnight_financing_bps"]
        daily = notional * bps / Decimal("10000")
        total = (daily * (hours / Decimal("24"))).quantize(Decimal("0.0001"))
        cost_type = "fx_financing"
    elif product == ExecutionProduct.MARGIN_GOLD.value:
        bps = EXECUTION_SIM_DEFAULTS["fx_overnight_financing_bps"]
        daily = notional * bps / Decimal("10000")
        total = (daily * (hours / Decimal("24"))).quantize(Decimal("0.0001"))
        cost_type = "gold_financing"
    elif product == ExecutionProduct.EQUITY_MARGIN_SHORT.value and net_quantity < 0:
        annual = EXECUTION_SIM_DEFAULTS["equity_borrow_fee_annual_pct"]
        total = (notional * annual * (hours / Decimal("8760"))).quantize(Decimal("0.0001"))
        cost_type = "equity_borrow"

    if total <= 0 or cost_type is None:
        return Decimal("0")

    field = "accumulated_funding" if cost_type != "equity_borrow" else "accumulated_borrow_fee"
    store.session.execute(
        text(
            f"""
            UPDATE broker_positions SET {field} = {field} + :amt, updated_at = NOW()
            WHERE id = :id
            """
        ),
        {"id": broker_position_id, "amt": total},
    )
    store.session.execute(
        text(
            """
            UPDATE broker_accounts SET
              cash = cash - :amt, balance = balance - :amt,
              realized_pnl = realized_pnl - :amt, updated_at = NOW()
            WHERE id = :aid
            """
        ),
        {"aid": broker_account_id, "amt": total},
    )
    try:
        store.session.execute(
            text(
                """
                INSERT INTO broker_cost_accrual_log (
                  broker_account_id, broker_position_id, cost_type, amount, details
                ) VALUES (:aid, :pid, :ctype, :amt, CAST(:details AS jsonb))
                """
            ),
            {
                "aid": broker_account_id,
                "pid": broker_position_id,
                "ctype": cost_type,
                "amt": total,
                "details": __import__("json").dumps(
                    {"symbol": symbol, "notional_usd": str(notional), "hours": str(hours)}
                ),
            },
        )
    except Exception:
        store.session.rollback()

    try:
        from quantara_engine.owner_portfolio.asset_allocation import is_equal_asset_mode_active
        from quantara_engine.owner_portfolio.asset_ledger import apply_asset_fill_impact
        from quantara_engine.owner_portfolio.service import LIVE_SIM_OWNER_SLUG

        if is_equal_asset_mode_active(store, LIVE_SIM_OWNER_SLUG):
            apply_asset_fill_impact(
                store,
                owner_slug=LIVE_SIM_OWNER_SLUG,
                canonical_symbol=symbol,
                funding_delta=total,
            )
    except Exception:
        pass

    return total
