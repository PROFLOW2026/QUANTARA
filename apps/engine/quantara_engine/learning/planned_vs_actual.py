"""Planned vs actual entry risk — observation only, never resizes/rejects."""

from __future__ import annotations

import json
import logging
from decimal import Decimal
from typing import Any
from uuid import uuid4

from sqlalchemy import text

logger = logging.getLogger(__name__)

DEFAULT_TOLERANCE_PCT = Decimal("2")


def _sl_risk_usd(
    *,
    entry_price: Decimal,
    stop_loss: Decimal,
    quantity: Decimal,
    symbol: str | None,
    fx_rates: dict[str, Decimal] | None,
) -> Decimal | None:
    """Convert stop-distance × quantity to USD (JPY quote pairs need FX)."""
    diff = abs(entry_price - stop_loss)
    if diff <= 0 or quantity <= 0:
        return None
    sym = (symbol or "").upper().replace("/", "")
    if sym.endswith("JPY") or (fx_rates and "JPY" in fx_rates and sym.endswith("JPY")):
        from quantara_engine.broker.instruments import get_instrument_spec

        try:
            spec = get_instrument_spec(sym)
        except KeyError:
            spec = None
        if spec and (spec.quote_currency or "").upper() == "JPY":
            jpy_per_usd = (fx_rates or {}).get("JPY")
            if not jpy_per_usd or jpy_per_usd <= 0:
                return None
            quote_risk = quantity * diff
            return quote_risk / jpy_per_usd
    return diff * quantity


def compute_risk_observation(
    *,
    direction: str,
    signal_candle_close: Decimal | None,
    planned_entry_ref: Decimal | None,
    execution_candle_open: Decimal | None,
    actual_fill_price: Decimal,
    planned_sl: Decimal | None,
    planned_tp: Decimal | None,
    quantity: Decimal,
    equity_at_entry: Decimal | None,
    planned_risk_usd: Decimal | None,
    spread: Decimal | None = None,
    slippage: Decimal | None = None,
    fees: Decimal | None = None,
    tolerance_pct: Decimal = DEFAULT_TOLERANCE_PCT,
    symbol: str | None = None,
    fx_rates: dict[str, Decimal] | None = None,
) -> dict[str, Any]:
    """Compute gap and post-fill risk-to-SL for LONG and SHORT."""
    gap = None
    if signal_candle_close is not None and execution_candle_open is not None:
        gap = execution_candle_open - signal_candle_close

    planned_risk = planned_risk_usd
    if planned_risk is None and planned_entry_ref is not None and planned_sl is not None:
        planned_risk = _sl_risk_usd(
            entry_price=planned_entry_ref,
            stop_loss=planned_sl,
            quantity=quantity,
            symbol=symbol,
            fx_rates=fx_rates,
        )

    actual_risk = None
    if planned_sl is not None:
        actual_risk = _sl_risk_usd(
            entry_price=actual_fill_price,
            stop_loss=planned_sl,
            quantity=quantity,
            symbol=symbol,
            fx_rates=fx_rates,
        )

    planned_risk_pct = None
    actual_risk_pct = None
    if equity_at_entry and equity_at_entry > 0:
        if planned_risk is not None:
            planned_risk_pct = planned_risk / equity_at_entry * Decimal("100")
        if actual_risk is not None:
            actual_risk_pct = actual_risk / equity_at_entry * Decimal("100")

    risk_diff_usd = None
    risk_diff_pct = None
    if planned_risk is not None and actual_risk is not None:
        risk_diff_usd = actual_risk - planned_risk
        if planned_risk > 0:
            risk_diff_pct = risk_diff_usd / planned_risk * Decimal("100")

    planned_rr = None
    actual_rr = None
    if planned_sl is not None and planned_tp is not None:
        if planned_entry_ref is not None:
            risk = abs(planned_entry_ref - planned_sl)
            reward = abs(planned_tp - planned_entry_ref)
            if risk > 0:
                planned_rr = reward / risk
        risk_a = abs(actual_fill_price - planned_sl)
        reward_a = abs(planned_tp - actual_fill_price)
        if risk_a > 0:
            actual_rr = reward_a / risk_a

    overrun = False
    if planned_risk is not None and actual_risk is not None and planned_risk > 0:
        max_allowed = planned_risk * (Decimal("1") + tolerance_pct / Decimal("100"))
        overrun = actual_risk > max_allowed

    return {
        "gap_from_signal": float(gap) if gap is not None else None,
        "planned_risk_usd": float(planned_risk) if planned_risk is not None else None,
        "planned_risk_pct": float(planned_risk_pct) if planned_risk_pct is not None else None,
        "actual_risk_usd": float(actual_risk) if actual_risk is not None else None,
        "actual_risk_pct": float(actual_risk_pct) if actual_risk_pct is not None else None,
        "risk_diff_usd": float(risk_diff_usd) if risk_diff_usd is not None else None,
        "risk_diff_pct": float(risk_diff_pct) if risk_diff_pct is not None else None,
        "planned_rr": float(planned_rr) if planned_rr is not None else None,
        "actual_rr": float(actual_rr) if actual_rr is not None else None,
        "risk_overrun": overrun,
        "spread": float(spread) if spread is not None else None,
        "slippage": float(slippage) if slippage is not None else None,
        "fees": float(fees) if fees is not None else None,
    }


def record_planned_vs_actual(
    store,
    *,
    baseline_id: str | None,
    source_type: str,
    symbol: str,
    direction: str,
    actual_fill_price: Decimal,
    quantity: Decimal,
    planned_sl: Decimal | None,
    planned_tp: Decimal | None = None,
    signal_candle_close: Decimal | None = None,
    planned_entry_ref: Decimal | None = None,
    execution_candle_open: Decimal | None = None,
    planned_risk_usd: Decimal | None = None,
    equity_at_entry: Decimal | None = None,
    spread: Decimal | None = None,
    slippage: Decimal | None = None,
    fees: Decimal | None = None,
    timeframe: str | None = None,
    portfolio_id: str | None = None,
    broker_account_id: str | None = None,
    position_id: str | None = None,
    trade_id: str | None = None,
    live_sim_position_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> str | None:
    fx_map: dict[str, Decimal] | None = None
    try:
        instrument = store.get_instrument_by_symbol(symbol)
        if instrument:
            ctx = store.build_currency_context_for_instruments([instrument])
            fx_map = {k: v for k, v in ctx.fx_rates.quote_per_usd.items()}
    except Exception:
        fx_map = None

    obs = compute_risk_observation(
        direction=direction,
        signal_candle_close=signal_candle_close,
        planned_entry_ref=planned_entry_ref,
        execution_candle_open=execution_candle_open,
        actual_fill_price=actual_fill_price,
        planned_sl=planned_sl,
        planned_tp=planned_tp,
        quantity=quantity,
        equity_at_entry=equity_at_entry,
        planned_risk_usd=planned_risk_usd,
        spread=spread,
        slippage=slippage,
        fees=fees,
        symbol=symbol,
        fx_rates=fx_map,
    )
    row_id = str(uuid4())
    try:
        store.session.execute(
            text(
                """
                INSERT INTO learning_planned_vs_actual_risk (
                  id, baseline_id, source_type, portfolio_id, broker_account_id,
                  position_id, trade_id, live_sim_position_id,
                  symbol, timeframe, direction,
                  signal_candle_close, planned_entry_ref, execution_candle_open,
                  actual_fill_price, gap_from_signal, spread, slippage, fees,
                  planned_sl, planned_tp, planned_risk_usd, planned_risk_pct,
                  actual_risk_usd, actual_risk_pct, risk_diff_usd, risk_diff_pct,
                  planned_rr, actual_rr, risk_overrun, equity_at_entry, quantity, metadata
                ) VALUES (
                  CAST(:id AS uuid), CAST(:baseline_id AS uuid), :source_type,
                  CAST(:portfolio_id AS uuid), CAST(:broker_account_id AS uuid),
                  CAST(:position_id AS uuid), CAST(:trade_id AS uuid),
                  CAST(:live_sim_position_id AS uuid),
                  :symbol, :timeframe, :direction,
                  :signal_candle_close, :planned_entry_ref, :execution_candle_open,
                  :actual_fill_price, :gap_from_signal, :spread, :slippage, :fees,
                  :planned_sl, :planned_tp, :planned_risk_usd, :planned_risk_pct,
                  :actual_risk_usd, :actual_risk_pct, :risk_diff_usd, :risk_diff_pct,
                  :planned_rr, :actual_rr, :risk_overrun, :equity_at_entry, :quantity,
                  CAST(:metadata AS jsonb)
                )
                """
            ),
            {
                "id": row_id,
                "baseline_id": baseline_id,
                "source_type": source_type,
                "portfolio_id": portfolio_id,
                "broker_account_id": broker_account_id,
                "position_id": position_id,
                "trade_id": trade_id,
                "live_sim_position_id": live_sim_position_id,
                "symbol": symbol,
                "timeframe": timeframe,
                "direction": direction,
                "signal_candle_close": signal_candle_close,
                "planned_entry_ref": planned_entry_ref,
                "execution_candle_open": execution_candle_open,
                "actual_fill_price": actual_fill_price,
                "gap_from_signal": obs["gap_from_signal"],
                "spread": obs["spread"],
                "slippage": obs["slippage"],
                "fees": obs["fees"],
                "planned_sl": planned_sl,
                "planned_tp": planned_tp,
                "planned_risk_usd": obs["planned_risk_usd"],
                "planned_risk_pct": obs["planned_risk_pct"],
                "actual_risk_usd": obs["actual_risk_usd"],
                "actual_risk_pct": obs["actual_risk_pct"],
                "risk_diff_usd": obs["risk_diff_usd"],
                "risk_diff_pct": obs["risk_diff_pct"],
                "planned_rr": obs["planned_rr"],
                "actual_rr": obs["actual_rr"],
                "risk_overrun": obs["risk_overrun"],
                "equity_at_entry": equity_at_entry,
                "quantity": quantity,
                "metadata": json.dumps(
                    {
                        **(metadata or {}),
                        "observational_only": True,
                        "flag": "RISK_OVERRUN" if obs["risk_overrun"] else None,
                    },
                    default=str,
                ),
            },
        )
    except Exception:
        logger.exception("Failed to record planned vs actual risk (non-fatal)")
        return None
    return row_id
