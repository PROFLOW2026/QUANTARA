"""Kraken crypto shorts keep derivative margin after the fill.

The 2026-10-24 18:00 Israel BTCUSD short (volatility-squeeze, 0.1333 BTC)
was accepted as crypto_derivative and then force-closed because the account
snapshot priced it as spot crypto at 100% margin.
"""

from decimal import Decimal

from quantara_engine.broker.account import build_account_snapshot
from quantara_engine.broker.execution_model import ExecutionModelVersion
from quantara_engine.broker.execution_product import (
    route_execution_product,
    rules_for_booked_product,
)
from quantara_engine.broker.instruments import get_instrument_spec
from quantara_engine.broker.margin import (
    initial_margin_for_notional,
    maintenance_margin_for_notional,
    quote_notional_usd,
)
from quantara_engine.broker.pre_trade import evaluate_broker_order
from quantara_engine.broker.profile import QUANTARA_LIVE_SIM_10K
from quantara_engine.broker.types import AccountState, BrokerOrderRequest, BrokerRejectionReason

# Fill of allocation c3393fa7, candle 2026-10-24 18:00 Asia/Jerusalem.
# Kraken equity just before that round trip: 2198.83 - 62.94 realized on the cover.
BTC_QTY = Decimal("0.1333")
BTC_FILL = Decimal("80083.940764")
KRAKEN_EQUITY_BEFORE = Decimal("2135.89")
FX = {"USD": Decimal("1")}


def _route():
    return route_execution_product(
        "BTCUSD",
        "short",
        execution_model=ExecutionModelVersion.REALISTIC_BROKER_V1,
    )


def _book(mark: Decimal = BTC_FILL, *, product: str | None, equity: Decimal = KRAKEN_EQUITY_BEFORE):
    products = {"BTCUSD": product} if product else None
    return build_account_snapshot(
        cash=equity,
        balance=equity,
        realized_pnl=Decimal("0"),
        positions={"BTCUSD": (Decimal("-1") * BTC_QTY, BTC_FILL, mark)},
        fx_rates=FX,
        profile=QUANTARA_LIVE_SIM_10K,
        execution_products=products,
    )


def test_observed_btc_short_is_a_derivative_and_priced_in_usd():
    route = _route()
    spec = get_instrument_spec("BTCUSD")
    notional = quote_notional_usd(BTC_QTY, BTC_FILL, spec, FX)
    assert route.product.value == "crypto_derivative"
    assert route.rules.initial_margin_pct == Decimal("10")
    assert route.rules.maintenance_margin_pct == Decimal("5")
    assert notional == Decimal("10675.19")
    assert spec.contract_size == Decimal("1")
    assert spec.quote_currency == "USD"


def test_valid_btc_derivative_short_is_not_liquidated_at_entry():
    route = _route()
    spec = get_instrument_spec("BTCUSD")
    notional = quote_notional_usd(BTC_QTY, BTC_FILL, spec, FX)
    flat = build_account_snapshot(
        cash=KRAKEN_EQUITY_BEFORE,
        balance=KRAKEN_EQUITY_BEFORE,
        realized_pnl=Decimal("0"),
        positions={},
        fx_rates=FX,
        profile=QUANTARA_LIVE_SIM_10K,
    )
    decision = evaluate_broker_order(
        flat,
        QUANTARA_LIVE_SIM_10K,
        BrokerOrderRequest(
            symbol="BTCUSD",
            asset_class="crypto",
            direction="short",
            quantity=BTC_QTY,
            mark_price=BTC_FILL,
            execution_product=route.product.value,
            product_rules_key=route.asset_class_key,
        ),
        FX,
        product_rules=route.rules,
    )
    assert decision.accepted
    assert decision.accepted_quantity == BTC_QTY

    booked = _book(product="crypto_derivative")
    initial = initial_margin_for_notional(notional, route.rules)
    maintenance = maintenance_margin_for_notional(notional, route.rules)
    assert booked.account_state == AccountState.ACTIVE
    assert booked.gross_exposure == notional
    assert booked.initial_margin_used == initial == Decimal("1067.52")
    assert booked.maintenance_margin_required == maintenance == Decimal("533.76")
    # One charge at the derivative rate. Spot 100% would be the full notional.
    assert booked.initial_margin_used + booked.maintenance_margin_required == Decimal("1601.28")
    assert booked.initial_margin_used != notional
    assert booked.maintenance_margin_required != notional
    assert booked.gross_leverage < QUANTARA_LIVE_SIM_10K.max_gross_leverage
    assert booked.margin_level_pct > QUANTARA_LIVE_SIM_10K.margin_warning_level_pct


def test_same_position_without_product_still_liquidates_as_spot():
    """Guards against turning liquidation off for every crypto position."""
    spot = _book(product=None)
    assert spot.maintenance_margin_required == spot.gross_exposure
    assert spot.account_state == AccountState.LIQUIDATION


def test_five_percent_adverse_move_does_not_liquidate_max_size_short():
    mark = (BTC_FILL * Decimal("1.05")).quantize(Decimal("0.00000001"))
    booked = _book(mark, product="crypto_derivative")
    assert booked.unrealized_pnl < 0
    assert booked.account_state == AccountState.ACTIVE
    assert booked.margin_level_pct > QUANTARA_LIVE_SIM_10K.liquidation_level_pct


def test_large_adverse_move_still_liquidates_derivative():
    mark = (BTC_FILL * Decimal("1.18")).quantize(Decimal("0.00000001"))
    booked = _book(mark, product="crypto_derivative")
    assert booked.account_state == AccountState.LIQUIDATION
    assert booked.maintenance_margin_required < booked.gross_exposure
    assert booked.margin_level_pct <= QUANTARA_LIVE_SIM_10K.liquidation_level_pct


def test_insufficient_derivative_margin_is_rejected():
    """A new BTC short is rejected when its own 10% margin exceeds free margin.

    The existing stock position already holds most of the initial margin.
    The crypto charge is the derivative increment only, not the full notional
    and not a second copy of the stock margin.
    """
    route = _route()
    account = build_account_snapshot(
        cash=Decimal("10000"),
        balance=Decimal("10000"),
        realized_pnl=Decimal("0"),
        positions={"NVDA": (Decimal("90"), Decimal("200"), Decimal("200"))},
        fx_rates=FX,
        profile=QUANTARA_LIVE_SIM_10K,
    )
    assert account.account_state == AccountState.ACTIVE
    assert account.free_margin == Decimal("1000.00")
    decision = evaluate_broker_order(
        account,
        QUANTARA_LIVE_SIM_10K,
        BrokerOrderRequest(
            symbol="BTCUSD",
            asset_class="crypto",
            direction="short",
            quantity=Decimal("0.2"),
            mark_price=BTC_FILL,
            execution_product=route.product.value,
        ),
        FX,
        product_rules=route.rules,
    )
    assert not decision.accepted
    assert decision.rejection_reason == BrokerRejectionReason.INSUFFICIENT_MARGIN
    assert decision.diagnostics["required_initial_margin"] == Decimal("1601.68")
    assert decision.diagnostics["required_initial_margin"] < Decimal("2000")


def test_mark_to_market_uses_booked_derivative_rules():
    spec = get_instrument_spec("BTCUSD")
    rules = rules_for_booked_product(QUANTARA_LIVE_SIM_10K, spec, "crypto_derivative")
    spot_rules = rules_for_booked_product(QUANTARA_LIVE_SIM_10K, spec, None)
    notional = quote_notional_usd(BTC_QTY, BTC_FILL, spec, FX)
    assert rules.maintenance_margin_pct == Decimal("5")
    assert maintenance_margin_for_notional(notional, rules) == Decimal("533.76")
    assert maintenance_margin_for_notional(notional, spot_rules) == notional
