"""Integration helpers for strategy pipeline → broker (advisory + execution)."""

from __future__ import annotations

from datetime import datetime

from quantara_engine.broker.execution_service import BrokerExecutionService
from quantara_engine.broker.market_gate import data_fresh_for_instrument, market_open_for_instrument
from quantara_engine.broker.pre_trade import BrokerOrderDecision, evaluate_broker_order
from quantara_engine.broker.profile import QUANTARA_STANDARD_PAPER
from quantara_engine.broker.types import BrokerOrderRequest
from quantara_engine.competition.leverage import is_paper_competition_portfolio
from quantara_engine.domain.types import Direction, Instrument, OrderIntent
from quantara_engine.persistence.store import TradingStore
from quantara_engine.portfolio.currency import quote_currencies_for_instruments, resolve_dashboard_fx_rates


def advisory_broker_check(
    store: TradingStore,
    intent: OrderIntent,
    instrument: Instrument,
    mark_price,
    *,
    execution_at: datetime,
    timeframe: str,
    opportunity_key: str | None = None,
) -> BrokerOrderDecision:
    """Non-authoritative preview at signal time — final gate is at execution."""
    service = BrokerExecutionService(store)
    account = service.load_account_snapshot()
    fx = resolve_dashboard_fx_rates(store, quote_currencies_for_instruments([instrument]))
    fx_map = {k: v for k, v in fx.quote_per_usd.items()}
    direction = "long" if intent.direction == Direction.LONG else "short"
    market_open = market_open_for_instrument(instrument, execution_at)
    data_fresh, _ = data_fresh_for_instrument(store, instrument, timeframe, execution_at)
    request = BrokerOrderRequest(
        symbol=instrument.symbol,
        asset_class=str(instrument.asset_class),
        direction=direction,
        quantity=intent.quantity,
        mark_price=mark_price,
        is_close=intent.is_close,
        strategy_portfolio_id=intent.portfolio_id,
        opportunity_key=opportunity_key,
        market_open=market_open,
        data_fresh=data_fresh,
    )
    return evaluate_broker_order(account, QUANTARA_STANDARD_PAPER, request, fx_map)


def should_use_broker_realism(portfolio_id: str) -> bool:
    return is_paper_competition_portfolio(portfolio_id)
