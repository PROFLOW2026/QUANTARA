"""Canonical pre-trade broker order evaluation."""

from __future__ import annotations

from decimal import Decimal

from quantara_engine.broker.instruments import get_instrument_spec
from quantara_engine.broker.margin import (
    gross_leverage,
    initial_margin_for_notional,
    maintenance_margin_for_notional,
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
        buying_power_before=account.available_margin,
        buying_power_after=account.available_margin,
        gross_leverage_before=account.gross_leverage,
        gross_leverage_after=account.gross_leverage,
        asset_exposure_before=extra.get("asset_exposure_before", Decimal("0")),
        asset_exposure_after=extra.get("asset_exposure_after", extra.get("asset_exposure_before", Decimal("0"))),
        diagnostics=extra,
    )


def _signed_qty(direction: str, qty: Decimal) -> Decimal:
    return qty if direction.lower() == "long" else -qty


def _project_post_trade(
    account: BrokerAccountSnapshot,
    symbol: str,
    current_qty: Decimal,
    order_signed: Decimal,
    mark_price: Decimal,
    spec,
    fx_rates: dict[str, Decimal],
    profile: BrokerProfile,
) -> tuple[Decimal, Decimal, Decimal, Decimal, Decimal]:
    """Return projected gross, net, initial_margin, maintenance, asset_notional."""
    projected_qty = current_qty + order_signed

    gross = Decimal("0")
    net = Decimal("0")
    initial = Decimal("0")
    maintenance = Decimal("0")

    for sym, pos in account.positions.items():
        qty = pos.net_quantity if sym != symbol else projected_qty
        if qty == 0:
            continue
        s = get_instrument_spec(sym)
        rules = profile.rules_for(s.asset_class)
        notional = quote_notional_usd(qty, pos.mark_price if sym != symbol else mark_price, s, fx_rates)
        gross += notional
        net += notional if qty > 0 else -notional
        initial += initial_margin_for_notional(notional, rules)
        maintenance += maintenance_margin_for_notional(notional, rules)

    if symbol not in account.positions and projected_qty != 0:
        rules = profile.rules_for(spec.asset_class)
        notional = quote_notional_usd(projected_qty, mark_price, spec, fx_rates)
        gross += notional
        net += notional if projected_qty > 0 else -notional
        initial += initial_margin_for_notional(notional, rules)
        maintenance += maintenance_margin_for_notional(notional, rules)

    asset_notional = (
        quote_notional_usd(projected_qty, mark_price, spec, fx_rates) if projected_qty else Decimal("0")
    )
    return gross, net, initial, maintenance, asset_notional


def _is_risk_reducing(current_qty: Decimal, order_signed: Decimal) -> bool:
    if current_qty == 0 or order_signed == 0:
        return False
    return (current_qty > 0 and order_signed < 0) or (current_qty < 0 and order_signed > 0)


def evaluate_broker_order(
    account: BrokerAccountSnapshot,
    profile: BrokerProfile,
    request: BrokerOrderRequest,
    fx_rates: dict[str, Decimal],
) -> BrokerOrderDecision:
    """
    Determine whether a broker would accept an order.

    Strategy risk must already have passed. This layer enforces account reality.
    Risk-reducing orders are allowed even in margin_call (closes only).
    """
    spec = get_instrument_spec(request.symbol)
    rules = profile.rules_for(spec.asset_class)

    pos = account.positions.get(request.symbol)
    current_qty = pos.net_quantity if pos else Decimal("0")
    current_asset_notional = (
        quote_notional_usd(current_qty, request.mark_price, spec, fx_rates) if current_qty else Decimal("0")
    )

    qty = normalize_quantity(request.quantity, spec)
    if qty <= 0:
        return _reject(
            BrokerRejectionReason.INVALID_QUANTITY,
            "quantity below minimum or invalid step after normalization",
            account=account,
        )

    qty_err = validate_quantity(qty, spec)
    if qty_err:
        return _reject(BrokerRejectionReason.INVALID_QUANTITY, qty_err, account=account)

    direction = request.direction.lower()
    order_signed = _signed_qty(direction, qty)
    risk_reducing = _is_risk_reducing(current_qty, order_signed)
    pure_close = risk_reducing and abs(current_qty + order_signed) <= abs(current_qty)

    # Account state gates — risk-increasing blocked in call/liquidation
    if account.account_state == AccountState.PAUSED:
        return _reject(
            BrokerRejectionReason.ACCOUNT_PAUSED,
            "account paused",
            account=account,
        )
    if account.account_state == AccountState.LIQUIDATION and not request.is_liquidation:
        return _reject(
            BrokerRejectionReason.LIQUIDATION,
            "account in liquidation — only liquidation orders allowed",
            account=account,
        )
    if account.account_state == AccountState.MARGIN_CALL and not risk_reducing:
        return _reject(
            BrokerRejectionReason.MARGIN_CALL,
            "margin call — risk-increasing orders blocked",
            account=account,
        )

    if not request.data_fresh:
        return _reject(BrokerRejectionReason.STALE_MARKET_DATA, "market data stale", account=account)

    # US equities: no execution while market closed (entries OR closes)
    if spec.session_key == "us_equity_rth" and not request.market_open and not request.is_liquidation:
        return _reject(
            BrokerRejectionReason.MARKET_CLOSED,
            "US equity market closed — no executable price",
            account=account,
        )

    if not request.is_close and not request.market_open and not request.is_liquidation:
        return _reject(
            BrokerRejectionReason.MARKET_CLOSED,
            "market closed for new entries",
            account=account,
        )

    if direction == "short" and not rules.shorting_allowed and not pure_close:
        return _reject(
            BrokerRejectionReason.SHORT_NOT_ALLOWED,
            f"shorting not allowed for {spec.asset_class} (QUANTARA_STANDARD_PAPER spot crypto)",
            account=account,
        )

    order_notional = quote_notional_usd(qty, request.mark_price, spec, fx_rates)
    if order_notional < spec.min_notional and spec.quote_currency != "JPY":
        return _reject(
            BrokerRejectionReason.INVALID_QUANTITY,
            f"notional {order_notional} below min {spec.min_notional}",
            account=account,
        )

    if rules.max_order_notional and order_notional > rules.max_order_notional and not pure_close:
        return _reject(
            BrokerRejectionReason.MAX_ORDER_NOTIONAL,
            f"order notional {order_notional} > max {rules.max_order_notional}",
            account=account,
        )

    projected_gross, projected_net, proj_initial, _, projected_asset_notional = _project_post_trade(
        account, request.symbol, current_qty, order_signed, request.mark_price, spec, fx_rates, profile
    )
    projected_equity = account.equity
    proj_gross_lev = gross_leverage(projected_gross, projected_equity)
    proj_net_lev = net_leverage(projected_net, projected_equity)

    # Risk-reducing portion: skip leverage/asset expansion checks for the close qty
    increasing_signed = order_signed
    if risk_reducing:
        close_portion = min(abs(order_signed), abs(current_qty))
        increasing_signed = order_signed + (_signed_qty("long", close_portion) if order_signed < 0 else -close_portion)
        if increasing_signed == 0 or abs(increasing_signed) < abs(order_signed):
            # Validate only the risk-increasing remainder (flip open)
            if increasing_signed != 0:
                inc_gross, inc_net, _, _, inc_asset = _project_post_trade(
                    account,
                    request.symbol,
                    current_qty + (_signed_qty("long", close_portion) if order_signed < 0 else -close_portion),
                    increasing_signed,
                    request.mark_price,
                    spec,
                    fx_rates,
                    profile,
                )
                if inc_gross > projected_gross:
                    projected_gross = inc_gross
                    projected_net = inc_net
                    projected_asset_notional = inc_asset
                    proj_gross_lev = gross_leverage(projected_gross, projected_equity)
                    proj_net_lev = net_leverage(projected_net, projected_equity)
            else:
                # Pure risk-reducing — accept without leverage checks
                return BrokerOrderDecision(
                    accepted=True,
                    accepted_quantity=qty,
                    buying_power_before=account.available_margin,
                    buying_power_after=account.available_margin,
                    gross_leverage_before=account.gross_leverage,
                    gross_leverage_after=gross_leverage(
                        projected_gross, projected_equity
                    ),
                    asset_exposure_before=current_asset_notional,
                    asset_exposure_after=projected_asset_notional,
                    diagnostics={"risk_reducing": True, "pure_close": True},
                )

    if not risk_reducing or increasing_signed != 0:
        if proj_gross_lev > profile.max_gross_leverage:
            return _reject(
                BrokerRejectionReason.MAX_GROSS_LEVERAGE,
                f"projected gross leverage {proj_gross_lev} > max {profile.max_gross_leverage}",
                account=account,
                asset_exposure_before=current_asset_notional,
                asset_exposure_after=projected_asset_notional,
            )

        if proj_net_lev > profile.max_net_leverage:
            return _reject(
                BrokerRejectionReason.MAX_LEVERAGE,
                f"projected net leverage {proj_net_lev} > max {profile.max_net_leverage}",
                account=account,
                asset_exposure_before=current_asset_notional,
            )

        if rules.max_leverage and projected_equity > 0:
            asset_lev = (projected_asset_notional / projected_equity).quantize(Decimal("0.0001"))
            if asset_lev > rules.max_leverage:
                return _reject(
                    BrokerRejectionReason.MAX_ASSET_EXPOSURE,
                    f"asset leverage {asset_lev} > max {rules.max_leverage}",
                    account=account,
                    asset_exposure_before=current_asset_notional,
                    asset_exposure_after=projected_asset_notional,
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

        margin_delta = proj_initial - account.initial_margin_used
        if margin_delta > account.free_margin and not risk_reducing:
            return _reject(
                BrokerRejectionReason.INSUFFICIENT_MARGIN,
                f"required additional margin {margin_delta} > free margin {account.free_margin}",
                account=account,
                required_initial_margin=margin_delta,
                asset_exposure_before=current_asset_notional,
            )

        # Spot crypto: consume spot_crypto_cash
        if spec.asset_class == "crypto" and rules.initial_margin_pct >= Decimal("100"):
            if order_notional > account.spot_crypto_cash and not risk_reducing:
                return _reject(
                    BrokerRejectionReason.INSUFFICIENT_BUYING_POWER,
                    f"spot crypto notional {order_notional} > available cash {account.spot_crypto_cash}",
                    account=account,
                    asset_exposure_before=current_asset_notional,
                )

    return BrokerOrderDecision(
        accepted=True,
        accepted_quantity=qty,
        required_initial_margin=proj_initial - account.initial_margin_used,
        buying_power_before=account.available_margin,
        buying_power_after=max(Decimal("0"), account.available_margin - max(Decimal("0"), proj_initial - account.initial_margin_used)),
        gross_leverage_before=account.gross_leverage,
        gross_leverage_after=proj_gross_lev,
        asset_exposure_before=current_asset_notional,
        asset_exposure_after=projected_asset_notional,
        diagnostics={
            "order_notional_usd": str(order_notional),
            "projected_gross_exposure": str(projected_gross),
            "risk_reducing": risk_reducing,
        },
    )
