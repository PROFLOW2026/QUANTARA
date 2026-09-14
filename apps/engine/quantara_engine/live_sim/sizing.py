"""Live-sim position sizing — risk budget with broker-safe buying-power caps."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_EVEN

from quantara_engine.broker.instruments import get_instrument_spec
from quantara_engine.broker.margin import initial_margin_for_notional, quote_notional_usd
from quantara_engine.domain.types import Direction, ExecutionAssumptions, Instrument
from quantara_engine.execution.fill_calculator import calculate_fill_price
from quantara_engine.portfolio.currency import FxRateTable
from quantara_engine.risk.sizing import (
    _desired_quantity_for_risk,
    round_quantity,
    select_quantity_for_risk_budget,
)

BROKER_LEVERAGE_QUANTIZE = Decimal("0.0001")
# Keep notional strictly below max asset leverage after broker-side quantization.
LIVE_SIM_NOTIONAL_HEADROOM_USD = Decimal("2.00")


@dataclass(frozen=True)
class LiveSimSizingResult:
    quantity: Decimal
    expected_risk_usd: Decimal
    target_risk_usd: Decimal
    deny_reason: str | None
    sizing_reason: str | None = None
    headroom_notional_usd: Decimal | None = None


def entry_mark_price_for_sizing(
    base_price: Decimal,
    direction: Direction,
    assumptions: ExecutionAssumptions,
) -> Decimal:
    """Conservative entry mark — matches broker pre-trade (fill price, not mid)."""
    fill = calculate_fill_price(
        direction,
        "entry",
        base_price,
        Decimal("1"),
        assumptions,
    )
    return fill.fill_price


def broker_quantized_asset_leverage(notional: Decimal, equity: Decimal) -> Decimal:
    if equity <= 0:
        return Decimal("999")
    return (notional / equity).quantize(BROKER_LEVERAGE_QUANTIZE, rounding=ROUND_HALF_EVEN)


def max_safe_quantity_for_live_sim(
    *,
    equity: Decimal,
    cash: Decimal,
    current_asset_notional: Decimal,
    entry_base_price: Decimal,
    direction: Direction,
    instrument: Instrument,
    execution_assumptions: ExecutionAssumptions | None,
    max_asset_leverage: Decimal,
    fx_rates: FxRateTable | None = None,
    product_rules=None,
) -> tuple[Decimal, Decimal]:
    """
    Maximum quantity that satisfies broker asset-leverage quantization and cash.

    Returns (quantity, headroom_notional_usd) where headroom is remaining notional
    budget before hitting max_asset_leverage at the execution mark price.
    """
    if execution_assumptions is None:
        execution_assumptions = ExecutionAssumptions()
    if equity <= 0 or entry_base_price <= 0:
        return Decimal("0"), Decimal("0")

    mark = entry_mark_price_for_sizing(entry_base_price, direction, execution_assumptions)
    if mark <= 0:
        return Decimal("0"), Decimal("0")

    db_sym = instrument.symbol.upper().replace("/", "")
    spec = get_instrument_spec(db_sym)
    fx_table = fx_rates or FxRateTable.usd_only()
    fx = fx_table.quote_per_usd

    headroom = LIVE_SIM_NOTIONAL_HEADROOM_USD
    max_total_notional = max(Decimal("0"), equity * max_asset_leverage - headroom)
    remaining_leverage_notional = max(Decimal("0"), max_total_notional - current_asset_notional)

    # Unit economics must be USD — never divide USD headroom by a non-USD quote price.
    unit_notional_usd = quote_notional_usd(Decimal("1"), mark, spec, fx)
    max_by_lev_qty = (
        round_quantity(remaining_leverage_notional / unit_notional_usd, instrument.quantity_step)
        if remaining_leverage_notional > 0 and unit_notional_usd > 0
        else Decimal("0")
    )

    if direction == Direction.LONG:
        fee_rate = execution_assumptions.fee_rate or Decimal("0")
        # Align cash headroom with broker initial-margin economics (not full quote notional).
        from quantara_engine.broker.profile import QUANTARA_LIVE_SIM_10K

        im_rules = product_rules if product_rules is not None else QUANTARA_LIVE_SIM_10K.rules_for(
            spec.asset_class
        )
        unit_im = initial_margin_for_notional(unit_notional_usd, im_rules)
        cash_per_unit = unit_im * (Decimal("1") + fee_rate)
        max_by_cash_qty = (
            round_quantity(cash / cash_per_unit, instrument.quantity_step)
            if cash > 0 and cash_per_unit > 0
            else Decimal("0")
        )
        start_qty = min(max_by_lev_qty, max_by_cash_qty)
    else:
        start_qty = max_by_lev_qty

    qty = start_qty
    while qty >= instrument.min_quantity:
        projected = current_asset_notional + quote_notional_usd(qty, mark, spec, fx)
        lev = broker_quantized_asset_leverage(projected, equity)
        # Match broker pre-trade strict comparison (reject when lev > max).
        if lev <= max_asset_leverage and projected <= max_total_notional:
            remaining = max(Decimal("0"), max_total_notional - projected)
            return qty, remaining
        qty = round_quantity(qty - instrument.quantity_step, instrument.quantity_step)

    return Decimal("0"), Decimal("0")


def _cap_desired_by_buying_power(
    desired_quantity: Decimal,
    *,
    equity: Decimal,
    cash: Decimal,
    current_asset_notional: Decimal,
    entry_base_price: Decimal,
    direction: Direction,
    instrument: Instrument,
    execution_assumptions: ExecutionAssumptions | None,
    max_asset_leverage: Decimal,
    fx_rates: FxRateTable | None = None,
    product_rules=None,
) -> tuple[Decimal, str | None, Decimal]:
    safe_qty, headroom = max_safe_quantity_for_live_sim(
        equity=equity,
        cash=cash,
        current_asset_notional=current_asset_notional,
        entry_base_price=entry_base_price,
        direction=direction,
        instrument=instrument,
        execution_assumptions=execution_assumptions,
        max_asset_leverage=max_asset_leverage,
        fx_rates=fx_rates,
        product_rules=product_rules,
    )
    if safe_qty < instrument.min_quantity:
        return Decimal("0"), "buying_power_cap", headroom
    if safe_qty < desired_quantity:
        return safe_qty, "buying_power_cap", headroom
    return desired_quantity, None, headroom


def size_live_sim_entry(
    *,
    equity: Decimal,
    cash: Decimal,
    target_risk: Decimal,
    entry_reference: Decimal,
    stop_loss: Decimal,
    direction: Direction,
    instrument: Instrument,
    fx_rates: FxRateTable,
    execution_assumptions: ExecutionAssumptions | None,
    max_asset_leverage: Decimal = Decimal("1"),
    current_asset_notional: Decimal = Decimal("0"),
    product_rules=None,
    hard_max_risk_usd: Decimal | None = None,
    risk_rounding_tolerance_pct: Decimal | None = None,
) -> LiveSimSizingResult:
    from quantara_engine.risk.sizing import DEFAULT_RISK_ROUNDING_TOLERANCE_PCT

    sl_distance = abs(entry_reference - stop_loss)
    if sl_distance <= 0:
        return LiveSimSizingResult(
            quantity=Decimal("0"),
            expected_risk_usd=Decimal("0"),
            target_risk_usd=target_risk,
            deny_reason="INVALID_STOP_LOSS",
        )

    desired = _desired_quantity_for_risk(target_risk, sl_distance, instrument, fx_rates)
    sizing_reason: str | None = "risk_budget"
    headroom = Decimal("0")
    desired, bp_reason, headroom = _cap_desired_by_buying_power(
        desired,
        equity=equity,
        cash=cash,
        current_asset_notional=current_asset_notional,
        entry_base_price=entry_reference,
        direction=direction,
        instrument=instrument,
        execution_assumptions=execution_assumptions,
        max_asset_leverage=max_asset_leverage,
        fx_rates=fx_rates,
        product_rules=product_rules,
    )
    if bp_reason:
        sizing_reason = bp_reason

    tol = (
        risk_rounding_tolerance_pct
        if risk_rounding_tolerance_pct is not None
        else DEFAULT_RISK_ROUNDING_TOLERANCE_PCT
    )
    qty, expected_risk, deny = select_quantity_for_risk_budget(
        target_risk=target_risk,
        desired_quantity=desired,
        instrument=instrument,
        direction=direction,
        entry_reference=entry_reference,
        stop_loss=stop_loss,
        fx_rates=fx_rates,
        execution_assumptions=execution_assumptions,
        tolerance_pct=tol,
        hard_max_risk_usd=hard_max_risk_usd,
    )
    if deny or qty <= 0:
        return LiveSimSizingResult(
            quantity=Decimal("0"),
            expected_risk_usd=expected_risk,
            target_risk_usd=target_risk,
            deny_reason=deny or "MIN_QUANTITY",
            sizing_reason=sizing_reason,
            headroom_notional_usd=headroom,
        )
    if qty < desired and sizing_reason == "risk_budget":
        sizing_reason = "risk_budget_stepped"
    return LiveSimSizingResult(
        quantity=qty,
        expected_risk_usd=expected_risk,
        target_risk_usd=target_risk,
        deny_reason=None,
        sizing_reason=sizing_reason,
        headroom_notional_usd=headroom,
    )


def validate_live_sim_broker_pre_trade(
    *,
    equity: Decimal,
    cash: Decimal,
    spot_crypto_cash: Decimal,
    quantity: Decimal,
    mark_price: Decimal,
    direction: Direction,
    instrument: Instrument,
    profile,
    fx_rates: FxRateTable | None = None,
    account_slug: str | None = None,
    store=None,
    execution_model=None,
) -> tuple[bool, str | None]:
    """Return (accepted, rejection_reason) using the same broker gate as execution."""
    from quantara_engine.broker.account import build_account_snapshot
    from quantara_engine.broker.accounts import LIVE_SIM_VENDOR_ACCOUNT_SLUGS
    from quantara_engine.broker.execution_model import ExecutionModelVersion
    from quantara_engine.broker.execution_product import route_execution_product
    from quantara_engine.broker.pre_trade import evaluate_broker_order
    from quantara_engine.broker.spot_crypto_cash import effective_spot_crypto_cash
    from quantara_engine.broker.types import BrokerOrderRequest
    from quantara_engine.live_sim.execution_routing import resolve_execution_model_for_slug

    db_sym = instrument.symbol.upper().replace("/", "")
    spec = get_instrument_spec(db_sym)
    dir_str = "long" if direction == Direction.LONG else "short"

    model = execution_model
    if model is None and store is not None and account_slug:
        model = resolve_execution_model_for_slug(store, account_slug)
    elif model is None and account_slug in LIVE_SIM_VENDOR_ACCOUNT_SLUGS:
        # Vendor Live Sim accounts are realistic even in store-less unit tests.
        model = ExecutionModelVersion.REALISTIC_BROKER_V1

    route = (
        route_execution_product(db_sym, dir_str, execution_model=model)
        if model is not None
        else None
    )

    fx = (fx_rates or FxRateTable.usd_only()).quote_per_usd
    account = build_account_snapshot(
        cash=cash,
        balance=cash,
        realized_pnl=Decimal("0"),
        positions={},
        fx_rates=fx,
        spot_crypto_cash=effective_spot_crypto_cash(
            cash=cash,
            spot_crypto_cash=spot_crypto_cash,
        ),
    )
    request = BrokerOrderRequest(
        symbol=db_sym,
        asset_class=route.asset_class_key if route else spec.asset_class,
        direction=dir_str,
        quantity=quantity,
        mark_price=mark_price,
        data_fresh=True,
        market_open=True,
        execution_product=route.product.value if route else None,
        product_rules_key=route.asset_class_key if route else None,
        account_slug=account_slug,
    )
    decision = evaluate_broker_order(
        account,
        profile,
        request,
        fx,
        product_rules=route.rules if route else None,
    )
    if decision.accepted:
        return True, None
    reason = decision.rejection_reason.value if decision.rejection_reason else "broker_rejected"
    return False, reason
