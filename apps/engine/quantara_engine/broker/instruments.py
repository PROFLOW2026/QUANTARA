"""Canonical InstrumentSpec for active universe."""

from __future__ import annotations

from decimal import Decimal

from quantara_engine.broker.types import InstrumentSpec

INSTRUMENT_SPECS: dict[str, InstrumentSpec] = {
    "BTCUSD": InstrumentSpec(
        symbol="BTCUSD",
        asset_class="crypto",
        base_currency="BTC",
        quote_currency="USD",
        pip_size=Decimal("0.01"),
        tick_size=Decimal("0.01"),
        contract_size=Decimal("1"),
        min_quantity=Decimal("0.0001"),
        quantity_step=Decimal("0.0001"),
        min_notional=Decimal("10"),
        shortable=False,
        fractional=True,
        session_key="24x7",
    ),
    "ETHUSD": InstrumentSpec(
        symbol="ETHUSD",
        asset_class="crypto",
        base_currency="ETH",
        quote_currency="USD",
        pip_size=Decimal("0.01"),
        tick_size=Decimal("0.01"),
        contract_size=Decimal("1"),
        min_quantity=Decimal("0.0001"),
        quantity_step=Decimal("0.0001"),
        min_notional=Decimal("10"),
        shortable=False,
        fractional=True,
        session_key="24x7",
    ),
    "XAUUSD": InstrumentSpec(
        symbol="XAUUSD",
        asset_class="commodity",
        base_currency="XAU",
        quote_currency="USD",
        pip_size=Decimal("0.01"),
        tick_size=Decimal("0.01"),
        contract_size=Decimal("1"),
        min_quantity=Decimal("0.01"),
        quantity_step=Decimal("0.01"),
        min_notional=Decimal("10"),
        shortable=True,
        fractional=True,
        session_key="24x5",
    ),
    "GBPJPY": InstrumentSpec(
        symbol="GBPJPY",
        asset_class="forex",
        base_currency="GBP",
        quote_currency="JPY",
        pip_size=Decimal("0.01"),
        tick_size=Decimal("0.001"),
        contract_size=Decimal("1"),  # 1 unit = 1 GBP in quote JPY
        min_quantity=Decimal("1000"),
        quantity_step=Decimal("1000"),
        min_notional=Decimal("1000"),  # JPY notional floor (PAPER ASSUMPTION)
        shortable=True,
        fractional=False,
        session_key="24x5",
    ),
    "NVDA": InstrumentSpec(
        symbol="NVDA",
        asset_class="stock",
        base_currency="NVDA",
        quote_currency="USD",
        pip_size=Decimal("0.01"),
        tick_size=Decimal("0.01"),
        contract_size=Decimal("1"),
        min_quantity=Decimal("1"),
        quantity_step=Decimal("1"),
        min_notional=Decimal("1"),
        shortable=True,
        fractional=False,
        session_key="us_equity_rth",
    ),
    "TSLA": InstrumentSpec(
        symbol="TSLA",
        asset_class="stock",
        base_currency="TSLA",
        quote_currency="USD",
        pip_size=Decimal("0.01"),
        tick_size=Decimal("0.01"),
        contract_size=Decimal("1"),
        min_quantity=Decimal("1"),
        quantity_step=Decimal("1"),
        min_notional=Decimal("1"),
        shortable=True,
        fractional=False,
        session_key="us_equity_rth",
    ),
    "AMD": InstrumentSpec(
        symbol="AMD",
        asset_class="stock",
        base_currency="AMD",
        quote_currency="USD",
        pip_size=Decimal("0.01"),
        tick_size=Decimal("0.01"),
        contract_size=Decimal("1"),
        min_quantity=Decimal("1"),
        quantity_step=Decimal("1"),
        min_notional=Decimal("1"),
        shortable=True,
        fractional=False,
        session_key="us_equity_rth",
    ),
    "COIN": InstrumentSpec(
        symbol="COIN",
        asset_class="stock",
        base_currency="COIN",
        quote_currency="USD",
        pip_size=Decimal("0.01"),
        tick_size=Decimal("0.01"),
        contract_size=Decimal("1"),
        min_quantity=Decimal("1"),
        quantity_step=Decimal("1"),
        min_notional=Decimal("1"),
        shortable=True,
        fractional=False,
        session_key="us_equity_rth",
    ),
}


def get_instrument_spec(symbol: str) -> InstrumentSpec:
    key = symbol.upper()
    if key not in INSTRUMENT_SPECS:
        raise KeyError(f"No InstrumentSpec for {symbol}")
    return INSTRUMENT_SPECS[key]
