"""Owner flatten — close all open paper positions at legal executable prices."""

from __future__ import annotations

import logging
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from quantara_engine.domain.types import (
    DecisionLogEntry,
    DecisionType,
    ExecutionAssumptions,
    ExitReason,
    new_id,
)
from quantara_engine.execution.paper_broker import PaperBrokerAdapter
from quantara_engine.market_data.polling import is_bar_complete
from quantara_engine.market_data.registry import get_asset
from quantara_engine.market_data.sessions import session_allows_entries
from quantara_engine.trading.trading_controls import (
    TradingControlState,
    load_trading_control,
    save_trading_control,
)

if TYPE_CHECKING:
    from quantara_engine.domain.types import Instrument, Position, StrategyInstance
    from quantara_engine.persistence.store import TradingStore
    from quantara_engine.portfolio.service import PortfolioState

logger = logging.getLogger(__name__)


def _latest_tradable_candle(
    store: TradingStore,
    instrument_id: str,
    timeframe: str,
    now: datetime,
):
    candles = store.list_recent_candles(instrument_id, timeframe, limit=5)
    for candle in reversed(candles):
        if is_bar_complete(candle.timestamp, timeframe, now):
            return candle
    return None


def _asset_tradable(instrument: Instrument, ts: datetime) -> bool:
    asset = get_asset(instrument.symbol)
    if not asset:
        return True
    return session_allows_entries(asset.trading_sessions, ts)


def process_flatten_cycle(
    store: TradingStore,
    now: datetime,
    *,
    portfolio_states: dict[str, PortfolioState],
    open_by_portfolio: dict[str, list],
    instance_by_id: dict[str, StrategyInstance],
    instrument_cache: dict[str, Instrument | None],
    pending_exits: list[dict[str, Any]],
    currency=None,
) -> dict[str, Any]:
    """
    Attempt owner flatten closes for open positions when market is tradable.
    Positions awaiting reopen are tracked in trading_control_state.pending_market_reopen.
    """
    settings = store.get_settings_dict()
    control = load_trading_control(settings)
    if control.state != TradingControlState.FLATTENING:
        return {"flatten_attempted": 0, "flatten_closed": 0, "awaiting_reopen": []}

    from quantara_engine.portfolio.currency import CurrencyContext, build_currency_context

    attempted = 0
    closed = 0
    awaiting: set[str] = set()
    def _context_for(instrument: Instrument):
        if currency is not None:
            return currency
        if isinstance(getattr(instrument, "quote_currency", None), str):
            return build_currency_context(store, [instrument])
        return CurrencyContext.usd_only({instrument.id: instrument})

    for pid, positions in open_by_portfolio.items():
        state = portfolio_states.get(pid)
        if state is None:
            continue
        for position in list(positions):
            instance = instance_by_id.get(position.strategy_instance_id)
            if not instance:
                continue
            instrument = instrument_cache.get(position.instrument_id)
            if not instrument:
                continue

            open_pos = next((p for p in state.open_positions() if p.id == position.id), None)
            if open_pos is None:
                continue

            if not _asset_tradable(instrument, now):
                awaiting.add(instrument.symbol)
                continue

            candle = _latest_tradable_candle(store, instrument.id, instance.timeframe, now)
            if candle is None:
                awaiting.add(instrument.symbol)
                continue

            attempted += 1
            from quantara_engine.execution.cost_profile import execution_assumptions_for

            broker = PaperBrokerAdapter(instrument.id, execution_assumptions_for(instrument))
            order, fill = broker.execute_exit_at_trigger(
                open_pos.direction,
                open_pos.quantity,
                candle,
                candle.open,
                state.portfolio.id,
                gap_exit=True,
            )
            trade = state.close_position(
                open_pos, fill, ExitReason.MANUAL, candle.timestamp, _context_for(instrument)
            )
            decision = DecisionLogEntry(
                id=new_id(),
                strategy_instance_id=instance.id,
                instrument_id=instrument.id,
                candle_timestamp=candle.timestamp,
                decision_type=DecisionType.TRADING_HALTED,
                message="Owner flatten — market close at tradable price",
                signal_id=None,
                metadata={"flatten": True, "fill_price": float(fill.fill_price)},
            )
            pending_exits.append(
                {
                    "order": order,
                    "fill": fill,
                    "position": open_pos,
                    "trade": trade,
                    "portfolio_state": state,
                    "strategy_instance_id": instance.id,
                    "filled_at": candle.timestamp,
                    "decision": decision,
                }
            )
            closed += 1
            logger.info(
                "Flatten closed %s %s at %s",
                instrument.symbol,
                open_pos.id,
                fill.fill_price,
            )

    control.pending_market_reopen = sorted(awaiting)
    open_remaining = store.count_open_competition_positions()
    if open_remaining == 0:
        control.state = TradingControlState.STOPPED
        logger.info("Flatten complete — transitioning to STOPPED")
    save_trading_control(store, control)

    return {
        "flatten_attempted": attempted,
        "flatten_closed": closed,
        "awaiting_reopen": sorted(awaiting),
        "open_positions_remaining": open_remaining,
        "trading_control_state": control.state.value,
    }
