"""Targeted market-data prep for dynamic rotation candidates (not full-universe fetch)."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from quantara_engine.market_data.polling import STRATEGY_MIN_CANDLES
from quantara_engine.market_data.registry import get_asset
from quantara_engine.persistence.store import TradingStore

logger = logging.getLogger(__name__)

MAX_PREP_SYMBOLS_PER_CYCLE = 5


def symbol_has_strategy_history(store: TradingStore, symbol: str, *, min_5m: int = STRATEGY_MIN_CANDLES) -> bool:
    sym = symbol.upper().replace("/", "")
    inst = store.get_instrument_by_symbol(sym)
    if not inst:
        return False
    return store.count_candles(inst.id, "5m") >= min_5m


def ensure_rotation_candidate_data(
    store: TradingStore,
    symbols: list[str],
    *,
    now: datetime | None = None,
) -> dict[str, dict]:
    """
    Bootstrap/fetch live 5m for a small candidate set missing strategy history.
    Does not bypass freshness rules for live entry — only fills candle gaps.
    """
    from quantara_engine.market_data.registry import list_target_assets
    from quantara_workers.jobs.fetch_data import _bootstrap_asset_isolated, _fetch_live_asset_isolated

    now = now or datetime.now(timezone.utc)
    assets_by_sym = {a.db_symbol: a for a in list_target_assets()}
    report: dict[str, dict] = {}

    for raw in symbols[:MAX_PREP_SYMBOLS_PER_CYCLE]:
        sym = raw.upper().replace("/", "")
        if symbol_has_strategy_history(store, sym):
            report[sym] = {"status": "already_ready", "candles_5m": store.count_candles(
                store.get_instrument_by_symbol(sym).id, "5m"
            )}
            continue
        asset = assets_by_sym.get(sym) or get_asset(sym)
        if not asset:
            report[sym] = {"status": "unknown_asset"}
            continue
        try:
            boot_n, boot_d, boot_err = _bootstrap_asset_isolated(asset)
            count, derived, err, status = _fetch_live_asset_isolated(asset, now, {})
            inst = store.get_instrument_by_symbol(sym)
            stored = store.count_candles(inst.id, "5m") if inst else 0
            report[sym] = {
                "status": status.get("status") if isinstance(status, dict) else "fetched",
                "bootstrap_5m": boot_n,
                "fetch_5m": count,
                "derived_5m": derived + boot_d,
                "stored_5m": stored,
                "error": err or boot_err,
            }
        except Exception as exc:
            logger.exception("candidate prep failed for %s", sym)
            report[sym] = {"status": "error", "error": str(exc)}

    return report
