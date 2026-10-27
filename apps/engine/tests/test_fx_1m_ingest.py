"""FX scheduled 1m ingest — Tiingo primary, Twelve Data fallback."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch

from quantara_engine.domain.types import Candle
from quantara_engine.market_data.fx_1m_ingest import (
    fetch_tiingo_fx_1m,
    fetch_twelve_data_fx_1m_fallback,
)
from quantara_engine.market_data.registry import get_asset


def _candle(ts: str) -> Candle:
    return Candle(
        instrument_id="inst-fx",
        timeframe="1m",
        timestamp=datetime.fromisoformat(ts).replace(tzinfo=timezone.utc),
        open=Decimal("1"),
        high=Decimal("2"),
        low=Decimal("0.5"),
        close=Decimal("1.5"),
        source="test",
        is_complete=True,
    )


def test_tiingo_fx_1m_skips_when_not_eligible():
    store = MagicMock()
    asset = get_asset("XAUUSD")
    assert asset is not None
    with patch(
        "quantara_engine.market_data.fx_1m_ingest.is_provider_eligible",
        return_value=False,
    ):
        assert fetch_tiingo_fx_1m(store, asset, "inst-fx", since=None, caller="test") == []


def test_twelve_data_fallback_used_when_tiingo_empty():
    store = MagicMock()
    asset = get_asset("GBPJPY")
    assert asset is not None
    since = datetime(2026, 10, 27, 10, 0, tzinfo=timezone.utc)
    td_candle = _candle("2026-10-27T10:05:00+00:00")

    with (
        patch(
            "quantara_engine.market_data.fx_1m_ingest.is_provider_eligible",
            return_value=True,
        ),
        patch(
            "quantara_engine.market_data.fx_1m_ingest.TwelveDataMarketDataProvider"
        ) as td_cls,
    ):
        td_cls.return_value.fetch_latest.return_value = [td_candle]
        out = fetch_twelve_data_fx_1m_fallback(
            store,
            asset,
            "inst-fx",
            since=since,
            caller="test",
            stale_canonical=True,
        )
    assert len(out) == 1
    td_cls.return_value.fetch_latest.assert_called_once()
    assert td_cls.call_args.kwargs.get("allow_non_canonical_timeframes") is True


def test_fetch_live_fx_falls_back_to_twelve_data_when_tiingo_fails():
    from quantara_workers.jobs import fetch_data as fd

    store = MagicMock()
    instrument = MagicMock()
    instrument.id = "inst-xau"
    asset = get_asset("XAUUSD")
    assert asset is not None
    now = datetime(2026, 10, 27, 12, 30, tzinfo=timezone.utc)
    stale_5m = datetime(2026, 10, 27, 11, 0, tzinfo=timezone.utc)

    store.latest_candle_timestamp.side_effect = lambda iid, tf: (
        stale_5m if tf == "5m" else datetime(2026, 10, 27, 11, 55, tzinfo=timezone.utc)
    )
    store.count_candles.return_value = 500

    td_candle = _candle("2026-10-27T12:05:00+00:00")

    with (
        patch.object(fd, "_try_local_1m_canonical", return_value=(0, 0, False)),
        patch.object(fd, "_should_poll_asset", return_value=(True, None)),
        patch(
            "quantara_engine.market_data.sessions.is_forex_session",
            return_value=True,
        ),
        patch(
            "quantara_engine.market_data.fx_1m_ingest.fetch_tiingo_fx_1m",
            return_value=[],
        ),
        patch(
            "quantara_engine.market_data.fx_1m_ingest.fetch_twelve_data_fx_1m_fallback",
            return_value=[td_candle],
        ),
        patch.object(fd, "derive_higher_from_1m", return_value=(1, 1)) as derive,
        patch.object(fd, "update_spot_from_latest_5m"),
        patch.object(fd, "_mark_tiingo_fetch"),
    ):
        count, derived, err, source = fd._fetch_asset_live(
            store,
            instrument,
            asset,
            now,
            {},
        )

    assert err is None
    assert source == "twelvedata"
    assert count >= 0
    derive.assert_called_once()
    store.upsert_candle.assert_called()


def test_fetch_live_fx_still_ingests_when_should_poll_deferred():
    """Stale FX must reach the 1m ingest path even if 5m poll is deferred."""
    from quantara_workers.jobs import fetch_data as fd

    store = MagicMock()
    instrument = MagicMock()
    instrument.id = "inst-xau"
    asset = get_asset("XAUUSD")
    assert asset is not None
    now = datetime(2026, 10, 27, 12, 30, tzinfo=timezone.utc)
    stale_5m = datetime(2026, 10, 27, 11, 0, tzinfo=timezone.utc)
    stale_1m = datetime(2026, 10, 27, 11, 58, tzinfo=timezone.utc)

    store.latest_candle_timestamp.side_effect = lambda iid, tf: (
        stale_5m if tf == "5m" else stale_1m
    )
    store.count_candles.return_value = 500

    td_candle = _candle("2026-10-27T12:05:00+00:00")

    with (
        patch.object(fd, "_try_local_1m_canonical", return_value=(0, 0, False)),
        patch.object(
            fd,
            "_should_poll_asset",
            return_value=(False, "deferred (Tiingo poll interval 5m)"),
        ),
        patch(
            "quantara_engine.market_data.sessions.is_forex_session",
            return_value=True,
        ),
        patch(
            "quantara_engine.market_data.fx_1m_ingest.fetch_tiingo_fx_1m",
            return_value=[],
        ),
        patch(
            "quantara_engine.market_data.fx_1m_ingest.fetch_twelve_data_fx_1m_fallback",
            return_value=[td_candle],
        ) as td_fetch,
        patch.object(fd, "derive_higher_from_1m", return_value=(1, 1)),
        patch.object(fd, "update_spot_from_latest_5m"),
    ):
        _count, _derived, err, source = fd._fetch_asset_live(
            store,
            instrument,
            asset,
            now,
            {},
        )

    assert err is None
    assert source == "twelvedata"
    td_fetch.assert_called_once()
    store.upsert_candle.assert_called()
