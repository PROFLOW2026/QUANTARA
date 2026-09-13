"""Finnhub equity LIVE_MARK tests — display/valuation split from Alpaca protection."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch

from quantara_engine.execution.crypto_mark_valuation import (
    FAST_CANONICAL_MARKS_KEY,
    display_price_from_canonical_mark,
)
from quantara_engine.execution.equity_live_mark import (
    RECOVERY_HEALTHY_STREAK,
    SOURCE_ALPACA,
    SOURCE_FINNHUB,
    is_finnhub_quote_fresh,
    resolve_equity_live_mark,
    run_finnhub_equity_live_marks,
    store_equity_alpaca_fallback_mark,
)
from quantara_engine.market_data.adapters.finnhub import FinnhubObservation


class FakeStore:
    def __init__(self, settings: dict | None = None):
        self._settings = dict(settings or {})
        self._writes: list[tuple[str, object]] = []

    def get_settings_dict(self) -> dict:
        return self._settings

    def update_settings(self, key: str, value, description: str | None = None, *, flush: bool = True):
        self._writes.append((key, value))
        if value is None:
            self._settings.pop(key, None)
        else:
            self._settings[key] = value

    def get_instrument_by_symbol(self, symbol: str):
        return MagicMock(id="inst-1", symbol=symbol)

    def session(self):
        return MagicMock()


def _obs(db_sym: str, price: str, *, age_seconds: float = 15.0) -> FinnhubObservation:
    now = datetime.now(timezone.utc)
    ts = now - timedelta(seconds=age_seconds)
    return FinnhubObservation(
        provider="finnhub",
        asset=db_sym,
        provider_symbol=db_sym,
        price=Decimal(price),
        timestamp=ts,
        received_at=now,
        data_type="quote",
        freshness_seconds=age_seconds,
    )


def test_finnhub_quote_freshness():
    now = datetime.now(timezone.utc)
    assert is_finnhub_quote_fresh(
        quote_at=now - timedelta(seconds=30),
        received_at=now - timedelta(seconds=5),
        now=now,
    )
    assert not is_finnhub_quote_fresh(
        quote_at=now - timedelta(seconds=200),
        received_at=now - timedelta(seconds=5),
        now=now,
    )


@patch("quantara_engine.execution.equity_live_mark.apply_fast_1m_marks")
def test_alpaca_fallback_stored_without_overwriting_finnhub(apply_marks):
    now = datetime.now(timezone.utc)
    store = FakeStore(
        {
            FAST_CANONICAL_MARKS_KEY: {
                "NVDA": {
                    "price": "220",
                    "at": now.isoformat(),
                    "source": SOURCE_FINNHUB,
                }
            }
        }
    )
    store_equity_alpaca_fallback_mark(
        store,
        "NVDA",
        Decimal("219"),
        now - timedelta(minutes=1),
    )
    apply_marks.assert_not_called()
    entry = store._settings[FAST_CANONICAL_MARKS_KEY]["NVDA"]
    assert entry["alpaca_price"] == "219"
    assert entry["source"] == SOURCE_FINNHUB


@patch("quantara_engine.execution.equity_live_mark.apply_fast_1m_marks")
def test_alpaca_becomes_display_when_no_finnhub(apply_marks):
    now = datetime.now(timezone.utc)
    store = FakeStore()
    store_equity_alpaca_fallback_mark(store, "NVDA", Decimal("219"), now)
    apply_marks.assert_called_once()
    entry = store._settings[FAST_CANONICAL_MARKS_KEY]["NVDA"]
    assert entry["source"] == SOURCE_ALPACA


def test_resolve_prefers_fresh_finnhub_over_alpaca():
    now = datetime.now(timezone.utc)
    store = FakeStore(
        {
            FAST_CANONICAL_MARKS_KEY: {
                "NVDA": {
                    "price": "220",
                    "at": (now - timedelta(seconds=20)).isoformat(),
                    "source": SOURCE_FINNHUB,
                    "alpaca_price": "219",
                    "alpaca_at": (now - timedelta(minutes=1)).isoformat(),
                }
            }
        }
    )
    resolved = resolve_equity_live_mark(store, "NVDA", now=now)
    assert resolved is not None
    assert resolved[0] == Decimal("220")
    assert resolved[2] == SOURCE_FINNHUB


def test_resolve_falls_back_to_alpaca_when_finnhub_stale():
    now = datetime.now(timezone.utc)
    store = FakeStore(
        {
            FAST_CANONICAL_MARKS_KEY: {
                "NVDA": {
                    "price": "220",
                    "at": (now - timedelta(minutes=10)).isoformat(),
                    "source": SOURCE_FINNHUB,
                    "alpaca_price": "219",
                    "alpaca_at": (now - timedelta(minutes=1)).isoformat(),
                }
            }
        }
    )
    resolved = resolve_equity_live_mark(store, "NVDA", now=now)
    assert resolved is not None
    assert resolved[0] == Decimal("219")
    assert resolved[2] == SOURCE_ALPACA


@patch("quantara_engine.execution.crypto_mark_valuation.is_fast_protection_equity", return_value=True)
@patch("quantara_engine.execution.equity_live_mark.resolve_equity_live_mark")
def test_display_price_uses_equity_resolver(resolve_mock, _eq):
    resolve_mock.return_value = (Decimal("100"), datetime.now(timezone.utc), SOURCE_FINNHUB)
    store = FakeStore()
    result = display_price_from_canonical_mark(store, "NVDA")
    assert result is not None
    assert result[0] == Decimal("100")


@patch("quantara_engine.execution.equity_live_mark.apply_fast_1m_marks")
@patch("quantara_engine.execution.equity_live_mark.is_us_equity_rth", return_value=True)
@patch("quantara_engine.execution.equity_live_mark.can_request", return_value=True)
@patch("quantara_engine.execution.equity_live_mark.finnhub_configured", return_value=True)
@patch("quantara_engine.execution.equity_live_mark.FinnhubMarketDataProvider")
def test_finnhub_live_mark_requires_recovery_streak(provider_cls, _cfg, _can, _rth, _apply):
    provider = MagicMock()
    provider.fetch_quote.return_value = _obs("NVDA", "220")
    provider_cls.return_value = provider
    store = FakeStore()

    report = run_finnhub_equity_live_marks(store)
    assert report["status"] == "success"
    assert "NVDA" not in report["applied"]

    for _ in range(RECOVERY_HEALTHY_STREAK):
        run_finnhub_equity_live_marks(store)

    report = run_finnhub_equity_live_marks(store)
    assert "NVDA" in report["applied"]


@patch("quantara_engine.execution.equity_live_mark.is_us_equity_rth", return_value=True)
@patch("quantara_engine.execution.equity_live_mark.can_request", return_value=True)
@patch("quantara_engine.execution.equity_live_mark.finnhub_configured", return_value=True)
@patch("quantara_engine.execution.equity_live_mark.FinnhubMarketDataProvider")
def test_stale_finnhub_falls_back_to_alpaca(provider_cls, _cfg, _can, _rth):
    now = datetime.now(timezone.utc)
    store = FakeStore(
        {
            FAST_CANONICAL_MARKS_KEY: {
                "NVDA": {
                    "price": "220",
                    "at": now.isoformat(),
                    "source": SOURCE_FINNHUB,
                    "alpaca_price": "219",
                    "alpaca_at": (now - timedelta(minutes=1)).isoformat(),
                }
            }
        }
    )
    provider = MagicMock()
    provider.fetch_quote.return_value = _obs("NVDA", "220", age_seconds=500)
    provider_cls.return_value = provider

    with patch("quantara_engine.execution.equity_live_mark.apply_fast_1m_marks") as apply_marks:
        report = run_finnhub_equity_live_marks(store, now=now)
        assert "NVDA" in report["fallbacks"]
        apply_marks.assert_called()
