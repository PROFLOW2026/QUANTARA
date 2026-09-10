"""Position sizing calculations."""

from __future__ import annotations

from decimal import Decimal, ROUND_DOWN

from quantara_engine.domain.types import Instrument, Portfolio, Position, RiskProfile

# Paper USD account: approximate JPY/USD for quote-currency conversion on JPY pairs.
_JPY_PER_USD = Decimal("150")


def round_quantity(quantity: Decimal, step: Decimal) -> Decimal:
    if step <= 0:
        return quantity
    units = (quantity / step).quantize(Decimal("1"), rounding=ROUND_DOWN)
    return (units * step).quantize(step)


def compute_target_risk_amount(portfolio: Portfolio, risk_profile: RiskProfile) -> Decimal:
    return (portfolio.equity * risk_profile.risk_per_trade_pct / Decimal("100")).quantize(
        Decimal("0.01")
    )


def _is_forex(instrument: Instrument) -> bool:
    return (instrument.asset_class or "").lower() == "forex"


def _effective_quote_currency(instrument: Instrument) -> str:
    """Infer quote currency — seed data may label FX pairs as USD incorrectly."""
    sym = (instrument.symbol or "").upper()
    if len(sym) >= 6 and sym.endswith("JPY"):
        return "JPY"
    if len(sym) >= 6 and sym.endswith("USD"):
        return "USD"
    return (instrument.quote_currency or "USD").upper()


def _quote_to_usd_factor(instrument: Instrument) -> Decimal:
    quote = _effective_quote_currency(instrument)
    if quote == "USD":
        return Decimal("1")
    if quote == "JPY":
        return Decimal("1") / _JPY_PER_USD
    return Decimal("1")


def fx_risk_usd(quantity: Decimal, sl_distance: Decimal, instrument: Instrument) -> Decimal:
    """
    Expected account-currency (USD) loss at stop for FX quantity in base units.

    quantity is base-currency units (e.g. 1000 GBP for 0.01 lot when contract_size=100000).
    sl_distance is absolute quote-currency price distance (e.g. 1.50 JPY on GBPJPY).
    """
    quote_loss = quantity * sl_distance
    return (quote_loss * _quote_to_usd_factor(instrument)).quantize(Decimal("0.01"))


def compute_actual_risk_amount(
    quantity: Decimal,
    sl_distance: Decimal,
    instrument: Instrument | None = None,
) -> Decimal:
    if instrument is not None and _is_forex(instrument):
        return fx_risk_usd(quantity, sl_distance, instrument)
    return (quantity * sl_distance).quantize(Decimal("0.01"))


def _desired_quantity_for_risk(
    target_risk: Decimal,
    sl_distance: Decimal,
    instrument: Instrument,
) -> Decimal:
    if _is_forex(instrument):
        quote = _effective_quote_currency(instrument)
        if quote == "JPY":
            # target_risk USD = qty * sl_distance JPY / JPY_USD
            return target_risk * _JPY_PER_USD / sl_distance
        if quote == "USD":
            return target_risk / sl_distance
        return target_risk / sl_distance
    return target_risk / sl_distance


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

    desired_quantity = _desired_quantity_for_risk(target_risk, sl_distance, instrument)

    if not allow_virtual_leverage:
        current_exposure = sum(p.quantity * mark_price for p in open_positions)
        max_exposure = portfolio.equity * risk_profile.max_total_exposure_pct / Decimal("100")
        remaining_headroom = max(Decimal("0"), max_exposure - current_exposure)
        if mark_price > 0:
            max_qty_by_exposure = remaining_headroom / mark_price
            desired_quantity = min(desired_quantity, max_qty_by_exposure)

        available = portfolio.balance - portfolio.reserved_capital
        if mark_price > 0 and available > 0:
            max_qty_by_capital = available / mark_price
            desired_quantity = min(desired_quantity, max_qty_by_capital)

    actual_quantity = round_quantity(desired_quantity, instrument.quantity_step)
    if actual_quantity <= 0 and desired_quantity > 0:
        actual_quantity = instrument.min_quantity
    actual_quantity = max(actual_quantity, Decimal("0"))
    actual_risk = compute_actual_risk_amount(actual_quantity, sl_distance, instrument)

    if actual_quantity < instrument.min_quantity:
        return Decimal("0"), target_risk, actual_risk, "QUANTITY_BELOW_MINIMUM after caps"

    return actual_quantity, target_risk, actual_risk, None
