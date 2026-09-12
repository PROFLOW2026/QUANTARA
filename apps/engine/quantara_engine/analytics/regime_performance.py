"""Performance attribution by market regime at trade entry."""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from typing import Any

from quantara_engine.competition.robot_registry import ROBOT_LABELS
from quantara_engine.market_regime.snapshots import regime_at_entry


def build_regime_performance(store) -> dict[str, Any]:
    _, _, entries = store.list_all_competition_entries()
    portfolio_meta: dict[str, dict[str, str]] = {}
    for entry in entries:
        slug = entry["instance"].strategy_slug
        portfolio_meta[entry["portfolio"].id] = {
            "robot_label": ROBOT_LABELS.get(slug, slug),
            "strategy_slug": slug,
            "risk_slug": entry["risk_profile"].slug,
        }

    experiment_ids = list({entry["instance"].experiment_id for entry in entries if entry["instance"].experiment_id})
    trades: list[dict[str, Any]] = []
    for exp_id in experiment_ids:
        if exp_id:
            trades.extend(store.list_competition_trades(exp_id, limit=5000))

    buckets: dict[tuple[str, str, str, str], dict[str, Any]] = defaultdict(
        lambda: {
            "trades": 0,
            "wins": 0,
            "realized_pnl": Decimal("0"),
            "fees": Decimal("0"),
            "long_trades": 0,
            "short_trades": 0,
            "r_sum": Decimal("0"),
            "r_count": 0,
        }
    )

    for trade in trades:
        pid = trade.get("portfolio_id")
        meta = portfolio_meta.get(pid, {})
        instrument = trade.get("instrument") or trade.get("symbol") or ""
        opened_at_raw = trade.get("opened_at")
        if not opened_at_raw:
            continue
        from datetime import datetime

        opened_at = (
            opened_at_raw
            if isinstance(opened_at_raw, datetime)
            else datetime.fromisoformat(str(opened_at_raw).replace("Z", "+00:00"))
        )
        regime = regime_at_entry(store, instrument, "15m", opened_at) or {}
        structure = regime.get("structure_regime") or "UNKNOWN"
        volatility = regime.get("volatility_regime") or "UNKNOWN"
        robot = meta.get("robot_label") or "unknown"
        risk = meta.get("risk_slug") or "unknown"
        key = (robot, structure, volatility, risk)
        bucket = buckets[key]
        pnl = Decimal(str(trade.get("realized_pnl") or trade.get("pnl") or 0))
        fees = Decimal(str(trade.get("fees_total") or trade.get("fees") or 0))
        bucket["trades"] += 1
        bucket["realized_pnl"] += pnl
        bucket["fees"] += fees
        if pnl > 0:
            bucket["wins"] += 1
        direction = str(trade.get("direction") or "").lower()
        if direction == "long":
            bucket["long_trades"] += 1
        elif direction == "short":
            bucket["short_trades"] += 1
        if trade.get("r_multiple") is not None:
            bucket["r_sum"] += Decimal(str(trade["r_multiple"]))
            bucket["r_count"] += 1

    rows = []
    for (robot, structure, volatility, risk), bucket in sorted(buckets.items()):
        trades_count = bucket["trades"]
        wins = bucket["wins"]
        realized = bucket["realized_pnl"]
        rows.append(
            {
                "robot_label": robot,
                "structure_regime": structure,
                "volatility_regime": volatility,
                "risk_slug": risk,
                "trades": trades_count,
                "win_rate": round(wins / trades_count * 100, 2) if trades_count else 0.0,
                "realized_pnl": float(realized),
                "fees": float(bucket["fees"]),
                "long_trades": bucket["long_trades"],
                "short_trades": bucket["short_trades"],
                "average_r": float(bucket["r_sum"] / bucket["r_count"]) if bucket["r_count"] else None,
                "average_trade": float(realized / trades_count) if trades_count else 0.0,
            }
        )

    return {"rows": rows, "trade_count": len(trades)}
