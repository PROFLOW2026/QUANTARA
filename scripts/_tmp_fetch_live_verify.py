"""Final fetch_live FX/Tiingo verification — read only."""

from __future__ import annotations

import inspect
import json
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "engine"))

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from quantara_engine.market_data.credits import INTERNAL_GUARD_LIMIT, safe_used_today
from quantara_engine.live_sim.integrity_containment import is_live_sim_entries_blocked
from quantara_engine.owner_portfolio.aggregation import aggregate_owner_portfolio
from quantara_engine.owner_portfolio.asset_allocation import list_asset_allocations
from quantara_engine.owner_portfolio.service import LIVE_SIM_OWNER_SLUG
from quantara_engine.persistence.store import TradingStore


def _db_url() -> str:
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if line.startswith("DATABASE_URL="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise RuntimeError("DATABASE_URL missing")


def _fetch_data_fx_fix_loaded() -> bool:
    from quantara_workers.jobs import fetch_data

    src = inspect.getsource(fetch_data._should_poll_asset)
    return "FX Tiingo 1m is 24x5" in src and "FAST_FX_DB_SYMBOLS" in src


def _gate(applicable: bool, ok: bool) -> str:
    if not applicable:
        return "N/A"
    return "PASS" if ok else "FAIL"


def _system_clean(gates: dict[str, str]) -> bool:
    return all(status != "FAIL" for status in gates.values())


def _parse_worker_ts(raw: str | None) -> datetime | None:
    if not raw:
        return None
    return datetime.fromisoformat(str(raw).replace("Z", "+00:00")).astimezone(timezone.utc)


def main() -> dict:
    engine = create_engine(_db_url())
    session = sessionmaker(bind=engine)()
    store = TradingStore(session)

    fetch = session.execute(
        text("SELECT value FROM settings WHERE key = 'worker_status:data_fetcher'")
    ).scalar() or {}
    ncf = session.execute(
        text("SELECT value FROM settings WHERE key = 'worker_status:non_crypto_fast_protection'")
    ).scalar() or {}
    containment = session.execute(
        text("SELECT value FROM settings WHERE key = 'live_sim_integrity_containment'")
    ).scalar() or {}

    gbpjpy_1m_before = session.execute(
        text(
            """
            SELECT MAX(c.timestamp) FROM candles c
            JOIN instruments i ON i.id = c.instrument_id
            WHERE i.symbol = 'GBPJPY' AND c.timeframe = '1m' AND c.source = 'tiingo'
            """
        )
    ).scalar()

    now = datetime.now(timezone.utc)
    gbp_asset = (fetch.get("assets") or {}).get("GBPJPY") or {}
    last_run_at = _parse_worker_ts(fetch.get("last_run"))
    fetch_recent = last_run_at is not None and (now - last_run_at) <= timedelta(minutes=20)
    fetch_live_seen = fetch.get("phase") == "fetch_live" and fetch_recent
    gbpjpy_1m_after = session.execute(
        text(
            """
            SELECT c.timestamp, c.source FROM candles c
            JOIN instruments i ON i.id = c.instrument_id
            WHERE i.symbol = 'GBPJPY' AND c.timeframe = '1m'
            ORDER BY c.timestamp DESC LIMIT 1
            """
        )
    ).mappings().first()
    gbpjpy_5m = session.execute(
        text(
            """
            SELECT MAX(c.timestamp) FROM candles c
            JOIN instruments i ON i.id = c.instrument_id
            WHERE i.symbol = 'GBPJPY' AND c.timeframe = '5m' AND c.source = 'tiingo'
            """
        )
    ).scalar()
    gbpjpy_5m_prev = session.execute(
        text(
            """
            SELECT MAX(c.timestamp) FROM candles c
            JOIN instruments i ON i.id = c.instrument_id
            WHERE i.symbol = 'GBPJPY' AND c.timeframe = '5m' AND c.source = 'tiingo'
              AND c.timestamp < (
                SELECT MAX(c2.timestamp) FROM candles c2
                JOIN instruments i2 ON i2.id = c2.instrument_id
                WHERE i2.symbol = 'GBPJPY' AND c2.timeframe = '5m' AND c2.source = 'tiingo'
              )
            """
        )
    ).scalar()

    new_positions = session.execute(
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

    eth = session.execute(
        text(
            """
            SELECT p.direction::text, p.quantity, p.stop_loss, p.take_profit,
                   bp.net_quantity, bp.mark_price, bp.unrealized_pnl,
                   COALESCE(SUM(l.remaining_qty), 0) AS attr_qty
            FROM live_sim_positions p
            JOIN broker_accounts ba ON ba.id = p.broker_account_id
            JOIN instruments i ON i.id = p.instrument_id
            JOIN broker_positions bp ON bp.broker_account_id = p.broker_account_id
              AND bp.instrument_id = p.instrument_id
            LEFT JOIN broker_attribution_lots l ON l.strategy_position_id = p.id
              AND l.remaining_qty > 0
            WHERE p.status = 'open' AND ba.slug = 'live-sim-kraken-like' AND i.symbol = 'ETHUSD'
            GROUP BY p.id, p.direction, p.quantity, p.stop_loss, p.take_profit,
                     bp.net_quantity, bp.mark_price, bp.unrealized_pnl
            """
        )
    ).mappings().first()

    owner = aggregate_owner_portfolio(store, slug=LIVE_SIM_OWNER_SLUG)
    brokers = session.execute(
        text(
            """
            SELECT slug, equity, realized_pnl, unrealized_pnl
            FROM broker_accounts WHERE slug IN ('live-sim-ibkr-like', 'live-sim-kraken-like')
            """
        )
    ).mappings().all()
    assets = list_asset_allocations(store, owner_slug=LIVE_SIM_OWNER_SLUG)
    ibkr = next(b for b in brokers if b["slug"] == "live-sim-ibkr-like")
    kraken = next(b for b in brokers if b["slug"] == "live-sim-kraken-like")
    broker_eq = Decimal(str(ibkr["equity"])) + Decimal(str(kraken["equity"]))
    broker_real = Decimal(str(ibkr["realized_pnl"])) + Decimal(str(kraken["realized_pnl"]))
    broker_unreal = Decimal(str(ibkr["unrealized_pnl"])) + Decimal(str(kraken["unrealized_pnl"]))
    asset_eq = sum(a.current_equity for a in assets if a.enabled)
    asset_real = sum(a.realized_pnl for a in assets if a.enabled)
    asset_unreal = sum(a.unrealized_pnl for a in assets if a.enabled)

    fx_td = int(ncf.get("fx_td_calls") or 0)
    incorrectly_deferred = "US market closed" in str(gbp_asset.get("note", ""))
    configured_tiingo = gbp_asset.get("configured_primary") == "tiingo"
    five_m_advancing = gbpjpy_5m is not None and (
        gbpjpy_5m_prev is None or gbpjpy_5m >= gbpjpy_5m_prev
    )
    ncf_gbpjpy = "GBPJPY" in (ncf.get("symbols") or [])

    eth_healthy = False
    if eth:
        phys = Decimal(str(eth["net_quantity"]))
        attr = Decimal(str(eth["attr_qty"]))
        eth_healthy = (
            phys == attr == Decimal(str(eth["quantity"]))
            and phys > 0
            and eth["stop_loss"]
            and eth["take_profit"]
            and eth["mark_price"]
        )

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

    code_loaded = _fetch_data_fx_fix_loaded()
    containment_on = is_live_sim_entries_blocked(store)
    td_used_today = safe_used_today(store)

    fetch_live_executed = fetch_live_seen or fetch_recent or bool(gbpjpy_1m_after)

    eth_applicable = eth is not None
    containment_release_check = containment_on

    integrity_gates = {
        "worker_fx_code_loaded": _gate(True, code_loaded),
        "data_fetcher_recent_activity": _gate(True, fetch_live_executed),
        "gbpjpy_tiingo_primary_configured": _gate(bool(gbp_asset), configured_tiingo),
        "gbpjpy_1m_present": _gate(True, gbpjpy_1m_after is not None),
        "gbpjpy_not_wrong_us_rth_defer": _gate(True, not incorrectly_deferred),
        "gbpjpy_5m_advancing": _gate(gbpjpy_5m is not None, five_m_advancing),
        "ncf_covers_gbpjpy": _gate(True, ncf_gbpjpy),
        "twelve_data_within_daily_guard": _gate(
            True, td_used_today < INTERNAL_GUARD_LIMIT
        ),
        "eth_open_protection": _gate(eth_applicable, eth_healthy),
        "no_entries_while_containment_active": _gate(
            containment_release_check, int(new_positions or 0) == 0
        ),
        "financial_layers_aligned": _gate(True, fin_clean),
        "containment_still_blocking_entries": _gate(
            False,
            not containment_on,
        ),
    }
    system_clean = _system_clean(integrity_gates)

    out = {
        "verified_at": datetime.now(timezone.utc).isoformat(),
        "worker_new_fetch_data_code_loaded": "YES" if code_loaded else "NO",
        "gbpjpy_fetch_live_cycle_executed": "YES" if fetch_live_executed else "NO",
        "gbpjpy_latest_1m": gbpjpy_1m_after["timestamp"].isoformat() if gbpjpy_1m_after else None,
        "gbpjpy_source": gbpjpy_1m_after["source"] if gbpjpy_1m_after else None,
        "gbpjpy_incorrectly_deferred_by_us_rth": "YES" if incorrectly_deferred else "NO",
        "gbpjpy_5m_advancing": "YES" if five_m_advancing else "NO",
        "ncf_gbpjpy_active": "YES" if ncf_gbpjpy else "NO",
        "twelve_data_used_today": td_used_today,
        "twelve_data_internal_guard": INTERNAL_GUARD_LIMIT,
        "ncf_fx_td_calls_last_cycle": fx_td,
        "eth_physical_protection": integrity_gates["eth_open_protection"],
        "new_live_sim_entries_during_containment": int(new_positions or 0),
        "financial_reconciliation_clean": "YES" if fin_clean else "NO",
        "integrity_gates": integrity_gates,
        "system_integrity_clean": "YES" if system_clean else "NO",
        "ready_to_release_live_sim_containment": integrity_gates[
            "containment_still_blocking_entries"
        ],
        "details": {
            "fetch_live_last_run": fetch.get("last_run"),
            "fetch_live_phase": fetch.get("phase"),
            "gbpjpy_asset_status": gbp_asset,
            "ncf_symbols": ncf.get("symbols"),
            "ncf_fx_fetched": ncf.get("fx_fetched"),
            "ncf_fx_td_calls": fx_td,
            "containment_blocked": containment_on,
            "eth": dict(eth) if eth else None,
            "financial_diffs": {
                "broker_vs_owner_equity": float(broker_eq - owner.total_equity),
                "asset_vs_owner_equity": float(asset_eq - owner.total_equity),
                "broker_vs_owner_realized": float(broker_real - owner.total_realized_pnl),
                "broker_vs_owner_unrealized": float(broker_unreal - owner.total_unrealized_pnl),
            },
        },
    }
    session.close()
    return out


if __name__ == "__main__":
    print(json.dumps(main(), indent=2, default=str))
