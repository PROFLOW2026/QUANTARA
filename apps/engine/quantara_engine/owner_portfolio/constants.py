"""Owner Live Sim equal-asset allocation constants."""

from __future__ import annotations

from decimal import Decimal

from quantara_engine.broker.vendor import BrokerVendor

LIVE_SIM_PER_ASSET_CAPITAL = Decimal("1250")
LIVE_SIM_TARGET_CAPITAL = Decimal("10000")

# canonical_symbol -> (broker_vendor, Hebrew label)
LIVE_SIM_EQUAL_ASSET_DEFINITIONS: dict[str, tuple[BrokerVendor, str]] = {
    "NVDA": (BrokerVendor.IBKR, "NVDA"),
    "TSLA": (BrokerVendor.IBKR, "TSLA"),
    "AMD": (BrokerVendor.IBKR, "AMD"),
    "COIN": (BrokerVendor.IBKR, "COIN"),
    "XAUUSD": (BrokerVendor.IBKR, "זהב"),
    "GBPJPY": (BrokerVendor.IBKR, "GBP/JPY"),
    "BTCUSD": (BrokerVendor.KRAKEN, "Bitcoin"),
    "ETHUSD": (BrokerVendor.KRAKEN, "Ethereum"),
}

IBKR_ASSET_SYMBOLS = tuple(
    sym for sym, (vendor, _) in LIVE_SIM_EQUAL_ASSET_DEFINITIONS.items() if vendor == BrokerVendor.IBKR
)
KRAKEN_ASSET_SYMBOLS = tuple(
    sym for sym, (vendor, _) in LIVE_SIM_EQUAL_ASSET_DEFINITIONS.items() if vendor == BrokerVendor.KRAKEN
)

LIVE_SIM_IBKR_TOTAL = LIVE_SIM_PER_ASSET_CAPITAL * len(IBKR_ASSET_SYMBOLS)
LIVE_SIM_KRAKEN_TOTAL = LIVE_SIM_PER_ASSET_CAPITAL * len(KRAKEN_ASSET_SYMBOLS)
