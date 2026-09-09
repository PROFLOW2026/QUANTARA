"""ORB status payloads for API/UI."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from quantara_engine.competition.orb_constants import ORB_ASSETS, ORB_STRATEGY_SLUG
from quantara_engine.persistence.store import TradingStore
from quantara_engine.strategies.opening_range_breakout.session import (
    compute_opening_range,
    is_market_closed,
    rth_session_date,
)
from quantara_engine.market_data.sessions import is_us_equity_rth


def build_orb_status(store: TradingStore, symbol: str | None = None) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    symbols = [symbol] if symbol else list(ORB_ASSETS)
    assets: dict[str, Any] = {}

    for sym in symbols:
        inst = store.get_instrument_by_symbol(sym)
        if not inst:
            continue
        candles = store.list_recent_candles(inst.id, "5m", limit=500)
        session_date = rth_session_date(now)
        opening = compute_opening_range(candles, session_date) if session_date else None
        latest_dec = store.latest_decision_for_instrument(inst.id, strategy_slug=ORB_STRATEGY_SLUG)
        trades_today = 0
        entries = store.list_orb_competition_entries()
        sym_entries = [e for e in entries if e["instance"].instrument_id == inst.id]
        if sym_entries and session_date:
            trades_today = store.count_trades_on_session_date(
                sym_entries[0]["portfolio"].id, inst.id, session_date
            )
        relation = None
        if opening and candles:
            last_close = float(candles[-1].close)
            if last_close > float(opening.high):
                relation = "above_range"
            elif last_close < float(opening.low):
                relation = "below_range"
            else:
                relation = "inside_range"

        assets[sym] = {
            "market_open": is_us_equity_rth(now),
            "market_closed": is_market_closed(now),
            "opening_range_high": float(opening.high) if opening else None,
            "opening_range_low": float(opening.low) if opening else None,
            "opening_range_size": float(opening.size) if opening else None,
            "range_ready": opening is not None,
            "current_relation": relation,
            "trades_today": trades_today,
            "latest_signal": latest_dec.decision_type.value if latest_dec else None,
            "latest_reason": latest_dec.message if latest_dec else None,
            "latest_signal_at": latest_dec.created_at.isoformat() if latest_dec else None,
        }

    enabled = store.is_orb_competition_enabled()
    return {
        "strategy_id": ORB_STRATEGY_SLUG,
        "version": "1.0.0",
        "display_name": "Opening Range Breakout",
        "paper_enabled": enabled,
        "portfolios_count": len(store.list_orb_competition_entries()),
        "now_utc": now.isoformat(),
        "assets": assets,
    }
