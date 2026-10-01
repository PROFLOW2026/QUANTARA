"""Classify V3.1 full-broker replay funnel (signals → orders → fills → rejects)."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from quantara_engine.domain.types import DecisionType


TAXONOMY = [
    "MIN_QUANTITY",
    "QTY_STEP",
    "MARGIN",
    "INSUFFICIENT_BUYING_POWER",
    "ASSET_RISK",
    "OWNER_RISK",
    "BROKER_RISK",
    "SYMBOL_EXPOSURE",
    "SESSION",
    "STALE",
    "PRICE_INVALID",
    "STOP_INVALID",
    "TP_INVALID",
    "NOTIONAL_LIMIT",
    "DUPLICATE",
    "ORDER_STATE",
    "SHORT_NOT_ALLOWED",
    "RISK_ENGINE",
    "OTHER",
]


def _classify_decision(dtype: str, message: str, meta: dict) -> str:
    msg = (message or "").lower()
    br = str(meta.get("broker_reason") or meta.get("broker_rejection") or "").lower()
    combined = f"{msg} {br}"

    if "duplicate" in combined or "opportunity" in combined:
        return "DUPLICATE"
    if "stale" in combined:
        return "STALE"
    if "session" in combined or "market closed" in combined or "outside market" in combined:
        return "SESSION"
    if "step" in combined and "qty" in combined:
        return "QTY_STEP"
    if "min" in combined and "qty" in combined:
        return "MIN_QUANTITY"
    if "invalid_quantity" in br or "invalid quantity" in combined:
        if "step" in combined:
            return "QTY_STEP"
        return "MIN_QUANTITY"
    if "insufficient_buying_power" in br:
        return "INSUFFICIENT_BUYING_POWER"
    if "insufficient_margin" in br or "margin" in combined:
        return "MARGIN"
    if "max_asset_exposure" in br or "asset exposure" in combined:
        return "SYMBOL_EXPOSURE"
    if "max_leverage" in br or "max_gross_leverage" in br:
        return "BROKER_RISK"
    if "max_order_notional" in br:
        return "NOTIONAL_LIMIT"
    if "short" in combined and ("not allowed" in combined or "locate" in combined):
        return "SHORT_NOT_ALLOWED"
    if "risk" in combined and ("denied" in combined or "limit" in combined):
        return "RISK_ENGINE"
    if dtype in ("broker_rejected", "broker_capability_denied"):
        return "BROKER_RISK"
    if dtype == "risk_denied":
        return "RISK_ENGINE"
    if "stop" in combined and "invalid" in combined:
        return "STOP_INVALID"
    if "tp" in combined and "invalid" in combined:
        return "TP_INVALID"
    return "OTHER"


def _decision_meta(d) -> dict:
    m = getattr(d, "metadata", None) or {}
    return dict(m) if isinstance(m, dict) else {}


def _decision_message(d) -> str:
    return str(getattr(d, "message", "") or "")


@dataclass
class FunnelAggregate:
    signal_count: int = 0
    risk_approved: int = 0
    broker_attempts: int = 0
    fills: int = 0
    rejects: Counter = field(default_factory=Counter)
    examples: list[dict[str, Any]] = field(default_factory=list)

    def add_example(self, ex: dict, *, cap: int = 10) -> None:
        if len(self.examples) < cap:
            self.examples.append(ex)


def aggregate_pipeline_decisions(
    decisions: list,
    *,
    asset: str,
    strategy_key: str,
) -> FunnelAggregate:
    agg = FunnelAggregate()
    for d in decisions:
        dtype = d.decision_type.value if hasattr(d.decision_type, "value") else str(d.decision_type)
        msg = _decision_message(d)
        meta = _decision_meta(d)
        if dtype in ("buy_signal", "sell_signal"):
            agg.signal_count += 1
        if dtype == "risk_approved":
            agg.risk_approved += 1
        if dtype in ("broker_rejected", "broker_capability_denied"):
            agg.broker_attempts += 1
            cat = _classify_decision(dtype, msg, meta)
            agg.rejects[cat] += 1
            agg.add_example(
                {
                    "asset": asset,
                    "strategy": strategy_key,
                    "timestamp": getattr(d, "candle_timestamp", None),
                    "decision": dtype,
                    "message": msg[:200],
                    "metadata": meta,
                    "reject_category": cat,
                }
            )
        if dtype == "risk_denied":
            agg.broker_attempts += 1
            cat = _classify_decision(dtype, msg, meta)
            agg.rejects[cat] += 1
            if "stale" in msg.lower() or "catch-up" in msg.lower():
                agg.add_example(
                    {
                        "asset": asset,
                        "strategy": strategy_key,
                        "timestamp": getattr(d, "candle_timestamp", None),
                        "reject_category": cat,
                        "message": msg[:200],
                    }
                )
        if dtype == "position_open":
            agg.fills += 1
    return agg


def merge_funnels(parts: list[FunnelAggregate]) -> dict[str, Any]:
    total_signals = sum(p.signal_count for p in parts)
    total_orders = sum(p.risk_approved for p in parts)
    total_attempts = sum(p.broker_attempts for p in parts)
    total_fills = sum(p.fills for p in parts)
    rejects: Counter = Counter()
    examples: list[dict] = []
    for p in parts:
        rejects.update(p.rejects)
        examples.extend(p.examples[:10])
    total_rejects = sum(rejects.values())
    pct = lambda n: round(n / total_rejects * 100, 2) if total_rejects else 0.0
    top = sorted(rejects.items(), key=lambda x: -x[1])[:5]
    by_cat = {k: {"count": rejects.get(k, 0), "pct": pct(rejects.get(k, 0))} for k in TAXONOMY}
    return {
        "signal_count": total_signals,
        "candidate_orders": total_orders,
        "broker_attempts": total_attempts,
        "fills": total_fills,
        "rejects": total_rejects,
        "by_category": by_cat,
        "top_reject_reasons": [{"category": k, "count": v, "pct": pct(v)} for k, v in top],
        "representative_examples": examples[:10],
    }


def classify_reject_root_cause(summary: dict[str, Any]) -> dict[str, str]:
    """A–E classification for top categories."""
    out: dict[str, str] = {}
    top = summary.get("top_reject_reasons") or []
    for row in top[:5]:
        cat = row["category"]
        if cat in ("STALE", "SESSION"):
            out[cat] = "A. LEGITIMATE MARKET/BROKER CONSTRAINT"
        elif cat in ("MIN_QUANTITY", "QTY_STEP", "NOTIONAL_LIMIT"):
            out[cat] = "B. STRATEGY GENERATES UNTRADEABLE ORDERS"
        elif cat in ("RISK_ENGINE", "BROKER_RISK", "SYMBOL_EXPOSURE"):
            out[cat] = "C. SIZING/POLICY DESIGN ISSUE"
        elif cat in ("DUPLICATE", "ORDER_STATE"):
            out[cat] = "D. BACKTEST/BROKER ADAPTER BUG"
        elif cat == "OTHER" and row["count"] > 1000:
            out[cat] = "D. BACKTEST/BROKER ADAPTER BUG (investigate)"
        else:
            out[cat] = "E. OTHER"
    return out
