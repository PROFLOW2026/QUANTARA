from decimal import Decimal
from unittest.mock import MagicMock

from quantara_engine.live_sim.protection_gate import eth_kraken_protection_status


def test_eth_gate_not_applicable_without_open_position():
    session = MagicMock()
    session.execute.return_value.scalar.return_value = 0
    out = eth_kraken_protection_status(session)
    assert out["status"] == "NOT_APPLICABLE"
    assert out["healthy"] is True
