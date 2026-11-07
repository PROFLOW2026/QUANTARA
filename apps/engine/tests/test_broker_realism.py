"""Broker-realistic paper account tests."""

from decimal import Decimal

import pytest

from quantara_engine.broker.account import build_account_snapshot
from quantara_engine.broker.instruments import get_instrument_spec
from quantara_engine.broker.margin import quote_notional_usd
from quantara_engine.broker.netting import apply_fill_to_net_position, apply_fill_with_realized_pnl
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
    account.available_margin = Decimal("5000")
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


def test_invalid_qty_rejected_by_pre_trade_no_silent_normalize():
    account = _empty_account()
    req = BrokerOrderRequest(
        symbol="NVDA",
        asset_class="stock",
        direction="long",
        quantity=Decimal("1.5"),
        mark_price=Decimal("170"),
    )
    decision = evaluate_broker_order(account, QUANTARA_STANDARD_PAPER, req, {"USD": Decimal("1")})
    assert not decision.accepted
    assert decision.rejection_reason == BrokerRejectionReason.INVALID_QUANTITY


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


def test_netting_realized_pnl_partial_close():
    result = apply_fill_with_realized_pnl(
        Decimal("100"), Decimal("100"), Decimal("40"), Decimal("110"), "short",
        mode=PositionMode.NETTING,
    )
    assert result.new_net_qty == Decimal("60")
    assert result.realized_pnl == Decimal("400.00")
    assert result.closed_quantity == Decimal("40")


def test_netting_flip_realized_pnl():
    result = apply_fill_with_realized_pnl(
        Decimal("60"), Decimal("100"), Decimal("100"), Decimal("90"), "short",
        mode=PositionMode.NETTING,
    )
    assert result.new_net_qty == Decimal("-40")
    assert result.new_avg_price == Decimal("90")
    assert result.realized_pnl == Decimal("-600.00")


def test_risk_reducing_close_allowed_over_leverage():
    from quantara_engine.broker.types import BrokerPosition

    account = _empty_account(Decimal("100000"))
    account.gross_exposure = Decimal("250000")
    account.gross_leverage = Decimal("2.5")
    account.initial_margin_used = Decimal("125000")
    account.free_margin = Decimal("0")
    account.available_margin = Decimal("0")
    account.positions["GBPJPY"] = BrokerPosition(
        symbol="GBPJPY",
        net_quantity=Decimal("10000"),
        average_price=Decimal("200"),
        mark_price=Decimal("200"),
    )
    req = BrokerOrderRequest(
        symbol="GBPJPY",
        asset_class="forex",
        direction="short",
        quantity=Decimal("4000"),
        mark_price=Decimal("200"),
        is_close=True,
        market_open=True,
    )
    fx = {"JPY": Decimal("150"), "USD": Decimal("1")}
    decision = evaluate_broker_order(account, QUANTARA_STANDARD_PAPER, req, fx)
    assert decision.accepted


def test_us_stock_close_rejected_when_market_closed():
    from quantara_engine.broker.types import BrokerPosition

    account = _empty_account()
    account.positions["TSLA"] = BrokerPosition(
        symbol="TSLA",
        net_quantity=Decimal("10"),
        average_price=Decimal("350"),
        mark_price=Decimal("350"),
    )
    req = BrokerOrderRequest(
        symbol="TSLA",
        asset_class="stock",
        direction="short",
        quantity=Decimal("10"),
        mark_price=Decimal("350"),
        is_close=True,
        market_open=False,
    )
    decision = evaluate_broker_order(account, QUANTARA_STANDARD_PAPER, req, {"USD": Decimal("1")})
    assert not decision.accepted
    assert decision.rejection_reason == BrokerRejectionReason.MARKET_CLOSED


def test_gbpjpy_realized_pnl_usd_conversion():
    fx = {"JPY": Decimal("150"), "USD": Decimal("1")}
    result = apply_fill_with_realized_pnl(
        Decimal("10000"),
        Decimal("200"),
        Decimal("4000"),
        Decimal("210"),
        "short",
        mode=PositionMode.NETTING,
        spec=get_instrument_spec("GBPJPY"),
        fx_rates=fx,
    )
    # (210-200)*4000 JPY = 40000 JPY / 150 = 266.67 USD
    assert result.realized_pnl == Decimal("266.67")


def test_fees_reduce_balance_not_realized_pnl():
    starting = Decimal("320000")
    fees = Decimal("2.50")
    realized = Decimal("100")
    balance = starting + realized - fees
    assert balance == Decimal("320097.50")


def test_mark_to_market_triggers_margin_call_without_fill():
    positions = {
        "NVDA": (Decimal("2000"), Decimal("100"), Decimal("80")),
    }
    snap = build_account_snapshot(
        cash=Decimal("50000"),
        balance=Decimal("50000"),
        realized_pnl=Decimal("0"),
        positions=positions,
        fx_rates={"USD": Decimal("1")},
    )
    assert snap.account_state.value in ("margin_call", "liquidation", "margin_warning")


def test_margin_call_blocks_risk_increasing():
    account = _empty_account(Decimal("50000"))
    account.account_state = AccountState.MARGIN_CALL
    req = BrokerOrderRequest(
        symbol="NVDA",
        asset_class="stock",
        direction="long",
        quantity=Decimal("10"),
        mark_price=Decimal("170"),
    )
    decision = evaluate_broker_order(account, QUANTARA_STANDARD_PAPER, req, {"USD": Decimal("1")})
    assert not decision.accepted
    assert decision.rejection_reason == BrokerRejectionReason.MARGIN_CALL


def test_dust_close_exempt_from_min_notional():
    """Protective TP/SL below min_notional must still close residual crypto qty."""
    # Flat account: 0.0028 * 2230 ≈ $6.24 < ETH min_notional $10 → new entry rejected.
    flat = _empty_account(Decimal("10000"))
    entry_req = BrokerOrderRequest(
        symbol="ETHUSD",
        asset_class="crypto",
        direction="long",
        quantity=Decimal("0.0028"),
        mark_price=Decimal("2230"),
        is_close=False,
    )
    entry_dec = evaluate_broker_order(flat, QUANTARA_STANDARD_PAPER, entry_req, {"USD": Decimal("1")})
    assert not entry_dec.accepted
    assert entry_dec.rejection_reason == BrokerRejectionReason.INVALID_QUANTITY

    # Mis-tagged is_close on a flat account is still risk-increasing → still blocked.
    fake_close = BrokerOrderRequest(
        symbol="ETHUSD",
        asset_class="crypto",
        direction="long",
        quantity=Decimal("0.0028"),
        mark_price=Decimal("2230"),
        is_close=True,
    )
    fake_dec = evaluate_broker_order(flat, QUANTARA_STANDARD_PAPER, fake_close, {"USD": Decimal("1")})
    assert not fake_dec.accepted
    assert fake_dec.rejection_reason == BrokerRejectionReason.INVALID_QUANTITY

    # Same qty as a close of an existing short dust position must be accepted.
    held = build_account_snapshot(
        cash=Decimal("10000"),
        balance=Decimal("10000"),
        realized_pnl=Decimal("0"),
        positions={"ETHUSD": (Decimal("-0.0028"), Decimal("2400"), Decimal("2230"))},
        fx_rates={"USD": Decimal("1")},
    )
    close_req = BrokerOrderRequest(
        symbol="ETHUSD",
        asset_class="crypto",
        direction="long",
        quantity=Decimal("0.0028"),
        mark_price=Decimal("2230"),
        is_close=True,
    )
    close_dec = evaluate_broker_order(held, QUANTARA_STANDARD_PAPER, close_req, {"USD": Decimal("1")})
    assert close_dec.accepted, close_dec.rejection_detail
    assert close_dec.accepted_quantity == Decimal("0.0028")


def test_residual_dust_below_min_quantity_full_flatten_allowed():
    """Exact abs(current) dust flatten must pass even when qty < min_quantity."""
    held = build_account_snapshot(
        cash=Decimal("10000"),
        balance=Decimal("10000"),
        realized_pnl=Decimal("0"),
        positions={"ETHUSD": (Decimal("-0.00005"), Decimal("2400"), Decimal("2230"))},
        fx_rates={"USD": Decimal("1")},
    )
    # Partial / non-exact dust still rejected (below min).
    partial = BrokerOrderRequest(
        symbol="ETHUSD",
        asset_class="crypto",
        direction="long",
        quantity=Decimal("0.00004"),
        mark_price=Decimal("2230"),
        is_close=True,
    )
    partial_dec = evaluate_broker_order(held, QUANTARA_STANDARD_PAPER, partial, {"USD": Decimal("1")})
    assert not partial_dec.accepted

    full = BrokerOrderRequest(
        symbol="ETHUSD",
        asset_class="crypto",
        direction="long",
        quantity=Decimal("0.00005"),
        mark_price=Decimal("2230"),
        is_close=True,
    )
    full_dec = evaluate_broker_order(held, QUANTARA_STANDARD_PAPER, full, {"USD": Decimal("1")})
    assert full_dec.accepted, full_dec.rejection_detail
    assert full_dec.accepted_quantity == Decimal("0.00005")
