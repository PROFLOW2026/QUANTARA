"""Fast protection must not advance exit cursors when broker close is not confirmed."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch

from quantara_engine.broker.execution_service import BrokerExecutionResult
from quantara_engine.domain.types import Candle, Direction, Instrument
from quantara_engine.execution.crypto_fast_protection import _process_live_sim_crypto
from quantara_engine.market_data.polling import FAST_PROTECTION_TIMEFRAME

TZ = timezone.utc
NOW = datetime(2026, 10, 27, 1, 0, tzinfo=TZ)
OPENED = datetime(2026, 10, 25, 0, 0, tzinfo=TZ)
SL_TS = datetime(2026, 10, 25, 4, 37, tzinfo=TZ)


def _eth() -> Instrument:
    return Instrument(
        id="eth-id",
        symbol="ETHUSD",
        name="ETH",
        asset_class="crypto",
        quantity_step=Decimal("0.0001"),
        min_quantity=Decimal("0.0001"),
    )


def _sl_candle() -> Candle:
    return Candle(
        instrument_id="eth-id",
        timeframe=FAST_PROTECTION_TIMEFRAME,
        timestamp=SL_TS,
        open=Decimal("2366"),
        high=Decimal("2370"),
        low=Decimal("2360"),
        close=Decimal("2365"),
        volume=Decimal("1"),
        is_complete=True,
    )


def _live_row() -> dict:
    return {
        "id": "pos-eth",
        "instrument_id": "eth-id",
        "direction": "short",
        "quantity": Decimal("1"),
        "entry_price": Decimal("2350"),
        "stop_loss": Decimal("2365"),
        "take_profit": Decimal("2330"),
        "timeframe": "15m",
        "opportunity_key": "opp",
        "broker_account_slug": "live-sim-kraken-like",
        "opened_at": OPENED,
        "symbol": "ETHUSD",
    }


def test_crypto_live_sim_holds_cursor_when_broker_close_not_confirmed():
    store = MagicMock()
    eth = _eth()
    store.get_instrument_by_id.return_value = eth
    cursors: dict[str, str] = {}
    fallback: dict[str, str] = {}
    candle = _sl_candle()
    broker_res = BrokerExecutionResult(
        accepted=True,
        shadow_only=True,
        physical_closed_qty=Decimal("0"),
        fill_quantity=Decimal("0"),
    )

    with patch(
        "quantara_engine.execution.crypto_fast_protection._management_candles",
        return_value=[candle],
    ), patch(
        "quantara_engine.execution.crypto_fast_protection.execute_through_broker",
        return_value=broker_res,
    ), patch(
        "quantara_engine.execution.crypto_fast_protection.finalize_live_sim_position_close",
        return_value=False,
    ):
        result = _process_live_sim_crypto(
            store,
            [_live_row()],
            now=NOW,
            cursors=cursors,
            fallback_cursors=fallback,
            candles_1m_by_instrument={"eth-id": [candle]},
            candles_5m_by_instrument={},
            stale_by_instrument={"eth-id": False},
        )

    assert result["closed"] == 0
    assert "pos-eth" not in cursors
    assert "pos-eth" not in fallback
