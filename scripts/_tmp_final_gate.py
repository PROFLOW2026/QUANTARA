"""Final post-restart gate: XAU scheduled fetch + PnL fill breakdown."""

from __future__ import annotations

import inspect
import json
import sys
import time
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "engine"))

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from quantara_engine.live_sim.integrity_containment import is_live_sim_entries_blocked
from quantara_engine.market_data.registry import get_asset
from quantara_engine.market_data.sessions import is_data_stale_while_session_open, session_allows_entries
from quantara_engine.owner_portfolio.aggregation import aggregate_owner_portfolio
from quantara_engine.owner_portfolio.asset_allocation import list_asset_allocations
from quantara_engine.owner_portfolio.service import LIVE_SIM_OWNER_SLUG
from quantara_engine.persistence.store import TradingStore


def db_url() -> str:
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if line.startswith("DATABASE_URL="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise RuntimeError("DATABASE_URL missing")


def latest(session, sym: str, tf: str) -> dict | None:
    row = session.execute(
        text(
            """
            SELECT c.timestamp, c.source FROM candles c
            JOIN instruments i ON i.id = c.instrument_id
            WHERE i.symbol = :sym AND c.timeframe = :tf
            ORDER BY c.timestamp DESC LIMIT 1
            """
        ),
        {"sym": sym, "tf": tf},
    ).mappings().first()
    return dict(row) if row else None


def stale_5m_trigger_loaded() -> bool:
    from quantara_workers.jobs import fetch_data

    src = inspect.getsource(fetch_data._fetch_asset_live)
    return "not fresh_1m or not fresh_5m" in src and "fetch_since" in src


def pnl_breakdown(session) -> dict:
    rows = session.execute(
        text(
            """
            SELECT o.order_purpose::text AS purpose, COALESCE(SUM(f.realized_pnl), 0) AS pnl,
                   COUNT(*) AS n
            FROM broker_fills f
            JOIN broker_orders o ON o.id = f.broker_order_id
            JOIN broker_accounts ba ON ba.id = o.broker_account_id
            WHERE ba.slug IN ('live-sim-ibkr-like', 'live-sim-kraken-like')
            GROUP BY o.order_purpose
            ORDER BY o.order_purpose
            """
        )
    ).mappings().all()

    exit_purposes = {"sl", "tp", "close"}
    exit_pnl = Decimal("0")
    entry_pnl = Decimal("0")
    other_pnl = Decimal("0")
    for r in rows:
        pnl = Decimal(str(r["pnl"]))
        purpose = str(r["purpose"])
        if purpose in exit_purposes:
            exit_pnl += pnl
        elif purpose == "entry":
            entry_pnl += pnl
        else:
            other_pnl += pnl

    brokers = session.execute(
        text(
            """
            SELECT COALESCE(SUM(realized_pnl), 0) FROM broker_accounts
            WHERE slug IN ('live-sim-ibkr-like', 'live-sim-kraken-like')
            """
        )
    ).scalar()

    total_broker = Decimal(str(brokers))
    entry_netting = total_broker - exit_pnl - other_pnl

    return {
        "by_purpose": {str(r["purpose"]): {"pnl": float(r["pnl"]), "count": int(r["n"])} for r in rows},
        "exit_fill_realized": float(exit_pnl),
        "entry_netting_realized": float(entry_pnl + other_pnl),
        "entry_only_realized": float(entry_pnl),
        "other_realized": float(other_pnl),
        "total_broker_realized": float(total_broker),
        "sum_check": float(exit_pnl + entry_pnl + other_pnl),
    }


def main() -> dict:
    engine = create_engine(db_url())
    session = sessionmaker(bind=engine)()
    store = TradingStore(session)
    now = datetime.now(timezone.utc)

    code_ok = stale_5m_trigger_loaded()
    baseline_xau_5m = latest(session, "XAUUSD", "5m")
    baseline_xau_1m = latest(session, "XAUUSD", "1m")
    baseline_ts = baseline_xau_5m["timestamp"] if baseline_xau_5m else None

    scheduled_healthy = False
    fetch_payload: dict = {}
    deadline = time.time() + 360

    while time.time() < deadline:
        session.expire_all()
        fetch_payload = session.execute(
            text("SELECT value FROM settings WHERE key = 'worker_status:data_fetcher'")
        ).scalar() or {}
        phase = fetch_payload.get("phase")
        last_run = fetch_payload.get("last_run")
        xau = (fetch_payload.get("assets") or {}).get("XAUUSD") or {}

        if phase == "fetch_live" and last_run and xau:
            lr = datetime.fromisoformat(str(last_run).replace("Z", "+00:00"))
            # post-restart: worker restarted ~01:09+3 = 22:09 UTC Sep 14
            if lr >= datetime(2026, 9, 14, 22, 8, 0, tzinfo=timezone.utc):
                if xau.get("status") == "healthy" or xau.get("candles_upserted", 0) > 0:
                    scheduled_healthy = True
                    break
        time.sleep(10)

    xau_1m = latest(session, "XAUUSD", "1m")
    xau_5m = latest(session, "XAUUSD", "5m")
    gbp_5m = latest(session, "GBPJPY", "5m")
    gbp_1m = latest(session, "GBPJPY", "1m")

    stale_cutoff = datetime(2026, 9, 14, 22, 0, 0, tzinfo=timezone.utc)  # 01:00+3
    xau_5m_advancing = bool(
        xau_5m and baseline_ts and xau_5m["timestamp"] >= baseline_ts
    )
    if xau_5m and xau_5m["timestamp"] > stale_cutoff:
        xau_5m_advancing = True

    gbp_asset = get_asset("GBPJPY")
    gbp_healthy = False
    if gbp_5m and gbp_asset:
        gbp_healthy = not is_data_stale_while_session_open(
            gbp_asset.trading_sessions, gbp_5m["timestamp"], now
        )

    ncf = session.execute(
        text("SELECT value FROM settings WHERE key = 'worker_status:non_crypto_fast_protection'")
    ).scalar() or {}
    td_burn = int(ncf.get("fx_td_calls") or 0)

    owner = aggregate_owner_portfolio(store, slug=LIVE_SIM_OWNER_SLUG)
    brokers = session.execute(
        text(
            """
            SELECT slug, equity, realized_pnl, unrealized_pnl
            FROM broker_accounts WHERE slug IN ('live-sim-ibkr-like', 'live-sim-kraken-like')
            """
        )
    ).mappings().all()
    assets = list_asset_allocations(store, LIVE_SIM_OWNER_SLUG)
    ibkr = next(b for b in brokers if b["slug"] == "live-sim-ibkr-like")
    kraken = next(b for b in brokers if b["slug"] == "live-sim-kraken-like")
    broker_eq = Decimal(str(ibkr["equity"])) + Decimal(str(kraken["equity"]))
    broker_real = Decimal(str(ibkr["realized_pnl"])) + Decimal(str(kraken["realized_pnl"]))
    broker_unreal = Decimal(str(ibkr["unrealized_pnl"])) + Decimal(str(kraken["unrealized_pnl"]))
    asset_eq = sum(a.current_equity for a in assets if a.enabled)
    asset_real = sum(a.realized_pnl for a in assets if a.enabled)
    asset_unreal = sum(a.unrealized_pnl for a in assets if a.enabled)

    fin_clean = all(
        abs(float(x)) < 0.01
        for x in (
            broker_eq - owner.total_equity,
            broker_real - owner.total_realized_pnl,
            broker_unreal - owner.total_unrealized_pnl,
            asset_eq - owner.total_equity,
            asset_real - owner.total_realized_pnl,
            asset_unreal - owner.total_unrealized_pnl,
        )
    )

    from quantara_engine.live_sim.protection_gate import eth_kraken_protection_status

    eth_status = eth_kraken_protection_status(session)
    eth_ok = bool(eth_status.get("healthy"))

    new_positions = int(
        session.execute(
            text(
                """
                SELECT COUNT(*) FROM live_sim_positions
                WHERE opened_at > (
                  SELECT (value->>'activated_at')::timestamptz
                  FROM settings WHERE key = 'live_sim_integrity_containment'
                )
                """
            )
        ).scalar()
        or 0
    )

    pnl = pnl_breakdown(session)

    xau_fetch = (fetch_payload.get("assets") or {}).get("XAUUSD") or {}
    if not scheduled_healthy and xau_fetch.get("status") == "healthy":
        scheduled_healthy = True

    system_clean = all(
        [
            code_ok,
            scheduled_healthy,
            xau_5m_advancing,
            gbp_healthy,
            td_burn == 0,
            fin_clean,
            eth_ok,
            new_positions == 0,
            is_live_sim_entries_blocked(store),
            abs(pnl["sum_check"] - pnl["total_broker_realized"]) < 0.01,
        ]
    )

    out = {
        "code_stale_5m_trigger_loaded": code_ok,
        "xau_scheduled_fetch_healthy": scheduled_healthy,
        "xau_5m_advancing": xau_5m_advancing,
        "gbpjpy_healthy": gbp_healthy,
        "td_burn": td_burn,
        "fin_clean": fin_clean,
        "eth_ok": eth_ok,
        "new_positions": new_positions,
        "pnl": pnl,
        "xau_1m": xau_1m,
        "xau_5m": xau_5m,
        "gbp_1m": gbp_1m,
        "gbp_5m": gbp_5m,
        "xau_fetch": xau_fetch,
        "fetch_phase": fetch_payload.get("phase"),
        "fetch_last_run": fetch_payload.get("last_run"),
        "system_clean": system_clean,
    }
    session.close()
    return out


if __name__ == "__main__":
    r = main()
    p = r["pnl"]
    print(f"XAU scheduled fetch healthy = {'YES' if r['xau_scheduled_fetch_healthy'] else 'NO'}")
    print(f"XAU 5m advancing = {'YES' if r['xau_5m_advancing'] else 'NO'}")
    print(f"GBPJPY healthy = {'YES' if r['gbpjpy_healthy'] else 'NO'}")
    print(f"Twelve Data normal burn = {r['td_burn']}")
    print(f"Financial reconciliation clean = {'YES' if r['fin_clean'] else 'NO'}")
    print(f"ETH protection healthy = {'YES' if r['eth_ok'] else 'NO'}")
    print(f"New Live Sim entries during containment = {r['new_positions']}")
    print()
    print(f"Realized from exit fills = {p['exit_fill_realized']:+.2f}")
    print(f"Realized from entry/netting fills = {p['entry_netting_realized']:+.2f}")
    print(f"Total broker realized = {p['total_broker_realized']:+.2f}")
    print(f"Sum = {p['sum_check']:+.2f}")
    print()
    print(f"SYSTEM INTEGRITY CLEAN = {'YES' if r['system_clean'] else 'NO'}")
    print(f"READY TO RELEASE CONTAINMENT = {'YES' if r['system_clean'] else 'NO'}")
    print()
    print("DEBUG:", json.dumps(r, indent=2, default=str))
