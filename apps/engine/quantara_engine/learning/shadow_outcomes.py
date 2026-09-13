"""Shadow RSI hypothetical trade ledger — never creates real orders/positions."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import uuid4

from sqlalchemy import text

from quantara_engine.domain.types import Direction, Position, PositionStatus
from quantara_engine.execution.exit_triggers import detect_exit_trigger
from quantara_engine.execution.fill_calculator import calculate_fill_price
from quantara_engine.execution.timing import next_execution_timestamp
from quantara_engine.learning.economics_util import assumptions_for_symbol
from quantara_engine.learning.rsi_shadow import ShadowSignalResult

logger = logging.getLogger(__name__)

SHADOW_REF_EQUITY = Decimal("2000")
SHADOW_RISK_PCT = Decimal("1.0")


def _dec(v: Any) -> Decimal | None:
    if v is None:
        return None
    return Decimal(str(v))


def _shadow_quantity(entry: Decimal, stop: Decimal) -> Decimal:
    dist = abs(entry - stop)
    if dist <= 0:
        return Decimal("1")
    risk = SHADOW_REF_EQUITY * SHADOW_RISK_PCT / Decimal("100")
    qty = (risk / dist).quantize(Decimal("0.0001"))
    return qty if qty > 0 else Decimal("1")


def open_shadow_trade_from_signal(
    store,
    *,
    baseline_id: str | None,
    eval_id: str | None,
    variant: str,
    symbol: str,
    timeframe: str,
    result: ShadowSignalResult,
    signal_candle_ts: datetime,
    candles: list,
    candle_index: int,
    structure_regime: str | None,
    volatility_regime: str | None,
) -> str | None:
    if result.action not in {"BUY", "SELL"}:
        return None
    if result.suggested_sl is None:
        return None

    direction = "long" if result.action == "BUY" else "short"
    entry_ts = next_execution_timestamp(signal_candle_ts, timeframe)
    planned_entry = _dec(candles[candle_index].close)
    entry_candle = None
    for c in candles[candle_index + 1 :]:
        if c.timestamp == entry_ts:
            entry_candle = c
            break

    entry_base = _dec(entry_candle.open) if entry_candle is not None else planned_entry
    assert entry_base is not None
    assumptions = assumptions_for_symbol(symbol, entry_base)
    dir_enum = Direction.LONG if direction == "long" else Direction.SHORT
    qty = _shadow_quantity(entry_base, result.suggested_sl)
    fill = (
        calculate_fill_price(dir_enum, "entry", entry_base, qty, assumptions)
        if entry_candle is not None
        else None
    )

    trade_id = str(uuid4())
    status = "open" if entry_candle is not None else "pending_entry"
    store.session.execute(
        text(
            """
            INSERT INTO learning_shadow_rsi_trades (
              id, baseline_id, eval_id, variant, symbol, timeframe, direction,
              signal_candle_ts, entry_candle_ts, planned_entry_ref, fill_price,
              stop_loss, take_profit, quantity, status,
              structure_regime, volatility_regime, metadata
            ) VALUES (
              CAST(:id AS uuid), CAST(:baseline_id AS uuid), CAST(:eval_id AS uuid),
              :variant, :symbol, :timeframe, :direction,
              :signal_candle_ts, :entry_candle_ts, :planned_entry_ref, :fill_price,
              :stop_loss, :take_profit, :quantity, :status,
              :structure_regime, :volatility_regime, CAST(:metadata AS jsonb)
            )
            ON CONFLICT (variant, symbol, timeframe, signal_candle_ts, direction)
            DO NOTHING
            """
        ),
        {
            "id": trade_id,
            "baseline_id": baseline_id,
            "eval_id": eval_id,
            "variant": variant,
            "symbol": symbol,
            "timeframe": timeframe,
            "direction": direction,
            "signal_candle_ts": signal_candle_ts,
            "entry_candle_ts": entry_candle.timestamp if entry_candle else entry_ts,
            "planned_entry_ref": planned_entry,
            "fill_price": fill.fill_price if fill else None,
            "stop_loss": result.suggested_sl,
            "take_profit": result.suggested_tp,
            "quantity": qty,
            "status": status,
            "structure_regime": structure_regime,
            "volatility_regime": volatility_regime,
            "metadata": json.dumps(
                {
                    "observational_only": True,
                    "shadow_ref_equity": float(SHADOW_REF_EQUITY),
                    "entry_spread": float(fill.spread_cost) if fill else None,
                    "entry_slippage": float(fill.slippage) if fill else None,
                    "entry_fees": float(fill.fees) if fill else None,
                },
                default=str,
            ),
        },
    )
    return trade_id


def _as_position(row: dict[str, Any]) -> Position:
    fill = Decimal(str(row["fill_price"] or row["planned_entry_ref"] or 0))
    return Position(
        id=str(row["id"]),
        portfolio_id="shadow",
        strategy_instance_id="shadow",
        instrument_id="shadow",
        direction=Direction.LONG if row["direction"] == "long" else Direction.SHORT,
        quantity=Decimal(str(row["quantity"])),
        entry_price=fill,
        stop_loss=Decimal(str(row["stop_loss"])),
        take_profit=Decimal(str(row["take_profit"])) if row.get("take_profit") is not None else None,
        current_price=fill,
        status=PositionStatus.OPEN,
        opened_at=row.get("entry_candle_ts") or row["signal_candle_ts"],
    )


def resolve_open_shadow_trades(
    store,
    *,
    symbol: str,
    timeframe: str,
    candles: list,
) -> int:
    rows = store.session.execute(
        text(
            """
            SELECT id::text, variant, direction, signal_candle_ts, entry_candle_ts,
                   planned_entry_ref, fill_price, stop_loss, take_profit, quantity, status,
                   metadata
            FROM learning_shadow_rsi_trades
            WHERE symbol = :symbol AND timeframe = :timeframe
              AND status IN ('open', 'pending_entry')
            """
        ),
        {"symbol": symbol, "timeframe": timeframe},
    ).mappings().all()

    if not rows:
        return 0

    closed = 0
    by_ts = {c.timestamp: c for c in candles}

    for row in rows:
        row = dict(row)
        if row["status"] == "pending_entry":
            entry_ts = row["entry_candle_ts"]
            entry_candle = by_ts.get(entry_ts)
            if entry_candle is None:
                continue
            dir_enum = Direction.LONG if row["direction"] == "long" else Direction.SHORT
            qty = Decimal(str(row["quantity"]))
            assumptions = assumptions_for_symbol(symbol, entry_candle.open)
            fill = calculate_fill_price(dir_enum, "entry", entry_candle.open, qty, assumptions)
            store.session.execute(
                text(
                    """
                    UPDATE learning_shadow_rsi_trades
                    SET fill_price = :fill, status = 'open', updated_at = NOW(),
                        metadata = COALESCE(metadata, '{}'::jsonb) || CAST(:meta AS jsonb)
                    WHERE id = CAST(:id AS uuid) AND status = 'pending_entry'
                    """
                ),
                {
                    "id": row["id"],
                    "fill": fill.fill_price,
                    "meta": json.dumps(
                        {
                            "entry_spread": float(fill.spread_cost),
                            "entry_slippage": float(fill.slippage),
                            "entry_fees": float(fill.fees),
                        }
                    ),
                },
            )
            row["fill_price"] = fill.fill_price
            row["status"] = "open"

        if row["status"] != "open" or row.get("fill_price") is None:
            continue

        pos = _as_position(row)
        entry_ts = row["entry_candle_ts"]
        assumptions = assumptions_for_symbol(symbol, Decimal(str(row["fill_price"])))
        for candle in candles:
            if entry_ts is not None and candle.timestamp <= entry_ts:
                continue
            trigger = detect_exit_trigger(pos, candle)
            if not trigger:
                continue
            reason, trigger_price = trigger
            dir_enum = Direction.LONG if row["direction"] == "long" else Direction.SHORT
            qty = Decimal(str(row["quantity"]))
            exit_fill = calculate_fill_price(dir_enum, "exit", trigger_price, qty, assumptions)
            entry_px = Decimal(str(row["fill_price"]))
            gross = (
                (exit_fill.fill_price - entry_px) * qty
                if dir_enum == Direction.LONG
                else (entry_px - exit_fill.fill_price) * qty
            )
            meta = row.get("metadata") or {}
            if isinstance(meta, str):
                meta = json.loads(meta)
            entry_fees = Decimal(str(meta.get("entry_fees") or 0))
            fees = entry_fees + exit_fill.fees
            net = gross - fees
            duration = None
            if entry_ts is not None:
                duration = int((candle.timestamp - entry_ts).total_seconds())
            store.session.execute(
                text(
                    """
                    UPDATE learning_shadow_rsi_trades
                    SET status = 'closed',
                        exit_reason = :exit_reason,
                        exit_price = :exit_price,
                        exit_ts = :exit_ts,
                        gross_pnl = :gross_pnl,
                        net_pnl = :net_pnl,
                        fees = :fees,
                        spread_cost = :spread_cost,
                        slippage_cost = :slippage_cost,
                        duration_seconds = :duration_seconds,
                        updated_at = NOW()
                    WHERE id = CAST(:id AS uuid) AND status = 'open'
                    """
                ),
                {
                    "id": row["id"],
                    "exit_reason": reason.value if hasattr(reason, "value") else str(reason),
                    "exit_price": exit_fill.fill_price,
                    "exit_ts": candle.timestamp,
                    "gross_pnl": gross,
                    "net_pnl": net,
                    "fees": fees,
                    "spread_cost": exit_fill.spread_cost,
                    "slippage_cost": exit_fill.slippage,
                    "duration_seconds": duration,
                },
            )
            closed += 1
            break

    return closed


def close_shadow_on_reversal(
    store,
    *,
    symbol: str,
    timeframe: str,
    candle_timestamp: datetime,
    candles: list,
    candle_index: int,
) -> int:
    rows = store.session.execute(
        text(
            """
            SELECT id::text, direction, fill_price, quantity, entry_candle_ts
            FROM learning_shadow_rsi_trades
            WHERE symbol = :symbol AND timeframe = :timeframe AND status = 'open'
            """
        ),
        {"symbol": symbol, "timeframe": timeframe},
    ).mappings().all()
    if not rows:
        return 0

    candle = candles[candle_index]
    assumptions = assumptions_for_symbol(symbol, candle.close)
    n = 0
    for row in rows:
        if row["fill_price"] is None:
            continue
        dir_enum = Direction.LONG if row["direction"] == "long" else Direction.SHORT
        qty = Decimal(str(row["quantity"]))
        exit_fill = calculate_fill_price(dir_enum, "exit", candle.close, qty, assumptions)
        entry_px = Decimal(str(row["fill_price"]))
        gross = (
            (exit_fill.fill_price - entry_px) * qty
            if dir_enum == Direction.LONG
            else (entry_px - exit_fill.fill_price) * qty
        )
        net = gross - exit_fill.fees
        duration = None
        if row["entry_candle_ts"] is not None:
            duration = int((candle.timestamp - row["entry_candle_ts"]).total_seconds())
        store.session.execute(
            text(
                """
                UPDATE learning_shadow_rsi_trades
                SET status = 'closed',
                    exit_reason = 'STRATEGY_CLOSE',
                    exit_price = :exit_price,
                    exit_ts = :exit_ts,
                    gross_pnl = :gross_pnl,
                    net_pnl = :net_pnl,
                    fees = :fees,
                    duration_seconds = :duration_seconds,
                    updated_at = NOW()
                WHERE id = CAST(:id AS uuid) AND status = 'open'
                """
            ),
            {
                "id": row["id"],
                "exit_price": exit_fill.fill_price,
                "exit_ts": candle_timestamp,
                "gross_pnl": gross,
                "net_pnl": net,
                "fees": exit_fill.fees,
                "duration_seconds": duration,
            },
        )
        n += 1
    return n


def shadow_variant_metrics(store, *, baseline_id: str | None = None, since=None) -> dict[str, Any]:
    clauses = ["1=1"]
    params: dict[str, Any] = {}
    if baseline_id:
        clauses.append("baseline_id = CAST(:baseline_id AS uuid)")
        params["baseline_id"] = baseline_id
    if since is not None:
        clauses.append("signal_candle_ts >= :since")
        params["since"] = since
    where = " AND ".join(clauses)

    rows = store.session.execute(
        text(
            f"""
            SELECT variant, direction, status, exit_reason,
                   COALESCE(net_pnl, 0) AS net_pnl,
                   COALESCE(gross_pnl, 0) AS gross_pnl,
                   duration_seconds, exit_ts, signal_candle_ts
            FROM learning_shadow_rsi_trades
            WHERE {where}
            ORDER BY COALESCE(exit_ts, signal_candle_ts)
            """
        ),
        params,
    ).mappings().all()

    out: dict[str, Any] = {}
    for variant in ("active", "directional", "no_rsi"):
        vrows = [r for r in rows if r["variant"] == variant]
        closed = [r for r in vrows if r["status"] == "closed"]
        wins = [r for r in closed if Decimal(str(r["net_pnl"])) > 0]
        losses = [r for r in closed if Decimal(str(r["net_pnl"])) <= 0]
        gross_wins = sum((Decimal(str(r["net_pnl"])) for r in wins), Decimal("0"))
        gross_losses = abs(sum((Decimal(str(r["net_pnl"])) for r in losses), Decimal("0")))
        net = sum((Decimal(str(r["net_pnl"])) for r in closed), Decimal("0"))
        equity = Decimal("0")
        peak = Decimal("0")
        max_dd = Decimal("0")
        for r in closed:
            equity += Decimal(str(r["net_pnl"]))
            peak = max(peak, equity)
            if peak > 0:
                max_dd = max(max_dd, (peak - equity) / peak * 100)
        avg_win = (gross_wins / len(wins)) if wins else Decimal("0")
        avg_loss = (gross_losses / len(losses)) if losses else Decimal("0")
        pf = (gross_wins / gross_losses) if gross_losses > 0 else None
        expectancy = (net / len(closed)) if closed else Decimal("0")
        durations = [r["duration_seconds"] for r in closed if r["duration_seconds"] is not None]
        out[variant] = {
            "opportunities": len(vrows),
            "hypothetical_trades": len(vrows),
            "closed_trades": len(closed),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": float(len(wins) / len(closed) * 100) if closed else None,
            "gross_pnl": float(sum((Decimal(str(r["gross_pnl"])) for r in closed), Decimal("0"))),
            "net_pnl": float(net),
            "profit_factor": float(pf) if pf is not None else None,
            "average_win": float(avg_win),
            "average_loss": float(avg_loss),
            "max_drawdown_pct": float(max_dd),
            "expectancy": float(expectancy),
            "sl_count": sum(1 for r in closed if str(r["exit_reason"] or "").lower() in {"sl", "stop_loss"}),
            "tp_count": sum(1 for r in closed if str(r["exit_reason"] or "").lower() in {"tp", "take_profit"}),
            "strategy_close_count": sum(
                1 for r in closed if str(r["exit_reason"] or "").upper() == "STRATEGY_CLOSE"
            ),
            "average_duration_seconds": (sum(durations) / len(durations)) if durations else None,
            "long_trades": sum(1 for r in vrows if r["direction"] == "long"),
            "short_trades": sum(1 for r in vrows if r["direction"] == "short"),
        }
    return out
