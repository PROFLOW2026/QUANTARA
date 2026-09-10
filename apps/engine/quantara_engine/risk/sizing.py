"""Position sizing calculations."""

from __future__ import annotations

from decimal import Decimal, ROUND_DOWN

from quantara_engine.domain.types import Instrument, Portfolio, Position, RiskProfile
from quantara_engine.portfolio.currency import ACCOUNT_CURRENCY, FxRateTable


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


def fx_risk_usd(
    quantity: Decimal,
    sl_distance: Decimal,
    instrument: Instrument,
    fx_rates: FxRateTable,
) -> Decimal:
    """
    Expected account-currency (USD) loss at stop for FX quantity in base units.

    quantity is base-currency units (e.g. 1000 GBP for 0.01 lot when contract_size=100000).
    sl_distance is absolute quote-currency price distance (e.g. 1.50 JPY on GBPJPY).
    """
    quote_loss = quantity * sl_distance
    return fx_rates.quote_to_account(quote_loss, instrument.quote_currency)


def compute_actual_risk_amount(
    quantity: Decimal,
    sl_distance: Decimal,
    instrument: Instrument | None = None,
    fx_rates: FxRateTable | None = None,
) -> Decimal:
    rates = fx_rates or FxRateTable.usd_only()
    if instrument is not None and _is_forex(instrument):
        return fx_risk_usd(quantity, sl_distance, instrument, rates)
    return (quantity * sl_distance).quantize(Decimal("0.01"))


def _desired_quantity_for_risk(
    target_risk: Decimal,
    sl_distance: Decimal,
    instrument: Instrument,
    fx_rates: FxRateTable,
) -> Decimal:
    quote = (instrument.quote_currency or ACCOUNT_CURRENCY).upper()
    if _is_forex(instrument) and quote == "JPY":
        jpy_per_usd = fx_rates.quote_per_usd.get("JPY")
        if jpy_per_usd is None or jpy_per_usd <= 0:
            raise ValueError("JPY/USD rate required for JPY-quoted FX sizing")
        return target_risk * jpy_per_usd / sl_distance
    if quote == ACCOUNT_CURRENCY:
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
    fx_rates: FxRateTable | None = None,
) -> tuple[Decimal, Decimal, Decimal, str | None]:
    """
    Returns (quantity, target_risk_amount, actual_risk_amount, denial_reason).
    """
    rates = fx_rates or FxRateTable.usd_only()
    target_risk = compute_target_risk_amount(portfolio, risk_profile)
    sl_distance = abs(entry_reference - stop_loss)
    if sl_distance <= 0:
        return Decimal("0"), target_risk, Decimal("0"), "INVALID_STOP_LOSS (zero distance)"

    desired_quantity = _desired_quantity_for_risk(target_risk, sl_distance, instrument, rates)

    if not allow_virtual_leverage:
        current_exposure = Decimal("0")
        for p in open_positions:
            current_exposure += rates.quote_notional_to_account(
                p.quantity, mark_price, instrument.quote_currency
            )
        max_exposure = portfolio.equity * risk_profile.max_total_exposure_pct / Decimal("100")
        remaining_headroom = max(Decimal("0"), max_exposure - current_exposure)
        if mark_price > 0:
            max_qty_by_exposure = remaining_headroom / (
                mark_price / rates.quote_per_usd.get(instrument.quote_currency.upper(), Decimal("1"))
                if (instrument.quote_currency or ACCOUNT_CURRENCY).upper() != ACCOUNT_CURRENCY
                else mark_price
            )
            desired_quantity = min(desired_quantity, max_qty_by_exposure)

        available = portfolio.balance - portfolio.reserved_capital
        if mark_price > 0 and available > 0:
            usd_per_unit = mark_price
            quote = (instrument.quote_currency or ACCOUNT_CURRENCY).upper()
            if quote != ACCOUNT_CURRENCY:
                rate = rates.quote_per_usd.get(quote)
                if rate and rate > 0:
                    usd_per_unit = mark_price / rate
            max_qty_by_capital = available / usd_per_unit
            desired_quantity = min(desired_quantity, max_qty_by_capital)

    actual_quantity = round_quantity(desired_quantity, instrument.quantity_step)
    if actual_quantity <= 0 and desired_quantity > 0:
        actual_quantity = instrument.min_quantity
    actual_quantity = max(actual_quantity, Decimal("0"))
    actual_risk = compute_actual_risk_amount(actual_quantity, sl_distance, instrument, rates)

    if actual_quantity < instrument.min_quantity:
        return Decimal("0"), target_risk, actual_risk, "QUANTITY_BELOW_MINIMUM after caps"

    return actual_quantity, target_risk, actual_risk, None
