"""Phase 2 — Live Sim profit discovery observability (measure only, no blocking caps)."""

from __future__ import annotations

import statistics
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import text

from quantara_engine.broker.accounts import LIVE_SIM_10K_ACCOUNT_SLUG
from quantara_engine.broker.instruments import get_instrument_spec
from quantara_engine.live_sim.analytics import build_live_sim_summary
from quantara_engine.live_sim.audit_scope import resolve_live_sim_audit_since
from quantara_engine.live_sim.execution_routing import list_active_live_sim_broker_account_ids
from quantara_engine.live_sim.risk_policy import compute_open_sl_risk
from quantara_engine.live_sim.v32_registry import load_v32_live_sim_active_combinations
from quantara_engine.persistence.store import TradingStore


def lifecycle_label(*, live_trades: int, net_pnl: float, expectancy: float | None, pf: float | None) -> str:
    if live_trades < 20:
        return "NEW"
    if live_trades < 50:
        if net_pnl > 0 and (expectancy or 0) > 0:
            return "LEARNING"
        if net_pnl <= 0:
            return "WEAK"
        return "LEARNING"
    if live_trades < 100:
        if (expectancy or 0) > 0 and (pf or 0) >= 1.05:
            return "PROVING"
        if net_pnl < 0 and (expectancy or 0) <= 0:
            return "DEMOTE_CANDIDATE"
        return "WEAK" if net_pnl < 0 else "LEARNING"
    if (expectancy or 0) > 0 and (pf or 0) >= 1.1:
        return "STRONG"
    if net_pnl < 0 or (expectancy or 0) <= 0:
        return "DEMOTE_CANDIDATE"
    return "PROVING"


def _audit_since(store: TradingStore) -> datetime | None:
    legacy = store.session.execute(
        text(
            """
            SELECT activated_at, account_metadata
            FROM broker_accounts WHERE slug = :slug
            """
        ),
        {"slug": LIVE_SIM_10K_ACCOUNT_SLUG},
    ).mappings().first()
    if not legacy:
        return None
    return resolve_live_sim_audit_since(
        store,
        account_metadata=dict(legacy.get("account_metadata") or {}),
        activated_at=legacy.get("activated_at"),
    )


def _position_rows(store: TradingStore, account_ids: list[str], since: datetime | None) -> list[dict]:
    if not account_ids:
        return []
    since_sql = "AND p.opened_at >= :since" if since else ""
    params: dict[str, Any] = {"aids": account_ids}
    if since:
        params["since"] = since
    rows = store.session.execute(
        text(
            f"""
            SELECT
              p.id::text AS position_id,
              i.symbol,
              p.timeframe,
              p.direction::text AS direction,
              p.strategy_slug,
              p.opened_at,
              p.closed_at,
              p.status,
              p.planned_sl_risk_usd,
              l.metadata->>'v32_candidate_key' AS v32_candidate_key,
              (
                SELECT COALESCE(SUM(al.realized_pnl), 0)
                FROM broker_attribution_ledger al
                WHERE al.strategy_position_id = p.id AND al.exit_price IS NOT NULL
              ) AS realized_pnl
            FROM live_sim_positions p
            JOIN instruments i ON i.id = p.instrument_id
            LEFT JOIN live_sim_allocation_log l ON l.id = p.allocation_log_id
            WHERE p.broker_account_id = ANY(CAST(:aids AS uuid[]))
              {since_sql}
            """
        ),
        params,
    ).mappings().all()
    return [dict(r) for r in rows]


def _match_v32_key(row: dict, manifest_index: dict[tuple[str, str, str], str]) -> str | None:
    explicit = row.get("v32_candidate_key")
    if explicit:
        return str(explicit)
    key = (
        str(row["symbol"]).upper(),
        str(row["timeframe"]),
        str(row["direction"]).lower(),
    )
    return manifest_index.get(key)


def build_discovery_observability_report(store: TradingStore) -> dict[str, Any]:
    summary = build_live_sim_summary(store)
    since = _audit_since(store)
    account_ids = list_active_live_sim_broker_account_ids(store)
    positions = _position_rows(store, account_ids, since)

    manifest = load_v32_live_sim_active_combinations()
    manifest_by_key = {r["key"]: r for r in manifest}
    slot_index: dict[tuple[str, str, str], str] = {}
    for r in manifest:
        slot_index[(r["asset"], r["timeframe"], r["direction"])] = r["key"]

    by_candidate: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "live_trades": 0,
            "wins": 0,
            "losses": 0,
            "net_pnl": 0.0,
            "risk_usd": [],
            "open_risk": 0.0,
            "first_open": None,
            "last_close": None,
        }
    )

    for row in positions:
        ck = _match_v32_key(row, slot_index) or f"unmapped|{row['symbol']}|{row['timeframe']}|{row['direction']}"
        bucket = by_candidate[ck]
        pnl = float(row.get("realized_pnl") or 0)
        if row["status"] == "closed":
            bucket["live_trades"] += 1
            if pnl > 0:
                bucket["wins"] += 1
                bucket.setdefault("gross_profit", 0.0)
                bucket["gross_profit"] += pnl
            elif pnl < 0:
                bucket["losses"] += 1
                bucket.setdefault("gross_loss", 0.0)
                bucket["gross_loss"] += abs(pnl)
            bucket["net_pnl"] += pnl
            closed = row.get("closed_at")
            if closed and (bucket["last_close"] is None or closed > bucket["last_close"]):
                bucket["last_close"] = closed
        elif row["status"] == "open":
            bucket["open_risk"] += float(row.get("planned_sl_risk_usd") or 0)
        opened = row.get("opened_at")
        if opened and (bucket["first_open"] is None or opened < bucket["first_open"]):
            bucket["first_open"] = opened
        if row.get("planned_sl_risk_usd"):
            bucket["risk_usd"].append(float(row["planned_sl_risk_usd"]))

    leaderboard: list[dict[str, Any]] = []
    for key, stats in by_candidate.items():
        meta = manifest_by_key.get(key, {})
        trades = stats["live_trades"]
        wins = stats["wins"]
        losses = stats["losses"]
        pnls = []
        if trades:
            avg = stats["net_pnl"] / trades
        else:
            avg = None
        gp = float(stats.get("gross_profit") or 0)
        gl = float(stats.get("gross_loss") or 0)
        pf = round(gp / gl, 4) if gl else None
        avg_r = None
        if stats["risk_usd"]:
            avg_r = round(stats["net_pnl"] / sum(stats["risk_usd"]) * len(stats["risk_usd"]), 4)
        days_active = None
        if stats["first_open"]:
            days_active = (
                datetime.now(timezone.utc)
                - stats["first_open"].replace(tzinfo=timezone.utc)
            ).days
        leaderboard.append(
            {
                "candidate_key": key,
                "asset": meta.get("asset") or key.split("|")[2] if "|" in key else None,
                "strategy": meta.get("family"),
                "version": meta.get("variant_id"),
                "timeframe": meta.get("timeframe"),
                "direction": meta.get("direction"),
                "live_trades": trades,
                "wins": wins,
                "losses": losses,
                "net_pnl": round(stats["net_pnl"], 2),
                "profit_factor": pf,
                "expectancy_usd": round(avg, 2) if avg is not None else None,
                "average_r": avg_r,
                "open_planned_sl_risk_usd": round(stats["open_risk"], 2),
                "days_active": days_active,
                "lifecycle": lifecycle_label(
                    live_trades=trades,
                    net_pnl=stats["net_pnl"],
                    expectancy=avg,
                    pf=pf,
                ),
            }
        )

    leaderboard.sort(key=lambda r: (-(r.get("net_pnl") or 0), -(r.get("live_trades") or 0)))

    open_by_asset: dict[str, float] = defaultdict(float)
    open_by_class: dict[str, float] = defaultdict(float)
    open_by_dir: dict[str, float] = defaultdict(float)
    open_count_by_symbol: dict[str, int] = defaultdict(int)
    pnl_by_asset: dict[str, float] = defaultdict(float)
    pnl_by_family: dict[str, float] = defaultdict(float)

    for row in positions:
        sym = str(row["symbol"]).upper()
        if row["status"] == "open":
            open_by_asset[sym] += float(row.get("planned_sl_risk_usd") or 0)
            open_count_by_symbol[sym] += 1
            try:
                ac = get_instrument_spec(sym).asset_class
            except KeyError:
                ac = "unknown"
            open_by_class[ac] += float(row.get("planned_sl_risk_usd") or 0)
            open_by_dir[str(row["direction"])] += float(row.get("planned_sl_risk_usd") or 0)
        if row["status"] == "closed":
            pnl_by_asset[sym] += float(row.get("realized_pnl") or 0)
            ck = _match_v32_key(row, slot_index)
            fam = (manifest_by_key.get(ck or "") or {}).get("family") or "unmapped"
            pnl_by_family[fam] += float(row.get("realized_pnl") or 0)

    total_open_risk = sum(open_by_asset.values())
    total_closed_pnl = sum(pnl_by_asset.values())
    asset_pnl_share = {
        k: round(v / total_closed_pnl * 100, 2) if total_closed_pnl else 0.0
        for k, v in sorted(pnl_by_asset.items(), key=lambda x: -abs(x[1]))
    }

    open_risk_total = float(
        sum(compute_open_sl_risk(store, aid).total_sl_risk_usd for aid in account_ids)
    )
    clean = summary.get("clean_window") or {}
    master = {
        "start_usd": 10_000,
        "current_equity_usd": summary.get("equity"),
        "return_pct": summary.get("total_return_pct"),
        "realized_pnl_usd": summary.get("realized_pnl"),
        "unrealized_pnl_usd": summary.get("unrealized_pnl"),
        "max_drawdown_pct": summary.get("current_drawdown_pct"),
        "total_live_trades": clean.get("closed_positions") or summary.get("closed_positions_count"),
        "profit_factor": clean.get("profit_factor"),
        "expectancy_usd": clean.get("expectancy_usd"),
        "open_planned_sl_risk_usd": float(summary.get("open_sl_risk_usd") or open_risk_total),
        "fees_usd": summary.get("fees_paid"),
        "audit_since": since.isoformat() if since else None,
    }

    overlap_proxy = {
        sym: cnt for sym, cnt in open_count_by_symbol.items() if cnt > 1
    }

    traded = [r for r in leaderboard if (r.get("live_trades") or 0) > 0]
    bottom = sorted(traded, key=lambda r: (r.get("net_pnl") or 0))[:10]

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "master_scoreboard": master,
        "active_candidates_manifest": len(manifest),
        "leaderboard": leaderboard,
        "top_contributors": traded[:10],
        "bottom_contributors": bottom,
        "concentration": {
            "open_planned_risk_by_asset": dict(open_by_asset),
            "open_planned_risk_by_asset_class": dict(open_by_class),
            "open_planned_risk_by_direction": dict(open_by_dir),
            "total_open_planned_sl_risk_usd": round(open_risk_total, 2),
            "pnl_by_asset": dict(pnl_by_asset),
            "pnl_concentration_pct_by_asset": asset_pnl_share,
            "pnl_by_strategy_family": dict(pnl_by_family),
            "simultaneous_strategies_same_symbol": overlap_proxy,
        },
        "live_trades_since_anchor": sum(r["live_trades"] for r in leaderboard),
    }
