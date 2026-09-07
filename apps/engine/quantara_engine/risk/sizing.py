"""Position sizing calculations."""

from __future__ import annotations

from decimal import Decimal, ROUND_DOWN

from quantara_engine.domain.types import Instrument, Portfolio, Position, RiskProfile


def round_quantity(quantity: Decimal, step: Decimal) -> Decimal:
    if step <= 0:
        return quantity
    units = (quantity / step).quantize(Decimal("1"), rounding=ROUND_DOWN)
    return (units * step).quantize(step)


def compute_target_risk_amount(portfolio: Portfolio, risk_profile: RiskProfile) -> Decimal:
    return (portfolio.equity * risk_profile.risk_per_trade_pct / Decimal("100")).quantize(
        Decimal("0.01")
    )


def compute_actual_risk_amount(quantity: Decimal, sl_distance: Decimal) -> Decimal:
    return (quantity * sl_distance).quantize(Decimal("0.01"))


def compute_position_size(
    portfolio: Portfolio,
    risk_profile: RiskProfile,
    instrument: Instrument,
    entry_reference: Decimal,
    stop_loss: Decimal,
    direction: str,
    open_positions: list[Position],
    mark_price: Decimal,
    *,
    allow_virtual_leverage: bool = False,
) -> tuple[Decimal, Decimal, Decimal, str | None]:
    """
    Returns (quantity, target_risk_amount, actual_risk_amount, denial_reason).
    """
    target_risk = compute_target_risk_amount(portfolio, risk_profile)
    sl_distance = abs(entry_reference - stop_loss)
    if sl_distance <= 0:
        return Decimal("0"), target_risk, Decimal("0"), "INVALID_STOP_LOSS (zero distance)"

    desired_quantity = target_risk / sl_distance

    if not allow_virtual_leverage:
        # Exposure cap (legacy / standard paper)
        current_exposure = sum(p.quantity * mark_price for p in open_positions)
        max_exposure = portfolio.equity * risk_profile.max_total_exposure_pct / Decimal("100")
        remaining_headroom = max(Decimal("0"), max_exposure - current_exposure)
        if mark_price > 0:
            max_qty_by_exposure = remaining_headroom / mark_price
            desired_quantity = min(desired_quantity, max_qty_by_exposure)

        # Available capital cap (Phase 1: reserved = exposure)
        available = portfolio.balance - portfolio.reserved_capital
        if mark_price > 0 and available > 0:
            max_qty_by_capital = available / mark_price
            desired_quantity = min(desired_quantity, max_qty_by_capital)

    actual_quantity = round_quantity(desired_quantity, instrument.quantity_step)
    actual_quantity = max(actual_quantity, Decimal("0"))
    actual_risk = compute_actual_risk_amount(actual_quantity, sl_distance)

    if actual_quantity < instrument.min_quantity:
        return Decimal("0"), target_risk, actual_risk, "QUANTITY_BELOW_MINIMUM after caps"

    return actual_quantity, target_risk, actual_risk, None
