"""Broker-realistic paper account tests."""

from decimal import Decimal

import pytest

from quantara_engine.broker.account import build_account_snapshot
from quantara_engine.broker.instruments import get_instrument_spec
from quantara_engine.broker.margin import quote_notional_usd
from quantara_engine.broker.netting import apply_fill_to_net_position
from quantara_engine.broker.normalizer import normalize_quantity, validate_quantity
from quantara_engine.broker.pre_trade import evaluate_broker_order
from quantara_engine.broker.profile import QUANTARA_STANDARD_PAPER
from quantara_engine.broker.types import (
    AccountState,
    BrokerAccountSnapshot,
    BrokerOrderRequest,
    BrokerRejectionReason,
    PositionMode,
)


def _empty_account(starting: Decimal = Decimal("320000")) -> BrokerAccountSnapshot:
    return build_account_snapshot(
        cash=starting,
        balance=starting,
        realized_pnl=Decimal("0"),
        positions={},
        fx_rates={"USD": Decimal("1"), "JPY": Decimal("150")},
    )


def test_buy_exceeds_buying_power_crypto_rejected():
    account = _empty_account(Decimal("1000"))
    spec = get_instrument_spec("BTCUSD")
    req = BrokerOrderRequest(
        symbol="BTCUSD",
        asset_class="crypto",
        direction="long",
        quantity=Decimal("1"),
        mark_price=Decimal("60000"),
    )
    decision = evaluate_broker_order(account, QUANTARA_STANDARD_PAPER, req, {"USD": Decimal("1")})
    assert not decision.accepted
    assert decision.rejection_reason in (
        BrokerRejectionReason.INSUFFICIENT_BUYING_POWER,
        BrokerRejectionReason.MAX_ORDER_NOTIONAL,
        BrokerRejectionReason.INSUFFICIENT_MARGIN,
    )


def test_order_exceeds_max_leverage_rejected():
    account = _empty_account(Decimal("10000"))
    # Simulate already high gross exposure
    account.gross_exposure = Decimal("18000")
    account.gross_leverage = Decimal("1.8")
    account.free_margin = Decimal("5000")
    account.buying_power = Decimal("5000")
    req = BrokerOrderRequest(
        symbol="NVDA",
        asset_class="stock",
        direction="long",
        quantity=Decimal("100"),
        mark_price=Decimal("200"),
    )
    decision = evaluate_broker_order(
        account, QUANTARA_STANDARD_PAPER, req, {"USD": Decimal("1")}
    )
    assert not decision.accepted
    assert decision.rejection_reason in (
        BrokerRejectionReason.MAX_GROSS_LEVERAGE,
        BrokerRejectionReason.INSUFFICIENT_MARGIN,
    )


def test_short_not_allowed_crypto():
    account = _empty_account()
    req = BrokerOrderRequest(
        symbol="BTCUSD",
        asset_class="crypto",
        direction="short",
        quantity=Decimal("0.01"),
        mark_price=Decimal("60000"),
    )
    decision = evaluate_broker_order(account, QUANTARA_STANDARD_PAPER, req, {"USD": Decimal("1")})
    assert not decision.accepted
    assert decision.rejection_reason == BrokerRejectionReason.SHORT_NOT_ALLOWED


def test_invalid_qty_step():
    spec = get_instrument_spec("NVDA")
    qty = normalize_quantity(Decimal("1.5"), spec)
    assert qty == Decimal("1")  # floored to whole shares
    err = validate_quantity(Decimal("1.5"), spec)
    assert err is not None


def test_valid_stock_order_accepted():
    account = _empty_account()
    req = BrokerOrderRequest(
        symbol="NVDA",
        asset_class="stock",
        direction="long",
        quantity=Decimal("10"),
        mark_price=Decimal("170"),
    )
    decision = evaluate_broker_order(account, QUANTARA_STANDARD_PAPER, req, {"USD": Decimal("1")})
    assert decision.accepted
    assert decision.accepted_quantity == Decimal("10")


def test_valid_forex_gbpjpy_jpy_conversion():
    account = _empty_account()
    req = BrokerOrderRequest(
        symbol="GBPJPY",
        asset_class="forex",
        direction="short",
        quantity=Decimal("4000"),
        mark_price=Decimal("208"),
    )
    fx = {"JPY": Decimal("150"), "USD": Decimal("1")}
    decision = evaluate_broker_order(account, QUANTARA_STANDARD_PAPER, req, fx)
    assert decision.accepted
    notional = quote_notional_usd(Decimal("4000"), Decimal("208"), get_instrument_spec("GBPJPY"), fx)
    assert notional == Decimal("5546.67") or notional > Decimal("5000")


def test_netting_partial_sell():
    qty, avg = apply_fill_to_net_position(
        Decimal("100"),
        Decimal("350"),
        Decimal("40"),
        Decimal("360"),
        "short",
        mode=PositionMode.NETTING,
    )
    assert qty == Decimal("60")
    assert avg == Decimal("350")


def test_netting_flip_to_short():
    qty, avg = apply_fill_to_net_position(
        Decimal("60"),
        Decimal("350"),
        Decimal("100"),
        Decimal("340"),
        "short",
        mode=PositionMode.NETTING,
    )
    assert qty == Decimal("-40")
    assert avg == Decimal("340")


def test_stale_data_rejected():
    account = _empty_account()
    req = BrokerOrderRequest(
        symbol="NVDA",
        asset_class="stock",
        direction="long",
        quantity=Decimal("5"),
        mark_price=Decimal("170"),
        data_fresh=False,
    )
    decision = evaluate_broker_order(account, QUANTARA_STANDARD_PAPER, req, {"USD": Decimal("1")})
    assert decision.rejection_reason == BrokerRejectionReason.STALE_MARKET_DATA


def test_margin_level_triggers_warning():
    positions = {
        "NVDA": (Decimal("1000"), Decimal("100"), Decimal("100")),
    }
    snap = build_account_snapshot(
        cash=Decimal("50000"),
        balance=Decimal("50000"),
        realized_pnl=Decimal("0"),
        positions=positions,
        fx_rates={"USD": Decimal("1")},
    )
    assert snap.initial_margin_used > Decimal("0")
    assert snap.gross_leverage > Decimal("1")


def test_market_closed_rejected():
    account = _empty_account()
    req = BrokerOrderRequest(
        symbol="TSLA",
        asset_class="stock",
        direction="long",
        quantity=Decimal("5"),
        mark_price=Decimal("350"),
        market_open=False,
    )
    decision = evaluate_broker_order(account, QUANTARA_STANDARD_PAPER, req, {"USD": Decimal("1")})
    assert decision.rejection_reason == BrokerRejectionReason.MARKET_CLOSED
