#!/usr/bin/env python3
"""Compare Stage-A rule_lab sim vs Stage-B broker trades for SOL/XRP Donchian."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps" / "engine"))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from quantara_engine.persistence.store import TradingStore
from quantara_engine.research.v3.constants import V3_RESEARCH_START
from quantara_engine.research.v3_2.job_plan import V32Job
from quantara_engine.research.v3_2.rule_lab import backtest_family, entry_mask_series, _df, _simulate_n1
from quantara_engine.research.v3_2.stage_b import validate_survivor_broker
from quantara_engine.strategies.common.indicators import atr


def db_url() -> str:
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if line.startswith("DATABASE_URL="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise RuntimeError("DATABASE_URL missing")


def strategy_style_sl_tp(df: pd.DataFrame, i: int, direction: str, atr_sl: float, atr_tp: float) -> tuple[float, float, float]:
    """Mirror v32-p2-live-sim: SL/TP from signal bar close + ATR(14) at signal."""
    close = float(df["close"].iloc[i])
    atr_val = float(atr(df.iloc[: i + 1], 14).iloc[-1]) if i >= 13 else float(atr(df, 14).iloc[i])
    if direction == "long":
        return close, close - atr_val * atr_sl, close + atr_val * atr_tp
    return close, close + atr_val * atr_sl, close - atr_val * atr_tp


def analyze(asset: str) -> dict:
    params = {"channel": 20, "atr_sl": 1.2, "atr_tp": 2.0}
    key = f"donchian_breakout_v2|v1|{asset}|15m|long"
    session = sessionmaker(bind=create_engine(db_url(), pool_pre_ping=True))()
    store = TradingStore(session)
    inst = store.get_instrument_by_symbol(asset)
    candles = store.list_candles(inst.id, "15m", since=V3_RESEARCH_START, limit=20000)
    df = _df(candles)
    mask = entry_mask_series("donchian_breakout_v2", df, direction="long", parameters=params)
    atr_sl, atr_tp = 1.2, 2.0

    # Current Stage-A sim
    trades_a = backtest_family(
        "donchian_breakout_v2", candles=candles, direction="long", parameters=params
    )
    pnls_a = [t.realized_pnl for t in trades_a]

    # Stage-A with strategy-aligned SL/TP (absolute from signal close)
    def sim_aligned():
        atr_s = atr(df, 14)
        out = []
        n = len(df)
        i = 0
        while i < n - 2:
            if not bool(mask.iloc[i]) or pd.isna(atr_s.iloc[i]) or atr_s.iloc[i] <= 0:
                i += 1
                continue
            sig_close, sl_px, tp_px = strategy_style_sl_tp(df, i, "long", atr_sl, atr_tp)
            entry = float(df["open"].iloc[i + 1])
            risk_amt = 25.0
            risk_dist = abs(entry - sl_px)
            if risk_dist <= 0:
                i += 1
                continue
            units = risk_amt / risk_dist
            cost = entry * units * (12.0 / 10000.0) * 2
            j = i + 2
            exit_px = entry
            hit = False
            while j < min(n, i + 2 + 20):
                hi = float(df["high"].iloc[j])
                lo = float(df["low"].iloc[j])
                if lo <= sl_px:
                    exit_px = sl_px
                    hit = True
                    break
                if hi >= tp_px:
                    exit_px = tp_px
                    hit = True
                    break
                j += 1
            if not hit:
                exit_px = float(df["close"].iloc[j - 1])
            pnl = (exit_px - entry) * units - cost
            out.append(pnl)
            i = j
        return out

    pnls_aligned = sim_aligned()

    job = V32Job(0, key, "donchian_breakout_v2", "v1", asset, "15m", "long", params)
    sb = validate_survivor_broker(job=job, candles=candles, store=store, instrument=inst)
    br = sb.get("broker_replay") or {}
    trade_rows = sb.get("trade_rows") or []

    # Signal counts
    signal_idx = [i for i in range(len(df) - 2) if bool(mask.iloc[i])]

    samples = []
    for i in signal_idx[:8]:
        entry = float(df["open"].iloc[i + 1])
        sc, sl_px, tp_px = strategy_style_sl_tp(df, i, "long", atr_sl, atr_tp)
        risk_dist_old = float(atr(df, 14).iloc[i]) * atr_sl
        samples.append(
            {
                "signal_ts": str(df.index[i]),
                "entry_ts": str(df.index[i + 1]),
                "signal_close": sc,
                "entry_open": entry,
                "sl_strategy_abs": sl_px,
                "tp_strategy_abs": tp_px,
                "sl_stage_a_entry_relative": entry - risk_dist_old,
                "tp_stage_a_entry_relative": entry + float(atr(df, 14).iloc[i]) * atr_tp,
                "sl_delta_vs_strategy": (entry - risk_dist_old) - sl_px,
            }
        )

    session.close()
    wins_a = sum(1 for p in pnls_a if p > 0)
    wins_al = sum(1 for p in pnls_aligned if p > 0)
    return {
        "asset": asset,
        "candles": len(candles),
        "raw_signals": len(signal_idx),
        "stage_a_trades": len(trades_a),
        "stage_a_wins": wins_a,
        "stage_a_sum_pnl": round(sum(pnls_a), 2),
        "stage_a_aligned_trades": len(pnls_aligned),
        "stage_a_aligned_wins": wins_al,
        "stage_a_aligned_sum_pnl": round(sum(pnls_aligned), 2),
        "stage_b_trades": br.get("trades"),
        "stage_b_pf": br.get("pf"),
        "stage_b_expectancy_r": br.get("expectancy_r"),
        "stage_b_classification": br.get("classification"),
        "sample_signal_sl_mismatch": samples[:5],
        "stage_b_first_trades": [
            {"opened": str(r[0]), "closed": str(r[1]), "pnl": r[3]} for r in trade_rows[:5]
        ],
    }


def main() -> None:
    out = {"sol": analyze("SOLUSD"), "xrp": analyze("XRPUSD")}
    path = ROOT / "scripts/research/stage_ab_divergence_report.json"
    path.write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
