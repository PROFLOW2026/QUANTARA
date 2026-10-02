#!/usr/bin/env python3
"""Read-only forensics for non-US Stage A/B blockers (no Wave 3)."""

from __future__ import annotations

import json
import sys
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps" / "engine"))

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from quantara_engine.market_data.registry import get_asset
from quantara_engine.persistence.store import TradingStore
from quantara_engine.research.v3.constants import V3_RESEARCH_START
from quantara_engine.research.v3_2.rule_lab import SimTrade, _simulate_n1
from quantara_engine.research.v3_2.stage_b import validate_survivor_broker
from quantara_engine.research.v3_2.job_plan import V32Job
from quantara_engine.research.v3_1.broker_replay import replay_finalist_broker
from quantara_engine.strategies.common.indicators import atr, candles_to_df


def db_url() -> str:
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if line.startswith("DATABASE_URL="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise RuntimeError("DATABASE_URL missing")


def load_candles(store: TradingStore, symbol: str, tf: str, limit: int = 5000):
    inst = store.get_instrument_by_symbol(symbol)
    if not inst:
        return inst, []
    candles = store.list_candles(inst.id, tf, since=V3_RESEARCH_START, limit=limit)
    return inst, candles


def btc_pnl_semantics() -> dict:
    """Deterministic micro-samples + diagnose cost term in rule_lab _simulate_n1."""
    # Synthetic 5-bar paths
    samples = []
    for label, entry, exit_px in [
        ("long_up", 100_000.0, 101_000.0),
        ("long_down", 100_000.0, 99_000.0),
    ]:
        risk_amt = 25.0
        risk_dist = 500.0
        units = risk_amt / risk_dist
        cost_bps = 12.0
        cost_bug = entry * (cost_bps / 10000.0) * 2
        cost_fixed_notional = entry * units * (cost_bps / 10000.0) * 2
        gross = (exit_px - entry) * units
        pnl_bug = gross - cost_bug
        pnl_fixed = gross - cost_fixed_notional
        samples.append(
            {
                "case": label,
                "entry": entry,
                "exit": exit_px,
                "units": round(units, 6),
                "gross_pnl": round(gross, 4),
                "cost_current_formula": round(cost_bug, 4),
                "net_pnl_current": round(pnl_bug, 4),
                "cost_notional_scaled": round(cost_fixed_notional, 4),
                "net_pnl_if_notional_cost": round(pnl_fixed, 4),
                "price_up_positive_gross": gross > 0 if label == "long_up" else gross < 0,
            }
        )

    # One real donchian-style bar pair from BTC if candles exist
    session = sessionmaker(bind=create_engine(db_url(), pool_pre_ping=True))()
    store = TradingStore(session)
    _, candles = load_candles(store, "BTCUSD", "15m", limit=800)
    real = []
    if len(candles) > 50:
        df = candles_to_df(candles)
        df.index = pd.to_datetime([c.timestamp for c in candles], utc=True)
        atr_s = atr(df, 14)
        i = 100
        entry = float(df["open"].iloc[i + 1])
        risk_dist = float(atr_s.iloc[i]) * 1.4
        units = 25.0 / risk_dist
        # force TP hit
        exit_tp = entry + float(atr_s.iloc[i]) * 2.0
        gross = (exit_tp - entry) * units
        cost_bug = entry * 0.0012 * 2
        real.append(
            {
                "bar_index": i,
                "entry": entry,
                "tp_exit": exit_tp,
                "atr": float(atr_s.iloc[i]),
                "units": units,
                "gross_tp_pnl": round(gross, 4),
                "cost_subtracted": round(cost_bug, 4),
                "net_with_current_cost": round(gross - cost_bug, 4),
                "winner_possible": gross > cost_bug,
            }
        )
    session.close()
    return {"synthetic": samples, "real_btc_bar_example": real}


def decision_forensics(symbol: str, key: str) -> dict:
    session = sessionmaker(bind=create_engine(db_url(), pool_pre_ping=True))()
    store = TradingStore(session)
    parts = key.split("|")
    family, variant, asset, tf, direction = parts[0], parts[1], parts[2], parts[3], parts[4]
    # load params from checkpoint
    cp = ROOT / "scripts/research/non_us_v3_2_discovery_checkpoint.jsonl"
    params = {}
    for line in cp.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        if row.get("key") == key:
            params = row.get("parameters") or {}
            break
    inst, candles = load_candles(store, asset, tf, limit=15000)
    job = V32Job(
        index=0,
        key=key,
        family=family,
        variant_id=variant,
        asset=asset,
        timeframe=tf,
        direction=direction,
        parameters=params,
    )
    rep = replay_finalist_broker(
        store,
        key=key,
        family=family,
        asset=asset,
        timeframe=tf,
        direction=direction,
        parameters=params,
        instrument=inst,
        candles=candles,
    )
    decisions = rep.get("pipeline_decisions") or []
    by_type = Counter(getattr(d, "decision_type", None).value if getattr(d, "decision_type", None) else str(d) for d in decisions)
    risk_msgs = [
        getattr(d, "message", "") or ""
        for d in decisions
        if getattr(getattr(d, "decision_type", None), "value", "") == "risk_denied"
    ]
    broker_msgs = [
        getattr(d, "message", "") or ""
        for d in decisions
        if getattr(getattr(d, "decision_type", None), "value", "") in ("broker_rejected", "broker_capability_denied")
    ]
    inst_row = {
        "symbol": inst.symbol if inst else None,
        "min_quantity": str(inst.min_quantity) if inst else None,
        "quantity_step": str(inst.quantity_step) if inst else None,
        "quote_currency": inst.quote_currency if inst else None,
        "product_id": getattr(get_asset(asset), "provider_symbols", {}).get("coinbase") if asset else None,
    }
    session.close()
    return {
        "key": key,
        "symbol": symbol,
        "decision_counts": dict(by_type),
        "rejections_summary": rep.get("rejections"),
        "risk_denied_messages_sample": risk_msgs[:15],
        "risk_denied_unique": list(dict.fromkeys(risk_msgs))[:20],
        "broker_rejected_messages_sample": broker_msgs[:15],
        "broker_rejected_unique": list(dict.fromkeys(broker_msgs))[:20],
        "instrument": inst_row,
        "signal_count": rep.get("signal_count"),
        "orders": rep.get("orders"),
        "fills": rep.get("fills"),
    }


def fx_latest() -> dict:
    session = sessionmaker(bind=create_engine(db_url(), pool_pre_ping=True))()
    store = TradingStore(session)
    out = {}
    for sym in ("EURUSD", "USDJPY", "GBPJPY", "XAUUSD"):
        inst = store.get_instrument_by_symbol(sym)
        if not inst:
            out[sym] = None
            continue
        for tf in ("5m", "15m", "1h"):
            ts = store.latest_candle_timestamp(inst.id, tf)
            out[f"{sym}_{tf}"] = ts.isoformat() if ts else None
        asset = get_asset(sym)
        out[f"{sym}_provider"] = asset.primary_provider.value if asset else None
    session.close()
    return out


def main() -> None:
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "btc_pnl": btc_pnl_semantics(),
        "eth": decision_forensics("ETHUSD", "donchian_breakout_v2|v1|ETHUSD|1h|long"),
        "sol": decision_forensics("SOLUSD", "donchian_breakout_v2|v1|SOLUSD|15m|long"),
        "xrp": decision_forensics("XRPUSD", "donchian_breakout_v2|v1|XRPUSD|15m|long"),
        "fx_latest": fx_latest(),
    }
    out = ROOT / "scripts/research/non_us_blocker_forensics_report.json"
    out.write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, default=str))


if __name__ == "__main__":
    main()
