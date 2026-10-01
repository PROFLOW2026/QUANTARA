"""Lightweight broker pre-trade filter for V3.2 Stage A (no full backtest)."""

from __future__ import annotations

from collections import Counter
from decimal import Decimal
from typing import Any

from quantara_engine.broker.account import build_account_snapshot
from quantara_engine.broker.execution_model import ExecutionModelVersion, resolve_execution_model
from quantara_engine.broker.execution_product import route_execution_product
from quantara_engine.broker.instruments import get_instrument_spec
from quantara_engine.broker.normalizer import normalize_quantity
from quantara_engine.broker.pre_trade import evaluate_broker_order
from quantara_engine.broker.profile import QUANTARA_STANDARD_PAPER
from quantara_engine.broker.types import BrokerOrderRequest
from quantara_engine.domain.types import Instrument
from quantara_engine.execution.cost_profile import execution_assumptions_for
from quantara_engine.market_data.sessions import session_allows_entries
from quantara_engine.market_data.registry import get_asset
from quantara_engine.persistence.store import TradingStore
from quantara_engine.research.v3_2.rule_lab import entry_mask_series, _df


def _map_reject(reason: str | None, detail: str | None) -> str:
    r = f"{reason or ''} {detail or ''}".lower()
    if "step" in r:
        return "QTY_STEP"
    if "min" in r and "qty" in r:
        return "MIN_QUANTITY"
    if "buying_power" in r:
        return "INSUFFICIENT_BUYING_POWER"
    if "margin" in r:
        return "MARGIN"
    if "exposure" in r or "asset" in r:
        return "SYMBOL_EXPOSURE"
    if "leverage" in r:
        return "BROKER_RISK"
    if "notional" in r:
        return "NOTIONAL_LIMIT"
    if "market closed" in r or "session" in r:
        return "SESSION"
    if "stale" in r:
        return "STALE"
    if "short" in r:
        return "SHORT_NOT_ALLOWED"
    return "OTHER"


def broker_light_funnel(
    store: TradingStore,
    *,
    instrument: Instrument,
    asset: str,
    candles: list,
    family: str,
    direction: str,
    parameters: dict[str, Any],
    equity: Decimal = Decimal("10000"),
    risk_pct: Decimal = Decimal("0.25"),
    sample_stride: int = 1,
) -> dict[str, Any]:
    df = _df(candles)
    if len(df) < 120:
        return {"signals": 0, "broker_valid": 0, "reject_rate": 1.0, "rejects": {}}

    asset_obj = get_asset(asset)
    session_open = (
        (lambda ts: session_allows_entries(asset_obj.trading_sessions or {}, ts))
        if asset_obj
        else None
    )
    mask = entry_mask_series(
        family, df, direction=direction, parameters=parameters, session_open=session_open
    )
    signal_idx = [i for i in range(len(df) - 2) if bool(mask.iloc[i])]
    signals = len(signal_idx)
    if sample_stride > 1:
        signal_idx = signal_idx[::sample_stride]

    account = build_account_snapshot(
        cash=equity,
        balance=equity,
        realized_pnl=Decimal("0"),
        positions={},
        fx_rates={"USD": Decimal("1"), "JPY": Decimal("150")},
    )
    exec_model = ExecutionModelVersion.REALISTIC_BROKER_V1
    rejects: Counter[str] = Counter()
    valid = 0
    attempts = 0

    for i in signal_idx:
        entry_px = Decimal(str(float(df["open"].iloc[i + 1])))
        atr_val = float(df["high"].iloc[i] - df["low"].iloc[i])
        sl_mult = float(parameters.get("atr_sl", 1.5))
        sl_dist = max(entry_px * Decimal("0.002"), Decimal(str(atr_val * sl_mult)))
        if direction == "long":
            sl = entry_px - sl_dist
        else:
            sl = entry_px + sl_dist
        risk_usd = equity * risk_pct / Decimal("100")
        stop_dist = abs(entry_px - sl)
        if stop_dist <= 0:
            rejects["STOP_INVALID"] += 1
            continue
        raw_qty = risk_usd / stop_dist
        spec = get_instrument_spec(asset.upper())
        try:
            qty = normalize_quantity(raw_qty, spec)
        except Exception:
            rejects["MIN_QUANTITY"] += 1
            continue
        if qty <= 0:
            rejects["MIN_QUANTITY"] += 1
            continue
        ts = df.index[i + 1].to_pydatetime()
        market_open = session_allows_entries(asset_obj.trading_sessions or {}, ts) if asset_obj else True
        route = route_execution_product(asset, direction, execution_model=exec_model)
        req = BrokerOrderRequest(
            symbol=asset,
            asset_class=str(instrument.asset_class),
            direction=direction,
            quantity=qty,
            mark_price=entry_px,
            market_open=market_open,
            data_fresh=True,
            execution_product=route.product.value,
            product_rules_key=route.asset_class_key,
        )
        attempts += 1
        decision = evaluate_broker_order(
            account,
            QUANTARA_STANDARD_PAPER,
            req,
            {"USD": Decimal("1")},
            product_rules=route.rules,
        )
        if decision.accepted:
            valid += 1
        else:
            reason = decision.rejection_reason.value if decision.rejection_reason else None
            rejects[_map_reject(reason, decision.rejection_detail)] += 1

    reject_rate = 1.0 - (valid / attempts) if attempts else 1.0
    valid_rate = valid / signals if signals else 0.0
    return {
        "signals": signals,
        "broker_attempts": attempts,
        "broker_valid": valid,
        "broker_valid_rate": round(valid_rate, 4),
        "broker_reject_rate": round(reject_rate, 4),
        "rejects": dict(rejects),
    }
