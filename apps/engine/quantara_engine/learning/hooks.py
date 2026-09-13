"""Observation hooks for run_strategy / fills — never mutate active trading decisions."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import text

from quantara_engine.domain.types import Signal, SignalAction
from quantara_engine.learning.activation import ensure_trading_week_baseline
from quantara_engine.learning.funnel import record_funnel_event, robot_label_for_slug
from quantara_engine.learning.rsi_shadow import evaluate_all_rsi_shadows
from quantara_engine.learning.shadow_outcomes import (
    close_shadow_on_reversal,
    open_shadow_trade_from_signal,
    resolve_open_shadow_trades,
)
from quantara_engine.market_regime.snapshots import classify_and_save

logger = logging.getLogger(__name__)


def _action_upper(signal: Signal) -> str:
    return signal.action.value.upper() if isinstance(signal.action, SignalAction) else str(signal.action).upper()


def observe_robot_a_candle(
    store,
    *,
    instrument,
    timeframe: str,
    candles: list,
    candle_index: int,
    active_signal: Signal,
    strategy_slug: str,
    parameter_overrides: dict[str, Any] | None,
    paper_run_id: str | None,
    allow_live_execution: bool,
    now: datetime,
) -> None:
    """
    Single observational hook after evaluate_signal for Robot A.
    Must not create intents/orders/fills/positions or consume opportunities.
    """
    if strategy_slug != "gold-trend-pullback":
        return
    if not allow_live_execution:
        return  # prospective learning only from live cycle

    try:
        baseline = ensure_trading_week_baseline(store, now=now)
        baseline_id = baseline["id"] if baseline else None
        activated_at = baseline.get("activated_at") if baseline else None
        candle = candles[candle_index]
        if activated_at is not None and candle.timestamp < activated_at:
            return

        from quantara_engine.strategies.gold_trend_pullback.v1_0_0 import GoldTrendPullbackV1

        params = {**GoldTrendPullbackV1.default_parameters(), **(parameter_overrides or {})}
        visible = candles[: candle_index + 1]

        # Regime tagging — observational; does not alter signal
        regime = classify_and_save(store, instrument, timeframe, visible)
        structure = regime.structure_regime.value if regime else None
        volatility = regime.volatility_regime.value if regime else None

        shadows = evaluate_all_rsi_shadows(visible, params, active_signal=active_signal)
        active = shadows["active"]
        directional = shadows["directional"]
        no_rsi = shadows["no_rsi"]

        eval_id = str(uuid4())
        store.session.execute(
            text(
                """
                INSERT INTO learning_eval_events (
                  id, baseline_id, evaluated_at, candle_timestamp, symbol, timeframe,
                  strategy_slug, robot_label, paper_run_id, direction, active_action,
                  active_reason, structure_regime, volatility_regime, metadata
                ) VALUES (
                  CAST(:id AS uuid), CAST(:baseline_id AS uuid), :evaluated_at, :candle_timestamp,
                  :symbol, :timeframe, :strategy_slug, :robot_label, CAST(:paper_run_id AS uuid),
                  :direction, :active_action, :active_reason, :structure_regime, :volatility_regime,
                  CAST(:metadata AS jsonb)
                )
                ON CONFLICT (symbol, timeframe, candle_timestamp, strategy_slug) DO NOTHING
                """
            ),
            {
                "id": eval_id,
                "baseline_id": baseline_id,
                "evaluated_at": now,
                "candle_timestamp": candle.timestamp,
                "symbol": instrument.symbol,
                "timeframe": timeframe,
                "strategy_slug": strategy_slug,
                "robot_label": robot_label_for_slug(strategy_slug),
                "paper_run_id": paper_run_id,
                "direction": (
                    "long"
                    if active.action == "BUY"
                    else ("short" if active.action == "SELL" else None)
                ),
                "active_action": active.action,
                "active_reason": active.reason,
                "structure_regime": structure,
                "volatility_regime": volatility,
                "metadata": json.dumps({"observational_only": True}),
            },
        )

        store.session.execute(
            text(
                """
                INSERT INTO learning_shadow_rsi_evals (
                  id, baseline_id, eval_event_id, evaluated_at, candle_timestamp,
                  symbol, timeframe, strategy_slug, paper_run_id,
                  active_action, active_reason,
                  shadow_directional_action, shadow_directional_reason,
                  shadow_no_rsi_action, shadow_no_rsi_reason,
                  rsi, ema20, ema50, ema200, atr, suggested_sl, suggested_tp,
                  structure_regime, volatility_regime, metadata
                ) VALUES (
                  CAST(:id AS uuid), CAST(:baseline_id AS uuid), CAST(:eval_event_id AS uuid),
                  :evaluated_at, :candle_timestamp, :symbol, :timeframe, :strategy_slug,
                  CAST(:paper_run_id AS uuid),
                  :active_action, :active_reason,
                  :shadow_directional_action, :shadow_directional_reason,
                  :shadow_no_rsi_action, :shadow_no_rsi_reason,
                  :rsi, :ema20, :ema50, :ema200, :atr, :suggested_sl, :suggested_tp,
                  :structure_regime, :volatility_regime, CAST(:metadata AS jsonb)
                )
                ON CONFLICT (symbol, timeframe, candle_timestamp, strategy_slug) DO NOTHING
                RETURNING id::text
                """
            ),
            {
                "id": str(uuid4()),
                "baseline_id": baseline_id,
                "eval_event_id": eval_id,
                "evaluated_at": now,
                "candle_timestamp": candle.timestamp,
                "symbol": instrument.symbol,
                "timeframe": timeframe,
                "strategy_slug": strategy_slug,
                "paper_run_id": paper_run_id,
                "active_action": active.action,
                "active_reason": active.reason,
                "shadow_directional_action": directional.action,
                "shadow_directional_reason": directional.reason,
                "shadow_no_rsi_action": no_rsi.action,
                "shadow_no_rsi_reason": no_rsi.reason,
                "rsi": active.rsi,
                "ema20": active.ema20,
                "ema50": active.ema50,
                "ema200": active.ema200,
                "atr": active.atr,
                "suggested_sl": active.suggested_sl,
                "suggested_tp": active.suggested_tp,
                "structure_regime": structure,
                "volatility_regime": volatility,
                "metadata": json.dumps({"observational_only": True}),
            },
        )

        # Funnel stages from active signal (canonical reasons)
        record_funnel_event(
            store,
            baseline_id=baseline_id,
            stage="strategy_evaluation",
            reason=active.reason,
            strategy_slug=strategy_slug,
            symbol=instrument.symbol,
            timeframe=timeframe,
            direction=(
                "long"
                if active.action == "BUY"
                else ("short" if active.action == "SELL" else None)
            ),
            candle_timestamp=candle.timestamp,
        )
        if active.action in {"BUY", "SELL"}:
            record_funnel_event(
                store,
                baseline_id=baseline_id,
                stage="buy_sell_setup",
                reason=active.reason,
                strategy_slug=strategy_slug,
                symbol=instrument.symbol,
                timeframe=timeframe,
                direction="long" if active.action == "BUY" else "short",
                candle_timestamp=candle.timestamp,
            )
        else:
            stage = (
                "strategy_filter_rejection"
                if active.reason.startswith("NO_SETUP")
                else "hold_no_setup"
            )
            record_funnel_event(
                store,
                baseline_id=baseline_id,
                stage=stage,
                reason=active.reason,
                strategy_slug=strategy_slug,
                symbol=instrument.symbol,
                timeframe=timeframe,
                candle_timestamp=candle.timestamp,
            )

        # Shadow trade ledger for all three variants (active mirrored observationally)
        shadow_eval_id = store.session.execute(
            text(
                """
                SELECT id::text FROM learning_shadow_rsi_evals
                WHERE symbol = :symbol AND timeframe = :timeframe
                  AND candle_timestamp = :ts AND strategy_slug = :slug
                """
            ),
            {
                "symbol": instrument.symbol,
                "timeframe": timeframe,
                "ts": candle.timestamp,
                "slug": strategy_slug,
            },
        ).scalar()

        for variant_key, result in shadows.items():
            if result.action in {"BUY", "SELL"}:
                open_shadow_trade_from_signal(
                    store,
                    baseline_id=baseline_id,
                    eval_id=shadow_eval_id,
                    variant=variant_key,
                    symbol=instrument.symbol,
                    timeframe=timeframe,
                    result=result,
                    signal_candle_ts=candle.timestamp,
                    candles=candles,
                    candle_index=candle_index,
                    structure_regime=structure,
                    volatility_regime=volatility,
                )
            elif result.action == "CLOSE":
                close_shadow_on_reversal(
                    store,
                    symbol=instrument.symbol,
                    timeframe=timeframe,
                    candle_timestamp=candle.timestamp,
                    candles=candles,
                    candle_index=candle_index,
                )

        resolve_open_shadow_trades(
            store,
            symbol=instrument.symbol,
            timeframe=timeframe,
            candles=candles,
        )
    except Exception:
        logger.exception(
            "Learning observation failed for %s %s (non-fatal; trading unchanged)",
            getattr(instrument, "symbol", "?"),
            timeframe,
        )


def observe_generic_eval(
    store,
    *,
    instrument,
    timeframe: str,
    candles: list,
    candle_index: int,
    active_signal: Signal,
    strategy_slug: str,
    allow_live_execution: bool,
    now: datetime,
) -> None:
    """Lightweight regime + funnel for non-Robot-A strategies."""
    if strategy_slug == "gold-trend-pullback":
        return
    if not allow_live_execution:
        return
    try:
        baseline = ensure_trading_week_baseline(store, now=now)
        baseline_id = baseline["id"] if baseline else None
        activated_at = baseline.get("activated_at") if baseline else None
        candle = candles[candle_index]
        if activated_at is not None and candle.timestamp < activated_at:
            return
        visible = candles[: candle_index + 1]
        regime = classify_and_save(store, instrument, timeframe, visible)
        structure = regime.structure_regime.value if regime else None
        volatility = regime.volatility_regime.value if regime else None
        action = _action_upper(active_signal)
        store.session.execute(
            text(
                """
                INSERT INTO learning_eval_events (
                  id, baseline_id, evaluated_at, candle_timestamp, symbol, timeframe,
                  strategy_slug, robot_label, direction, active_action, active_reason,
                  structure_regime, volatility_regime, metadata
                ) VALUES (
                  CAST(:id AS uuid), CAST(:baseline_id AS uuid), :evaluated_at, :candle_timestamp,
                  :symbol, :timeframe, :strategy_slug, :robot_label, :direction,
                  :active_action, :active_reason, :structure_regime, :volatility_regime,
                  CAST(:metadata AS jsonb)
                )
                ON CONFLICT (symbol, timeframe, candle_timestamp, strategy_slug) DO NOTHING
                """
            ),
            {
                "id": str(uuid4()),
                "baseline_id": baseline_id,
                "evaluated_at": now,
                "candle_timestamp": candle.timestamp,
                "symbol": instrument.symbol,
                "timeframe": timeframe,
                "strategy_slug": strategy_slug,
                "robot_label": robot_label_for_slug(strategy_slug),
                "direction": (
                    "long" if action == "BUY" else ("short" if action == "SELL" else None)
                ),
                "active_action": action,
                "active_reason": active_signal.reason,
                "structure_regime": structure,
                "volatility_regime": volatility,
                "metadata": json.dumps({"observational_only": True}),
            },
        )
        record_funnel_event(
            store,
            baseline_id=baseline_id,
            stage="strategy_evaluation",
            reason=active_signal.reason,
            strategy_slug=strategy_slug,
            symbol=instrument.symbol,
            timeframe=timeframe,
            candle_timestamp=candle.timestamp,
        )
        if action in {"BUY", "SELL"}:
            record_funnel_event(
                store,
                baseline_id=baseline_id,
                stage="buy_sell_setup",
                reason=active_signal.reason,
                strategy_slug=strategy_slug,
                symbol=instrument.symbol,
                timeframe=timeframe,
                direction="long" if action == "BUY" else "short",
                candle_timestamp=candle.timestamp,
            )
    except Exception:
        logger.exception("Generic learning observation failed (non-fatal)")
