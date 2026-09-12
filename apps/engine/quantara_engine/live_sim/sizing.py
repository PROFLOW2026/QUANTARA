"""Live-sim position sizing — risk budget with buying-power and constraint caps."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from quantara_engine.domain.types import Direction, ExecutionAssumptions, Instrument
from quantara_engine.portfolio.currency import FxRateTable
from quantara_engine.risk.sizing import (
    _desired_quantity_for_risk,
    round_quantity,
    select_quantity_for_risk_budget,
)


@dataclass(frozen=True)
class LiveSimSizingResult:
    quantity: Decimal
    expected_risk_usd: Decimal
    target_risk_usd: Decimal
    deny_reason: str | None
    sizing_reason: str | None = None


def _cap_desired_by_buying_power(
    desired_quantity: Decimal,
    *,
    cash: Decimal,
    entry_reference: Decimal,
    instrument: Instrument,
) -> tuple[Decimal, str | None]:
    if entry_reference <= 0 or cash <= 0:
        return Decimal("0"), "buying_power_cap"
    max_by_cash = round_quantity(cash / entry_reference, instrument.quantity_step)
    if max_by_cash < instrument.min_quantity:
        return Decimal("0"), "buying_power_cap"
    if max_by_cash < desired_quantity:
        return max_by_cash, "buying_power_cap"
    return desired_quantity, None


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
) -> LiveSimSizingResult:
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
    desired, bp_reason = _cap_desired_by_buying_power(
        desired,
        cash=cash,
        entry_reference=entry_reference,
        instrument=instrument,
    )
    if bp_reason:
        sizing_reason = bp_reason

    qty, expected_risk, deny = select_quantity_for_risk_budget(
        target_risk=target_risk,
        desired_quantity=desired,
        instrument=instrument,
        direction=direction,
        entry_reference=entry_reference,
        stop_loss=stop_loss,
        fx_rates=fx_rates,
        execution_assumptions=execution_assumptions,
    )
    if deny or qty <= 0:
        return LiveSimSizingResult(
            quantity=Decimal("0"),
            expected_risk_usd=expected_risk,
            target_risk_usd=target_risk,
            deny_reason=deny or "MIN_QUANTITY",
            sizing_reason=sizing_reason,
        )
    if qty < desired and sizing_reason == "risk_budget":
        sizing_reason = "risk_budget_stepped"
    return LiveSimSizingResult(
        quantity=qty,
        expected_risk_usd=expected_risk,
        target_risk_usd=target_risk,
        deny_reason=None,
        sizing_reason=sizing_reason,
    )
