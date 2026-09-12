"""GET /candles must return the newest window in chronological order."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from quantara_engine.api.routes import candles


def _candle(ts: datetime, close: float = 100.0):
    row = MagicMock()
    row.timestamp = ts
    row.open = close - 1
    row.high = close + 1
    row.low = close - 2
    row.close = close
    row.volume = 10.0
    return row


@patch("quantara_engine.api.routes.settings")
def test_candles_endpoint_uses_latest_window_not_oldest(mock_settings):
    mock_settings.market_data_provider = "live"

    store = MagicMock()
    inst = MagicMock(id="inst-eth")
    store.get_instrument_by_symbol.return_value = inst

    base = datetime(2026, 9, 10, 0, 0, tzinfo=timezone.utc)
    newest_window = [_candle(base + timedelta(minutes=5 * i), 100 + i) for i in range(250)]
    store.list_recent_candles.return_value = newest_window

    payload = candles(store, instrument_id="ETHUSD", timeframe="5m", limit=250)

    store.list_recent_candles.assert_called_once_with(inst.id, "5m", limit=250)
    store.list_candles.assert_not_called()
    assert len(payload) == 250
    assert payload[0]["time"] == newest_window[0].timestamp.isoformat()
    assert payload[-1]["time"] == newest_window[-1].timestamp.isoformat()
    times = [row["time"] for row in payload]
    assert times == sorted(times)


@patch("quantara_engine.api.routes.settings")
def test_candles_endpoint_latest_timestamp_is_most_recent(mock_settings):
    mock_settings.market_data_provider = "live"

    store = MagicMock()
    inst = MagicMock(id="inst-btc")
    store.get_instrument_by_symbol.return_value = inst

    older = _candle(datetime(2026, 9, 4, 12, 0, tzinfo=timezone.utc), 50.0)
    latest = _candle(datetime(2026, 9, 12, 18, 0, tzinfo=timezone.utc), 70.0)
    store.list_recent_candles.return_value = [older, latest]

    payload = candles(store, instrument_id="BTCUSD", timeframe="15m", limit=250)

    assert payload[-1]["time"] == latest.timestamp.isoformat()
    assert payload[-1]["close"] == 70.0
