"""Canonical pre-trade broker order evaluation."""

from __future__ import annotations

from decimal import Decimal

from quantara_engine.broker.instruments import get_instrument_spec
from quantara_engine.broker.margin import (
    gross_leverage,
    initial_margin_for_notional,
    net_leverage,
    quote_notional_usd,
)
from quantara_engine.broker.normalizer import normalize_quantity, validate_quantity
from quantara_engine.broker.types import (
    AccountState,
    BrokerAccountSnapshot,
    BrokerOrderDecision,
    BrokerOrderRequest,
    BrokerProfile,
    BrokerRejectionReason,
)


def _reject(
    reason: BrokerRejectionReason,
    detail: str,
    *,
    account: BrokerAccountSnapshot,
    qty: Decimal = Decimal("0"),
    **extra,
) -> BrokerOrderDecision:
    return BrokerOrderDecision(
        accepted=False,
        accepted_quantity=qty,
        rejection_reason=reason,
        rejection_detail=detail,
        buying_power_before=account.buying_power,
        buying_power_after=account.buying_power,
        gross_leverage_before=account.gross_leverage,
        gross_leverage_after=account.gross_leverage,
        asset_exposure_before=extra.get("asset_exposure_before", Decimal("0")),
        asset_exposure_after=extra.get("asset_exposure_before", Decimal("0")),
        diagnostics=extra,
    )


def evaluate_broker_order(
    account: BrokerAccountSnapshot,
    profile: BrokerProfile,
    request: BrokerOrderRequest,
    fx_rates: dict[str, Decimal],
) -> BrokerOrderDecision:
    """
    Determine whether a broker would accept an order.

    Strategy risk must already have passed. This layer enforces account reality.
    """
    if account.account_state in (
        AccountState.PAUSED,
        AccountState.LIQUIDATION,
        AccountState.MARGIN_CALL,
    ):
        return _reject(
            BrokerRejectionReason.ACCOUNT_PAUSED,
            f"account state={account.account_state.value}",
            account=account,
        )

    if not request.data_fresh:
        return _reject(
            BrokerRejectionReason.STALE_MARKET_DATA,
            "market data stale",
            account=account,
        )

    if not request.is_close and not request.market_open:
        return _reject(
            BrokerRejectionReason.MARKET_CLOSED,
            "market closed for new entries",
            account=account,
        )

    spec = get_instrument_spec(request.symbol)
    rules = profile.rules_for(spec.asset_class)

    qty = normalize_quantity(request.quantity, spec)
    if qty <= 0:
        return _reject(
            BrokerRejectionReason.INVALID_QUANTITY,
            "quantity below minimum or invalid step after normalization",
            account=account,
        )

    qty_err = validate_quantity(qty, spec)
    if qty_err:
        return _reject(
            BrokerRejectionReason.INVALID_QUANTITY,
            qty_err,
            account=account,
        )

    direction = request.direction.lower()
    if direction == "short" and not rules.shorting_allowed:
        return _reject(
            BrokerRejectionReason.SHORT_NOT_ALLOWED,
            f"shorting not allowed for {spec.asset_class}",
            account=account,
        )

    order_notional = quote_notional_usd(qty, request.mark_price, spec, fx_rates)
    if order_notional < spec.min_notional and spec.quote_currency != "JPY":
        return _reject(
            BrokerRejectionReason.INVALID_QUANTITY,
            f"notional {order_notional} below min {spec.min_notional}",
            account=account,
        )

    if rules.max_order_notional and order_notional > rules.max_order_notional:
        return _reject(
            BrokerRejectionReason.MAX_ORDER_NOTIONAL,
            f"order notional {order_notional} > max {rules.max_order_notional}",
            account=account,
        )

    pos = account.positions.get(request.symbol)
    current_qty = pos.net_quantity if pos else Decimal("0")
    current_asset_notional = (
        quote_notional_usd(current_qty, request.mark_price, spec, fx_rates) if current_qty else Decimal("0")
    )

    # Project post-trade exposure
    signed = qty if direction == "long" else -qty
    if request.is_close:
        signed = -signed if current_qty > 0 else signed

    projected_qty = current_qty + signed
    projected_asset_notional = quote_notional_usd(projected_qty, request.mark_price, spec, fx_rates)
    delta_notional = order_notional  # conservative: full order notional counts toward gross

    projected_gross = account.gross_exposure + delta_notional
    if not request.is_close:
        projected_gross = account.gross_exposure + order_notional

    projected_equity = account.equity
    proj_gross_lev = gross_leverage(projected_gross, projected_equity)
    proj_net = account.net_exposure + (order_notional if direction == "long" else -order_notional)
    proj_net_lev = net_leverage(proj_net, projected_equity)

    if proj_gross_lev > profile.max_gross_leverage:
        return _reject(
            BrokerRejectionReason.MAX_GROSS_LEVERAGE,
            f"projected gross leverage {proj_gross_lev} > max {profile.max_gross_leverage}",
            account=account,
            asset_exposure_before=current_asset_notional,
        )

    if proj_net_lev > profile.max_net_leverage:
        return _reject(
            BrokerRejectionReason.MAX_LEVERAGE,
            f"projected net leverage {proj_net_lev} > max {profile.max_net_leverage}",
            account=account,
            asset_exposure_before=current_asset_notional,
        )

    asset_max = rules.max_position_notional
    if asset_max and projected_asset_notional > asset_max:
        return _reject(
            BrokerRejectionReason.MAX_ASSET_EXPOSURE,
            f"projected asset notional {projected_asset_notional} > max {asset_max}",
            account=account,
            asset_exposure_before=current_asset_notional,
            asset_exposure_after=projected_asset_notional,
        )

    required_margin = initial_margin_for_notional(order_notional, rules)
    if not request.is_close and required_margin > account.free_margin:
        return _reject(
            BrokerRejectionReason.INSUFFICIENT_MARGIN,
            f"required margin {required_margin} > free margin {account.free_margin}",
            account=account,
            required_initial_margin=required_margin,
            asset_exposure_before=current_asset_notional,
        )

    # Spot crypto: buying power = cash check
    if spec.asset_class == "crypto" and rules.initial_margin_pct >= Decimal("100"):
        if order_notional > account.buying_power and not request.is_close:
            return _reject(
                BrokerRejectionReason.INSUFFICIENT_BUYING_POWER,
                f"notional {order_notional} > buying power {account.buying_power}",
                account=account,
                asset_exposure_before=current_asset_notional,
            )

    bp_after = account.buying_power - required_margin if not request.is_close else account.buying_power

    return BrokerOrderDecision(
        accepted=True,
        accepted_quantity=qty,
        required_initial_margin=required_margin,
        buying_power_before=account.buying_power,
        buying_power_after=max(Decimal("0"), bp_after),
        gross_leverage_before=account.gross_leverage,
        gross_leverage_after=proj_gross_lev,
        asset_exposure_before=current_asset_notional,
        asset_exposure_after=projected_asset_notional,
        diagnostics={
            "order_notional_usd": str(order_notional),
            "projected_gross_exposure": str(projected_gross),
        },
    )
