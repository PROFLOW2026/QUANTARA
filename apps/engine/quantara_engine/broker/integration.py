"""Integration helpers for strategy pipeline → broker pre-trade."""

from __future__ import annotations

from decimal import Decimal

from quantara_engine.broker.pre_trade import BrokerOrderDecision, evaluate_broker_order
from quantara_engine.broker.profile import QUANTARA_STANDARD_PAPER
from quantara_engine.broker.state_builder import build_competition_broker_account
from quantara_engine.broker.types import BrokerOrderRequest
from quantara_engine.competition.leverage import is_paper_competition_portfolio
from quantara_engine.domain.types import Direction, Instrument, OrderIntent
from quantara_engine.persistence.store import TradingStore
from quantara_engine.portfolio.currency import resolve_dashboard_fx_rates, quote_currencies_for_instruments


def check_broker_acceptance(
    store: TradingStore,
    intent: OrderIntent,
    instrument: Instrument,
    mark_price: Decimal,
    *,
    market_open: bool = True,
    data_fresh: bool = True,
    opportunity_key: str | None = None,
) -> BrokerOrderDecision:
    """Run canonical broker pre-trade check for a competition intent."""
    account = build_competition_broker_account(store)
    fx = resolve_dashboard_fx_rates(store, quote_currencies_for_instruments([instrument]))
    fx_map = {k: v for k, v in fx.quote_per_usd.items()}

    direction = "long" if intent.direction == Direction.LONG else "short"
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
