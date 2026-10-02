"""Twelve Data 720 hard guard — no priority bypass."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from quantara_engine.execution.fx_fast_credit_guard import can_fetch_twelve_data_1m, quota_mode
from quantara_engine.execution.fx_protection_sources import plan_fx_protection_fetch
from quantara_engine.market_data.credits import (
    DAILY_HARD_LIMIT,
    FetchPriority,
    INTERNAL_GUARD_LIMIT,
    _empty_state,
    _today_key,
    can_fetch,
    refresh_twelve_data_health,
)
from quantara_engine.persistence.store import TradingStore


def _store_with_used(used: int) -> TradingStore:
    store = MagicMock(spec=TradingStore)
    state = _empty_state()
    state["used"] = used
    store.get_settings_dict.return_value = {"provider_credits:twelvedata": state}
    return store


@pytest.mark.parametrize(
    "priority",
    [
        FetchPriority.OPEN_POSITION,
        FetchPriority.CATCH_UP,
        FetchPriority.SCHEDULED,
        FetchPriority.SPOT_UI,
        FetchPriority.AUDIT,
    ],
)
def test_can_fetch_719_allows_open_position(priority: FetchPriority) -> None:
    store = _store_with_used(719)
    if priority == FetchPriority.OPEN_POSITION:
        assert can_fetch(store, priority) is True
    elif priority == FetchPriority.CATCH_UP:
        assert can_fetch(store, priority) is True
    else:
        assert can_fetch(store, priority) is False


@pytest.mark.parametrize(
    "priority",
    [
        FetchPriority.OPEN_POSITION,
        FetchPriority.CATCH_UP,
        FetchPriority.SCHEDULED,
        FetchPriority.SPOT_UI,
        FetchPriority.AUDIT,
    ],
)
def test_can_fetch_720_blocks_all_priorities(priority: FetchPriority) -> None:
    store = _store_with_used(INTERNAL_GUARD_LIMIT)
    assert can_fetch(store, priority) is False


@pytest.mark.parametrize(
    "priority",
    [
        FetchPriority.OPEN_POSITION,
        FetchPriority.CATCH_UP,
        FetchPriority.SCHEDULED,
        FetchPriority.SPOT_UI,
        FetchPriority.AUDIT,
    ],
)
def test_can_fetch_800_blocks_all_priorities(priority: FetchPriority) -> None:
    store = _store_with_used(DAILY_HARD_LIMIT)
    assert can_fetch(store, priority) is False


def test_health_sync_skipped_at_hard_guard() -> None:
    store = _store_with_used(720)
    with patch(
        "quantara_engine.market_data.adapters.twelvedata.TwelveDataMarketDataProvider.fetch_api_usage"
    ) as fetch_usage:
        payload = refresh_twelve_data_health(force=True)
        fetch_usage.assert_not_called()
    assert payload.get("quota_mode") == "HARD_GUARD"


def test_fx_protection_hard_guard_never_selects_twelve_data() -> None:
    store = _store_with_used(720)
    plan = plan_fx_protection_fetch(
        store,
        "XAUUSD",
        quota_mode="HARD_GUARD",
        tiingo_eligible=True,
        td_eligible=True,
        near_sl=True,
        has_fresh_stored_1m=False,
        canonical_5m_stale=True,
    )
    assert plan.fetch_provider is None
    assert plan.source == "5m_fallback"


def test_can_fetch_twelve_data_1m_false_at_guard() -> None:
    store = _store_with_used(720)
    assert can_fetch_twelve_data_1m(store) is False
    assert quota_mode(store) == "HARD_GUARD"


def test_utc_daily_reset_clears_guard() -> None:
    from quantara_engine.market_data import credits

    store = MagicMock(spec=TradingStore)
    stale = _empty_state()
    stale["date"] = "1999-01-01"
    stale["used"] = 802
    store.get_settings_dict.return_value = {"provider_credits:twelvedata": stale}
    with patch.object(credits, "_today_key", return_value=_today_key()):
        assert can_fetch(store, FetchPriority.OPEN_POSITION) is True
