"""1m canonical BTC/ETH mark valuation — reuses fast-protection fetch."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from quantara_engine.domain.types import Candle, Direction, Instrument, Position, PositionStatus
from quantara_engine.execution.crypto_mark_valuation import (
    CRYPTO_CANONICAL_MARKS_KEY,
    apply_crypto_1m_marks,
    crypto_mark_owned_by_1m,
    get_crypto_canonical_mark,
    latest_completed_1m_close,
    load_crypto_canonical_marks,
)
from quantara_engine.market_data.polling import FAST_PROTECTION_TIMEFRAME

TZ = timezone.utc


def _eth() -> Instrument:
    return Instrument(
        id="eth-id",
        symbol="ETHUSD",
        name="ETH",
        asset_class="crypto",
        quote_currency="USD",
        quantity_step=Decimal("0.0001"),
        min_quantity=Decimal("0.0001"),
    )


def _candle(ts: datetime, close: str) -> Candle:
    return Candle(
        instrument_id="eth-id",
        timeframe=FAST_PROTECTION_TIMEFRAME,
        timestamp=ts,
        open=Decimal(close),
        high=Decimal(close),
        low=Decimal(close),
        close=Decimal(close),
        volume=Decimal("1"),
    )


def test_latest_completed_1m_close_ignores_incomplete_bar():
    now = datetime(2026, 9, 13, 3, 20, 30, tzinfo=TZ)
    complete = _candle(datetime(2026, 9, 13, 3, 19, tzinfo=TZ), "2525")
    incomplete = _candle(datetime(2026, 9, 13, 3, 20, tzinfo=TZ), "2530")
    result = latest_completed_1m_close([complete, incomplete], now)
    assert result == (Decimal("2525"), complete.timestamp)


def test_apply_crypto_1m_marks_updates_research_and_broker():
    store = MagicMock()
    eth = _eth()
    store.get_instrument_by_symbol.return_value = eth
    store.get_settings_dict.return_value = {}
    pos = Position(
        id="p1",
        portfolio_id="port-1",
        strategy_instance_id="si1",
        instrument_id="eth-id",
        direction=Direction.LONG,
        quantity=Decimal("1"),
        entry_price=Decimal("2500"),
        stop_loss=Decimal("2480"),
        take_profit=Decimal("2600"),
        current_price=Decimal("2500"),
        status=PositionStatus.OPEN,
        opened_at=datetime(2026, 9, 13, 0, 0, tzinfo=TZ),
        strategy_version_id="sv1",
    )
    store.list_all_competition_entries.return_value = (
        [],
        [],
        [{"portfolio": MagicMock(id="port-1"), "instance": MagicMock()}],
    )
    ts = datetime(2026, 9, 13, 3, 19, tzinfo=TZ)
    mark = Decimal("2525")

    from quantara_engine.portfolio.currency import CurrencyContext, FxRateTable

    with patch(
        "quantara_engine.persistence.batch_summary.batch_open_positions_by_portfolio",
        return_value={"port-1": [pos]},
    ), patch(
        "quantara_engine.portfolio.currency.build_currency_context",
        return_value=CurrencyContext({eth.id: eth}, FxRateTable.usd_only()),
    ), patch(
        "quantara_engine.execution.crypto_mark_valuation.BrokerExecutionService"
    ) as broker_cls:
        broker_cls.return_value.mark_to_market = MagicMock()
        report = apply_crypto_1m_marks(store, {"ETHUSD": (mark, ts)})

    assert report["applied_symbols"] == ["ETHUSD"]
    assert report["research_positions_updated"] == 1
    store.update_open_position_marks_batch.assert_called_once()
    store.update_settings.assert_called()
    args = store.update_settings.call_args[0]
    assert args[0] == CRYPTO_CANONICAL_MARKS_KEY
    assert broker_cls.call_count == 2


def test_older_mark_cannot_overwrite_newer_1m():
    store = MagicMock()
    eth = _eth()
    store.get_instrument_by_symbol.return_value = eth
    newer_at = datetime(2026, 9, 13, 3, 19, tzinfo=TZ)
    store.get_settings_dict.return_value = {
        CRYPTO_CANONICAL_MARKS_KEY: {
            "ETHUSD": {"price": "2525", "at": newer_at.isoformat()},
        }
    }
    store.list_all_competition_entries.return_value = ([], [], [])

    with patch("quantara_engine.execution.crypto_mark_valuation.BrokerExecutionService"):
        report = apply_crypto_1m_marks(
            store,
            {"ETHUSD": (Decimal("2520"), datetime(2026, 9, 13, 3, 15, tzinfo=TZ))},
        )

    assert report["applied_symbols"] == []
    store.update_open_position_marks_batch.assert_not_called()


def test_get_crypto_canonical_mark_roundtrip():
    store = MagicMock()
    at = datetime(2026, 9, 13, 3, 19, tzinfo=TZ)
    store.get_settings_dict.return_value = {
        CRYPTO_CANONICAL_MARKS_KEY: {"ETHUSD": {"price": "2525.5", "at": at.isoformat()}}
    }
    canon = get_crypto_canonical_mark(store, "ETHUSD")
    assert canon == (Decimal("2525.5"), at)


def test_crypto_mark_owned_by_1m_requires_open_exposure():
    store = MagicMock()
    at = datetime(2026, 9, 13, 3, 19, tzinfo=TZ)
    store.get_settings_dict.return_value = {
        CRYPTO_CANONICAL_MARKS_KEY: {"ETHUSD": {"price": "2525", "at": at.isoformat()}}
    }
    store.get_instrument_by_symbol.return_value = _eth()
    store.session.execute.return_value.first.return_value = None
    assert crypto_mark_owned_by_1m(store, "ETHUSD") is False


def test_refresh_broker_marks_skips_1m_owned_crypto():
    from quantara_engine.broker.integration import refresh_broker_marks_from_latest_closes

    store = MagicMock()
    svc = MagicMock()
    svc._tables_ready.return_value = True
    svc.get_account_id.return_value = "acct"
    snap = MagicMock()
    snap.positions.keys.return_value = ["ETHUSD"]
    svc.load_account_snapshot.return_value = snap

    with patch(
        "quantara_engine.broker.integration.BrokerExecutionService",
        return_value=svc,
    ), patch(
        "quantara_engine.execution.crypto_mark_valuation.crypto_mark_owned_by_1m",
        return_value=True,
    ):
        refresh_broker_marks_from_latest_closes(store, symbols=["ETHUSD"])

    svc.mark_to_market.assert_not_called()


def test_run_crypto_fast_protection_applies_marks_from_fetched_candles():
    from quantara_engine.execution.crypto_fast_protection import run_crypto_fast_protection

    store = MagicMock()
    eth = _eth()
    ts = datetime(2026, 9, 13, 3, 19, tzinfo=TZ)
    candle = _candle(ts, "2525")

    with patch(
        "quantara_engine.trading.trading_controls.load_trading_control",
        return_value=MagicMock(),
    ), patch(
        "quantara_engine.trading.trading_controls.allows_position_management",
        return_value=True,
    ), patch(
        "quantara_engine.execution.crypto_fast_protection._collect_research_crypto_work",
        return_value=[],
    ), patch(
        "quantara_engine.execution.crypto_fast_protection._collect_live_sim_crypto_rows",
        return_value=[{"id": "ls1", "instrument_id": "eth-id", "symbol": "ETHUSD"}],
    ), patch(
        "quantara_engine.execution.crypto_fast_protection._fetch_and_store_1m",
        return_value=[candle],
    ), patch(
        "quantara_engine.execution.crypto_fast_protection._process_research_crypto",
        return_value={"checked": 0, "closed": 0},
    ), patch(
        "quantara_engine.execution.crypto_fast_protection._process_live_sim_crypto",
        return_value={"checked": 1, "closed": 0},
    ), patch(
        "quantara_engine.execution.crypto_mark_valuation.apply_crypto_1m_marks",
        return_value={"applied_symbols": ["ETHUSD"]},
    ) as apply_marks:
        store.get_instrument_by_symbol.return_value = eth
        store.list_candles.return_value = [candle]
        report = run_crypto_fast_protection(store, datetime(2026, 9, 13, 3, 20, 30, tzinfo=TZ))

    apply_marks.assert_called_once()
    assert report["marks_applied"] == ["ETHUSD"]
