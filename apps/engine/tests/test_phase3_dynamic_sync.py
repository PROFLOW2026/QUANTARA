"""Phase 3 instances follow dynamic Live Sim active symbols."""

from unittest.mock import MagicMock, patch

from quantara_engine.live_sim.phase3_instance_sync import (
    phase3_eligible_symbols,
    sync_phase3_live_sim_instances,
)


def test_phase3_eligible_excludes_crypto_and_off_universe():
    store = MagicMock()
    with patch(
        "quantara_engine.live_sim.dynamic_universe.list_live_sim_active_trading_symbols",
        return_value={"META", "BTCUSD", "XAUUSD"},
    ):
        with patch(
            "quantara_engine.live_sim.dynamic_universe._vendor_for_symbol",
            side_effect=lambda s: __import__(
                "quantara_engine.broker.vendor", fromlist=["BrokerVendor"]
            ).BrokerVendor.KRAKEN
            if s == "BTCUSD"
            else __import__(
                "quantara_engine.broker.vendor", fromlist=["BrokerVendor"]
            ).BrokerVendor.IBKR,
        ):
            store.get_instrument_by_symbol.return_value = MagicMock(id="i1")
            out = phase3_eligible_symbols(store)
    assert "META" in out
    assert "BTCUSD" not in out
