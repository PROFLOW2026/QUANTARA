"""Position sizing calculations."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN

from quantara_engine.domain.types import Direction, ExecutionAssumptions, Instrument, Portfolio, Position, RiskProfile
from quantara_engine.execution.economics import executable_sl_distance
from quantara_engine.portfolio.currency import ACCOUNT_CURRENCY, FxRateTable

# Owner approval pending — 2% recommended after tolerance sweep (see audit script).
DEFAULT_RISK_ROUNDING_TOLERANCE_PCT = Decimal("2")


@dataclass(frozen=True)
class SizingResult:
    quantity: Decimal
    target_risk_amount: Decimal
    expected_risk_amount: Decimal
    denial_reason: str | None
    theoretical_quantity: Decimal
    max_allowed_risk: Decimal


def round_quantity(quantity: Decimal, step: Decimal) -> Decimal:
    if step <= 0:
        return quantity
    units = (quantity / step).quantize(Decimal("1"), rounding=ROUND_DOWN)
    return (units * step).quantize(step)


def compute_target_risk_amount(portfolio: Portfolio, risk_profile: RiskProfile) -> Decimal:
    return (portfolio.equity * risk_profile.risk_per_trade_pct / Decimal("100")).quantize(
        Decimal("0.01")
    )


def max_allowed_risk_amount(
    target_risk: Decimal, tolerance_pct: Decimal = DEFAULT_RISK_ROUNDING_TOLERANCE_PCT
) -> Decimal:
    return (target_risk * (Decimal("1") + tolerance_pct / Decimal("100"))).quantize(Decimal("0.01"))


def _is_forex(instrument: Instrument) -> bool:
    return (instrument.asset_class or "").lower() == "forex"


def fx_risk_usd(
    quantity: Decimal,
    sl_distance: Decimal,
    instrument: Instrument,
    fx_rates: FxRateTable,
) -> Decimal:
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


def _executable_sl_distance_for_qty(
    direction: Direction,
    entry_reference: Decimal,
    stop_loss: Decimal,
    quantity: Decimal,
    execution_assumptions: ExecutionAssumptions | None,
) -> Decimal:
    if execution_assumptions is not None:
        return executable_sl_distance(
            direction, entry_reference, stop_loss, quantity, execution_assumptions
        )
    return abs(entry_reference - stop_loss)


def _expected_risk_at_quantity(
    quantity: Decimal,
    *,
    direction: Direction,
    entry_reference: Decimal,
    stop_loss: Decimal,
    instrument: Instrument,
    fx_rates: FxRateTable,
    execution_assumptions: ExecutionAssumptions | None,
) -> Decimal:
    sl_distance = _executable_sl_distance_for_qty(
        direction, entry_reference, stop_loss, quantity, execution_assumptions
    )
    return compute_actual_risk_amount(quantity, sl_distance, instrument, fx_rates)


def generate_quantity_candidates(
    desired_quantity: Decimal,
    quantity_step: Decimal,
    min_quantity: Decimal,
) -> list[Decimal]:
    if quantity_step <= 0:
        return [desired_quantity] if desired_quantity >= min_quantity else []

    floor_q = round_quantity(desired_quantity, quantity_step)
    candidates: set[Decimal] = set()
    if floor_q >= min_quantity:
        candidates.add(floor_q)
    ceil_q = floor_q + quantity_step
    if ceil_q >= min_quantity:
        candidates.add(ceil_q)
    if min_quantity > 0:
        candidates.add(min_quantity)
    return sorted(q for q in candidates if q > 0)


def select_quantity_for_risk_budget(
    *,
    target_risk: Decimal,
    desired_quantity: Decimal,
    instrument: Instrument,
    direction: Direction,
    entry_reference: Decimal,
    stop_loss: Decimal,
    fx_rates: FxRateTable,
    execution_assumptions: ExecutionAssumptions | None,
    tolerance_pct: Decimal = DEFAULT_RISK_ROUNDING_TOLERANCE_PCT,
) -> tuple[Decimal, Decimal, str | None]:
    """
    Pick valid stepped quantity closest to target risk without exceeding tolerance cap.
    Never force min_quantity when it would materially exceed the risk budget.
    """
    max_risk = max_allowed_risk_amount(target_risk, tolerance_pct)
    min_qty = instrument.min_quantity
    step = instrument.quantity_step

    min_qty_risk = _expected_risk_at_quantity(
        min_qty,
        direction=direction,
        entry_reference=entry_reference,
        stop_loss=stop_loss,
        instrument=instrument,
        fx_rates=fx_rates,
        execution_assumptions=execution_assumptions,
    )
    if min_qty_risk > max_risk:
        return Decimal("0"), min_qty_risk, "MIN_QUANTITY_EXCEEDS_RISK_BUDGET"

    floor_q = round_quantity(desired_quantity, step)
    if floor_q < min_qty:
        floor_q = min_qty

    # Walk down from risk-sized floor — spread-aware risk rises with quantity, so the
    # largest valid stepped qty under budget is typically below floor, not min_qty.
    best_qty = Decimal("0")
    best_risk = min_qty_risk
    q = floor_q
    while q >= min_qty:
        risk = _expected_risk_at_quantity(
            q,
            direction=direction,
            entry_reference=entry_reference,
            stop_loss=stop_loss,
            instrument=instrument,
            fx_rates=fx_rates,
            execution_assumptions=execution_assumptions,
        )
        if risk <= max_risk:
            best_qty, best_risk = q, risk
            break
        q -= step

    if best_qty <= 0:
        return Decimal("0"), min_qty_risk, "MIN_QUANTITY_EXCEEDS_RISK_BUDGET"

    return best_qty, best_risk, None


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
    execution_assumptions: ExecutionAssumptions | None = None,
    risk_rounding_tolerance_pct: Decimal = DEFAULT_RISK_ROUNDING_TOLERANCE_PCT,
) -> tuple[Decimal, Decimal, Decimal, str | None]:
    """
    Returns (quantity, target_risk_amount, expected_risk_at_entry, denial_reason).
    """
    rates = fx_rates or FxRateTable.usd_only()
    target_risk = compute_target_risk_amount(portfolio, risk_profile)
    dir_enum = Direction.LONG if direction == "long" else Direction.SHORT

    probe_qty = instrument.min_quantity
    sl_distance = _executable_sl_distance_for_qty(
        dir_enum, entry_reference, stop_loss, probe_qty, execution_assumptions
    )
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

    actual_quantity, actual_risk, deny = select_quantity_for_risk_budget(
        target_risk=target_risk,
        desired_quantity=desired_quantity,
        instrument=instrument,
        direction=dir_enum,
        entry_reference=entry_reference,
        stop_loss=stop_loss,
        fx_rates=rates,
        execution_assumptions=execution_assumptions,
        tolerance_pct=risk_rounding_tolerance_pct,
    )

    if deny:
        return Decimal("0"), target_risk, actual_risk, deny

    if actual_quantity < instrument.min_quantity:
        return Decimal("0"), target_risk, actual_risk, "QUANTITY_BELOW_MINIMUM after caps"

    return actual_quantity, target_risk, actual_risk, None


def compute_position_size_detailed(
    portfolio: Portfolio,
    risk_profile: RiskProfile,
    instrument: Instrument,
    entry_reference: Decimal,
    stop_loss: Decimal,
    direction: str,
    open_positions: list[Position],
    mark_price: Decimal,
    **kwargs,
) -> SizingResult:
    tolerance = kwargs.pop("risk_rounding_tolerance_pct", DEFAULT_RISK_ROUNDING_TOLERANCE_PCT)
    rates = kwargs.get("fx_rates") or FxRateTable.usd_only()
    dir_enum = Direction.LONG if direction == "long" else Direction.SHORT
    assumptions = kwargs.get("execution_assumptions")
    target = compute_target_risk_amount(portfolio, risk_profile)
    probe = instrument.min_quantity
    sl_dist = _executable_sl_distance_for_qty(
        dir_enum, entry_reference, stop_loss, probe, assumptions
    )
    theoretical = (
        _desired_quantity_for_risk(target, sl_dist, instrument, rates) if sl_dist > 0 else Decimal("0")
    )
    qty, target_risk, actual, deny = compute_position_size(
        portfolio,
        risk_profile,
        instrument,
        entry_reference,
        stop_loss,
        direction,
        open_positions,
        mark_price,
        risk_rounding_tolerance_pct=tolerance,
        **kwargs,
    )
    return SizingResult(
        quantity=qty,
        target_risk_amount=target_risk,
        expected_risk_amount=actual,
        denial_reason=deny,
        theoretical_quantity=theoretical,
        max_allowed_risk=max_allowed_risk_amount(target, tolerance),
    )
