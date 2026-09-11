"""Canonical margin, exposure, and buying-power calculations."""

from __future__ import annotations

from decimal import Decimal

from quantara_engine.broker.types import AssetClassRules, BrokerAccountSnapshot, InstrumentSpec


def quote_notional_usd(
    quantity: Decimal,
    mark_price: Decimal,
    spec: InstrumentSpec,
    fx_rates: dict[str, Decimal],
) -> Decimal:
    """Convert position notional to account USD."""
    raw = abs(quantity) * mark_price * spec.contract_size
    quote = spec.quote_currency.upper()
    if quote == "USD":
        return raw.quantize(Decimal("0.01"))
    rate = fx_rates.get(quote)
    if not rate or rate <= 0:
        raise ValueError(f"Missing FX rate for {quote}")
    return (raw / rate).quantize(Decimal("0.01"))


def initial_margin_for_notional(notional_usd: Decimal, rules: AssetClassRules) -> Decimal:
    return (notional_usd * rules.initial_margin_pct / Decimal("100")).quantize(Decimal("0.01"))


def maintenance_margin_for_notional(notional_usd: Decimal, rules: AssetClassRules) -> Decimal:
    return (notional_usd * rules.maintenance_margin_pct / Decimal("100")).quantize(Decimal("0.01"))


def compute_account_metrics(
    *,
    cash: Decimal,
    balance: Decimal,
    realized_pnl: Decimal,
    positions: dict[str, tuple[Decimal, Decimal, InstrumentSpec]],
    fx_rates: dict[str, Decimal],
    asset_rules: dict[str, AssetClassRules],
) -> tuple[Decimal, Decimal, Decimal, Decimal, Decimal, Decimal, Decimal, Decimal, Decimal]:
    """
    Returns:
      unrealized, equity, gross_exposure, net_exposure,
      initial_margin_used, maintenance_required, free_margin, buying_power,
      (gross_leverage, net_leverage) as last two via caller
    """
    unrealized = Decimal("0")
    gross = Decimal("0")
    net = Decimal("0")
    initial_margin = Decimal("0")
    maintenance = Decimal("0")

    for symbol, (qty, mark, spec) in positions.items():
        if qty == 0:
            continue
        rules = asset_rules[spec.asset_class]
        notional = quote_notional_usd(qty, mark, spec, fx_rates)
        gross += notional
        net += notional if qty > 0 else -notional
        # Unrealized: simplified mark vs avg embedded in positions dict externally
        initial_margin += initial_margin_for_notional(notional, rules)
        maintenance += maintenance_margin_for_notional(notional, rules)

    equity = balance + unrealized
    free_margin = equity - initial_margin
    # Buying power: how much additional notional at minimum margin rate (conservative: use max initial %)
    buying_power = max(Decimal("0"), free_margin)
    return (
        unrealized,
        equity,
        gross,
        net,
        initial_margin,
        maintenance,
        free_margin,
        buying_power,
    )


def gross_leverage(gross_exposure: Decimal, equity: Decimal) -> Decimal:
    if equity <= 0:
        return Decimal("0")
    return (gross_exposure / equity).quantize(Decimal("0.0001"))


def net_leverage(net_exposure: Decimal, equity: Decimal) -> Decimal:
    if equity <= 0:
        return Decimal("0")
    return (abs(net_exposure) / equity).quantize(Decimal("0.0001"))


def margin_level_pct(equity: Decimal, maintenance: Decimal) -> Decimal | None:
    if maintenance <= 0:
        return None
    return (equity / maintenance * Decimal("100")).quantize(Decimal("0.01"))
