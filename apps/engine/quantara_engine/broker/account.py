"""Build broker account snapshot from persisted or simulated state."""

from __future__ import annotations

from decimal import Decimal

from quantara_engine.broker.instruments import get_instrument_spec
from quantara_engine.broker.margin import (
    gross_leverage,
    initial_margin_for_notional,
    maintenance_margin_for_notional,
    margin_level_pct,
    net_leverage,
    quote_notional_usd,
)
from quantara_engine.broker.profile import QUANTARA_STANDARD_PAPER
from quantara_engine.broker.types import (
    AccountState,
    BrokerAccountSnapshot,
    BrokerPosition,
    BrokerProfile,
)


def build_account_snapshot(
    *,
    cash: Decimal,
    balance: Decimal,
    realized_pnl: Decimal,
    positions: dict[str, tuple[Decimal, Decimal, Decimal]],
    fx_rates: dict[str, Decimal],
    profile: BrokerProfile | None = None,
    unrealized_override: Decimal | None = None,
    spot_crypto_cash: Decimal | None = None,
) -> BrokerAccountSnapshot:
    """
    Build canonical broker account metrics.

    positions: symbol -> (signed_net_qty, avg_price, mark_price)
    cash: unallocated cash (spot crypto + residual)
    balance: starting_cash + realized_pnl (margin reserve does NOT reduce balance)
    """
    profile = profile or QUANTARA_STANDARD_PAPER
    broker_positions: dict[str, BrokerPosition] = {}
    gross = Decimal("0")
    net = Decimal("0")
    unrealized = Decimal("0")
    initial_margin = Decimal("0")
    maintenance = Decimal("0")

    for symbol, (qty, avg, mark) in positions.items():
        if qty == 0:
            continue
        spec = get_instrument_spec(symbol)
        rules = profile.rules_for(spec.asset_class)
        notional = quote_notional_usd(qty, mark, spec, fx_rates)
        gross += notional
        net += notional if qty > 0 else -notional
        if spec.quote_currency.upper() == "USD":
            pnl_quote = (mark - avg) * qty
        else:
            rate = fx_rates.get(spec.quote_currency.upper(), Decimal("1"))
            pnl_quote = (mark - avg) * qty / rate if rate else Decimal("0")
        unrealized += pnl_quote.quantize(Decimal("0.01"))
        initial_margin += initial_margin_for_notional(notional, rules)
        maintenance += maintenance_margin_for_notional(notional, rules)
        broker_positions[symbol] = BrokerPosition(
            symbol=symbol,
            net_quantity=qty,
            average_price=avg,
            mark_price=mark,
            unrealized_pnl=pnl_quote.quantize(Decimal("0.01")),
        )

    if unrealized_override is not None:
        unrealized = unrealized_override

    equity = balance + unrealized
    free_margin = equity - initial_margin
    available_margin = max(Decimal("0"), free_margin)
    crypto_cash = spot_crypto_cash if spot_crypto_cash is not None else cash
    ml = margin_level_pct(equity, maintenance)
    g_lev = gross_leverage(gross, equity)
    n_lev = net_leverage(net, equity)

    state = AccountState.ACTIVE
    if ml is not None:
        if ml <= profile.liquidation_level_pct:
            state = AccountState.LIQUIDATION
        elif ml <= profile.margin_call_level_pct:
            state = AccountState.MARGIN_CALL
        elif ml <= profile.margin_warning_level_pct:
            state = AccountState.MARGIN_WARNING

    return BrokerAccountSnapshot(
        profile_slug=profile.slug,
        cash=cash,
        balance=balance,
        equity=equity,
        realized_pnl=realized_pnl,
        unrealized_pnl=unrealized,
        gross_exposure=gross,
        net_exposure=net,
        initial_margin_used=initial_margin,
        maintenance_margin_required=maintenance,
        free_margin=free_margin,
        available_margin=available_margin,
        spot_crypto_cash=crypto_cash,
        buying_power=available_margin,
        margin_level_pct=ml,
        gross_leverage=g_lev,
        net_leverage=n_lev,
        account_state=state,
        positions=broker_positions,
    )
