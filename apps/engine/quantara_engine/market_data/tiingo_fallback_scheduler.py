"""Quota-aware Tiingo fallback scheduling when primary providers are unavailable."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from quantara_engine.market_data.polling import (
    PROVIDER_TIMEFRAME,
    is_market_data_fresh,
    should_fetch_timeframe,
)
from quantara_engine.market_data.provider_budgets import (
    TIINGO_FX_RESERVE,
    TIINGO_HOURLY_HARD_LIMIT,
    TIINGO_USABLE_CANDLE_BUDGET,
    tiingo_budget_snapshot,
    tiingo_candle_remaining,
)
from quantara_engine.market_data.provider_resolver import (
    is_provider_configured,
    is_provider_eligible,
    provider_chain_for_asset,
)
from quantara_engine.market_data.registry import AssetClass, AssetDefinition, ProviderName, list_target_assets
from quantara_engine.market_data.sessions import session_allows_entries
from quantara_engine.persistence.store import TradingStore

FALLBACK_CURSOR_KEY = "tiingo_fallback_cursor"
FETCH_CYCLE_MINUTES = 5
CRYPTO_BATCH_SYMBOLS = frozenset({"BTCUSD", "ETHUSD"})


@dataclass(frozen=True)
class AssetFetchContext:
    db_symbol: str
    instrument_id: str
    last_candle_ts: datetime | None
    stored: int
    open_positions: int
    session_active: bool
    primary_eligible: bool
    needs_tiingo_fallback: bool
    needs_new_bar: bool
    is_fresh: bool
    staleness_min: float


@dataclass
class TiingoFallbackPlan:
    allowed_symbols: frozenset[str] = frozenset()
    crypto_batch: tuple[str, ...] = ()
    safe_calls: int = 0
    budget_mode: str = "healthy"
    fallback_active: bool = False
    deferred: dict[str, str] = field(default_factory=dict)
    budget: dict[str, Any] = field(default_factory=dict)


def _minutes_remaining_in_hour(now: datetime) -> float:
    start = now.replace(minute=0, second=0, microsecond=0)
    elapsed = (now - start).total_seconds() / 60
    return max(0.0, 60.0 - elapsed)


def _cycles_remaining_in_hour(now: datetime) -> int:
    minutes_left = _minutes_remaining_in_hour(now)
    return max(1, math.ceil(minutes_left / FETCH_CYCLE_MINUTES))


def _primary_eligible(store: TradingStore, asset: AssetDefinition) -> bool:
    from quantara_engine.market_data.credits import FetchPriority

    primary = asset.primary_provider
    return is_provider_eligible(store, primary, priority=FetchPriority.SCHEDULED, purpose="candles")


def _needs_tiingo_fallback(store: TradingStore, asset: AssetDefinition) -> bool:
    if _primary_eligible(store, asset):
        return False
    chain = provider_chain_for_asset(asset)
    if ProviderName.TIINGO not in chain:
        return False
    return is_provider_configured(ProviderName.TIINGO)


def _staleness_minutes(last_ts: datetime | None, now: datetime) -> float:
    if last_ts is None:
        return float("inf")
    if last_ts.tzinfo is None:
        last_ts = last_ts.replace(tzinfo=timezone.utc)
    from quantara_engine.market_data.polling import bar_staleness_minutes

    return bar_staleness_minutes(last_ts, PROVIDER_TIMEFRAME, now)


def _open_positions_by_symbol(store: TradingStore) -> dict[str, int]:
    from sqlalchemy import func, select

    from quantara_engine.competition.constants import ACTIVE_COMPETITION_EXPERIMENT_ID
    from quantara_engine.competition.orb_constants import ORB_COMPETITION_EXPERIMENT_ID
    from quantara_engine.models.enums import PositionStatus
    from quantara_engine.models.instruments import Instrument as OrmInstrument
    from quantara_engine.models.trading import Position as OrmPosition
    from quantara_engine.models.portfolio import StrategyInstance as OrmStrategyInstance

    rows = store.session.execute(
        select(OrmInstrument.symbol, func.count())
        .select_from(OrmPosition)
        .join(OrmInstrument, OrmInstrument.id == OrmPosition.instrument_id)
        .join(OrmStrategyInstance, OrmStrategyInstance.portfolio_id == OrmPosition.portfolio_id)
        .where(
            OrmPosition.status == PositionStatus.OPEN,
            OrmStrategyInstance.experiment_id.in_(
                [ACTIVE_COMPETITION_EXPERIMENT_ID, ORB_COMPETITION_EXPERIMENT_ID]
            ),
        )
        .group_by(OrmInstrument.symbol)
    ).all()
    return {str(sym): int(cnt) for sym, cnt in rows}


def build_asset_fetch_contexts(store: TradingStore, now: datetime) -> list[AssetFetchContext]:
    open_by_symbol = _open_positions_by_symbol(store)
    contexts: list[AssetFetchContext] = []
    for asset in list_target_assets():
        instrument = store.get_instrument_by_symbol(asset.db_symbol)
        if not instrument:
            continue
        last_ts = store.latest_candle_timestamp(instrument.id, PROVIDER_TIMEFRAME)
        stored = store.count_candles(instrument.id, PROVIDER_TIMEFRAME)
        session_active = bool(
            last_ts and session_allows_entries(asset.trading_sessions, last_ts)
        ) or asset.asset_class == AssetClass.CRYPTO
        if asset.asset_class in (AssetClass.STOCK, AssetClass.INDEX):
            from quantara_engine.market_data.sessions import is_us_equity_rth

            session_active = is_us_equity_rth(now)
        contexts.append(
            AssetFetchContext(
                db_symbol=asset.db_symbol,
                instrument_id=str(instrument.id),
                last_candle_ts=last_ts,
                stored=stored,
                open_positions=open_by_symbol.get(asset.db_symbol, 0),
                session_active=session_active,
                primary_eligible=_primary_eligible(store, asset),
                needs_tiingo_fallback=_needs_tiingo_fallback(store, asset),
                needs_new_bar=should_fetch_timeframe(PROVIDER_TIMEFRAME, last_ts, now),
                is_fresh=bool(last_ts and is_market_data_fresh(last_ts, PROVIDER_TIMEFRAME, now)),
                staleness_min=_staleness_minutes(last_ts, now),
            )
        )
    return contexts


def _safe_calls_this_cycle(candle_remaining: int, now: datetime) -> int:
    if candle_remaining <= 0:
        return 0
    cycles_left = _cycles_remaining_in_hour(now)
    spread = max(1, math.ceil(candle_remaining / cycles_left))
    return min(candle_remaining, spread)


def _priority_key(ctx: AssetFetchContext, rotation: int) -> tuple:
    return (
        -ctx.open_positions,
        -int(ctx.session_active),
        -min(ctx.staleness_min, 10_000.0),
        rotation,
    )


def _load_cursor(store: TradingStore) -> int:
    raw = store.get_settings_dict().get(FALLBACK_CURSOR_KEY)
    try:
        return int(raw or 0)
    except (TypeError, ValueError):
        return 0


def _save_cursor(store: TradingStore, cursor: int) -> None:
    store.update_settings(
        FALLBACK_CURSOR_KEY,
        int(cursor),
        description="Tiingo fallback fair-rotation cursor",
    )


def build_tiingo_fallback_plan(store: TradingStore, now: datetime | None = None) -> TiingoFallbackPlan:
    now = now or datetime.now(timezone.utc)
    budget = tiingo_budget_snapshot(store)
    contexts = build_asset_fetch_contexts(store, now)
    fallback_active = any(ctx.needs_tiingo_fallback for ctx in contexts)
    deferred: dict[str, str] = {}

    if not fallback_active:
        return TiingoFallbackPlan(
            budget_mode=budget["mode"],
            fallback_active=False,
            budget=budget,
        )

    candidates: list[AssetFetchContext] = []
    for ctx in contexts:
        if not ctx.needs_tiingo_fallback:
            continue
        if ctx.stored > 0 and not ctx.needs_new_bar and ctx.is_fresh:
            deferred[ctx.db_symbol] = "deferred (candle sufficient for cycle)"
            continue
        candidates.append(ctx)

    candle_remaining = int(budget["candle_remaining"])
    safe_calls = _safe_calls_this_cycle(candle_remaining, now)

    cursor = _load_cursor(store)
    ordered_assets = sorted(list_target_assets(), key=lambda a: a.db_symbol)
    symbol_order = {a.db_symbol: idx for idx, a in enumerate(ordered_assets)}

    ranked = sorted(
        candidates,
        key=lambda ctx: _priority_key(ctx, (symbol_order.get(ctx.db_symbol, 0) + cursor) % max(len(candidates), 1)),
    )

    selected: list[str] = []
    for ctx in ranked:
        if len(selected) >= safe_calls:
            deferred[ctx.db_symbol] = f"deferred (Tiingo budget conservation — {budget['mode']})"
            continue
        selected.append(ctx.db_symbol)

    for ctx in candidates:
        if ctx.db_symbol not in selected and ctx.db_symbol not in deferred:
            deferred[ctx.db_symbol] = f"deferred (Tiingo budget conservation — {budget['mode']})"

    crypto_batch: tuple[str, ...] = ()
    if "BTCUSD" in selected and "ETHUSD" in selected:
        crypto_batch = ("BTCUSD", "ETHUSD")

    if selected:
        _save_cursor(store, (cursor + len(selected)) % max(len(ordered_assets), 1))

    return TiingoFallbackPlan(
        allowed_symbols=frozenset(selected),
        crypto_batch=crypto_batch,
        safe_calls=safe_calls,
        budget_mode=budget["mode"],
        fallback_active=True,
        deferred=deferred,
        budget=budget,
    )


def tiingo_fallback_allowed(plan: TiingoFallbackPlan | None, db_symbol: str) -> bool:
    if plan is None or not plan.fallback_active:
        return True
    return db_symbol in plan.allowed_symbols


def defer_reason_for_asset(plan: TiingoFallbackPlan | None, db_symbol: str) -> str | None:
    if plan is None:
        return None
    return plan.deferred.get(db_symbol)
