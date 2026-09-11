"""Executable economics — spread-aware entry/exit previews and TP validity."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from quantara_engine.domain.types import Direction, ExecutionAssumptions, Instrument
from quantara_engine.execution.fill_calculator import FillResult, calculate_fill_price
from quantara_engine.portfolio.currency import FxRateTable


@dataclass(frozen=True)
class ExecutableEconomics:
    entry_base: Decimal
    entry_fill: Decimal
    tp_base: Decimal | None
    tp_fill: Decimal | None
    sl_base: Decimal
    sl_fill: Decimal
    gross_reward_quote: Decimal
    execution_cost_quote: Decimal
    net_reward_quote: Decimal
    expected_loss_quote: Decimal
    gross_reward_usd: Decimal
    net_reward_usd: Decimal
    expected_loss_usd: Decimal
    net_reward_risk_ratio: Decimal | None


def _quote_pnl(direction: Direction, entry: Decimal, exit_px: Decimal, qty: Decimal) -> Decimal:
    if direction == Direction.LONG:
        return (exit_px - entry) * qty
    return (entry - exit_px) * qty


def preview_fills(
    direction: Direction,
    entry_base: Decimal,
    stop_loss: Decimal,
    take_profit: Decimal | None,
    quantity: Decimal,
    assumptions: ExecutionAssumptions,
) -> tuple[FillResult, FillResult, FillResult | None]:
    entry = calculate_fill_price(direction, "entry", entry_base, quantity, assumptions)
    sl = calculate_fill_price(direction, "exit", stop_loss, quantity, assumptions)
    tp = None
    if take_profit is not None:
        tp = calculate_fill_price(direction, "exit", take_profit, quantity, assumptions)
    return entry, sl, tp


def executable_sl_distance(
    direction: Direction,
    entry_base: Decimal,
    stop_loss: Decimal,
    quantity: Decimal,
    assumptions: ExecutionAssumptions,
) -> Decimal:
    entry, sl, _ = preview_fills(direction, entry_base, stop_loss, None, quantity, assumptions)
    return abs(entry.fill_price - sl.fill_price)


def compute_executable_economics(
    direction: Direction,
    entry_base: Decimal,
    stop_loss: Decimal,
    take_profit: Decimal | None,
    quantity: Decimal,
    instrument: Instrument,
    assumptions: ExecutionAssumptions,
    fx_rates: FxRateTable,
) -> ExecutableEconomics:
    entry, sl, tp = preview_fills(
        direction, entry_base, stop_loss, take_profit, quantity, assumptions
    )
    gross_reward_q = Decimal("0")
    tp_fill_px = None
    tp_base = None
    if tp is not None:
        tp_fill_px = tp.fill_price
        tp_base = take_profit
        gross_reward_q = _quote_pnl(direction, entry.fill_price, tp.fill_price, quantity)

    expected_loss_q = abs(_quote_pnl(direction, entry.fill_price, sl.fill_price, quantity))
    ideal_tp_q = (
        _quote_pnl(direction, entry_base, take_profit, quantity)
        if take_profit is not None
        else Decimal("0")
    )
    execution_cost_q = ideal_tp_q - gross_reward_q if take_profit is not None else Decimal("0")
    net_reward_q = gross_reward_q

    gross_usd = fx_rates.quote_to_account(gross_reward_q, instrument.quote_currency)
    net_usd = fx_rates.quote_to_account(net_reward_q, instrument.quote_currency)
    loss_usd = fx_rates.quote_to_account(expected_loss_q, instrument.quote_currency)
    rr = (net_usd / loss_usd) if loss_usd > 0 else None

    return ExecutableEconomics(
        entry_base=entry_base,
        entry_fill=entry.fill_price,
        tp_base=tp_base,
        tp_fill=tp_fill_px,
        sl_base=stop_loss,
        sl_fill=sl.fill_price,
        gross_reward_quote=gross_reward_q.quantize(Decimal("0.01")),
        execution_cost_quote=execution_cost_q.quantize(Decimal("0.01")),
        net_reward_quote=net_reward_q.quantize(Decimal("0.01")),
        expected_loss_quote=expected_loss_q.quantize(Decimal("0.01")),
        gross_reward_usd=gross_usd,
        net_reward_usd=net_usd,
        expected_loss_usd=loss_usd,
        net_reward_risk_ratio=rr.quantize(Decimal("0.01")) if rr is not None else None,
    )


def tp_economically_valid(
    direction: Direction,
    entry_base: Decimal,
    take_profit: Decimal,
    quantity: Decimal,
    instrument: Instrument,
    assumptions: ExecutionAssumptions,
    fx_rates: FxRateTable,
) -> tuple[bool, ExecutableEconomics]:
    econ = compute_executable_economics(
        direction,
        entry_base,
        entry_base,
        take_profit,
        quantity,
        instrument,
        assumptions,
        fx_rates,
    )
    return econ.net_reward_usd > Decimal("0"), econ
