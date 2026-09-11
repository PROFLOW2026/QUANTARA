"""Tiingo fallback budget protection tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from quantara_engine.market_data.provider_budgets import (
    TIINGO_HOURLY_HARD_LIMIT,
    TIINGO_USABLE_CANDLE_BUDGET,
    can_request_tiingo_candle,
    can_request_tiingo_fx,
    record_request,
    tiingo_budget_mode,
    tiingo_candle_remaining,
)
from quantara_engine.market_data.provider_cooldown import mark_cooldown
from quantara_engine.market_data.registry import list_target_assets
from quantara_engine.market_data.tiingo_fallback_scheduler import (
    AssetFetchContext,
    build_tiingo_fallback_plan,
    build_asset_fetch_contexts,
    _safe_calls_this_cycle,
)
from quantara_engine.market_data.credits import mark_blocked


class FakeStore:
    def __init__(self, settings: dict | None = None):
        self._settings = dict(settings or {})
        self.session = MagicMock()
        self.session.execute.return_value.all.return_value = []

    def get_settings_dict(self) -> dict:
        return self._settings

    def update_settings(self, key: str, value, description: str | None = None, *, flush: bool = True):
        if value is None:
            self._settings.pop(key, None)
        else:
            self._settings[key] = value

    def get_instrument_by_symbol(self, symbol: str):
        inst = MagicMock()
        inst.id = f"inst-{symbol}"
        return inst

    def latest_candle_timestamp(self, instrument_id: str, timeframe: str):
        sym = instrument_id.replace("inst-", "")
        raw = self._settings.get(f"last_candle:{sym}")
        if not raw:
            return None
        return datetime.fromisoformat(str(raw))

    def count_candles(self, instrument_id: str, timeframe: str) -> int:
        sym = instrument_id.replace("inst-", "")
        return int(self._settings.get(f"stored:{sym}", 500))


def _block_primaries(store: FakeStore) -> None:
    mark_cooldown(store, "alpaca", reason="timeout")
    mark_blocked(store, "HTTP 429: quota exhausted")


def _set_tiingo_used(store: FakeStore, used: int) -> None:
    store._settings["provider_budget:tiingo"] = {
        "provider": "tiingo",
        "date": datetime.now(timezone.utc).date().isoformat(),
        "hour": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H"),
        "used_hour": used,
        "used_day": used,
        "status": "healthy",
        "active_symbols": [],
    }


def test_tiingo_candle_budget_never_exceeds_usable_cap():
    store = FakeStore()
    recorded = 0
    for _ in range(TIINGO_USABLE_CANDLE_BUDGET + 5):
        if not can_request_tiingo_candle(store):
            break
        record_request(store, "tiingo", symbol="TEST", caller="test", success=True)
        recorded += 1
    used = store._settings["provider_budget:tiingo"]["used_hour"]
    assert recorded == TIINGO_USABLE_CANDLE_BUDGET
    assert used == TIINGO_USABLE_CANDLE_BUDGET
    assert not can_request_tiingo_candle(store)


def test_fx_reserve_available_when_candle_budget_exhausted():
    store = FakeStore()
    _set_tiingo_used(store, TIINGO_USABLE_CANDLE_BUDGET)
    assert not can_request_tiingo_candle(store)
    assert can_request_tiingo_fx(store)
    _set_tiingo_used(store, TIINGO_HOURLY_HARD_LIMIT)
    assert not can_request_tiingo_fx(store)


def test_safe_calls_spreads_remaining_budget():
    now = datetime(2026, 9, 11, 16, 40, tzinfo=timezone.utc)
    assert _safe_calls_this_cycle(8, now) <= 8
    assert _safe_calls_this_cycle(8, now) >= 1


def test_open_position_assets_prioritized():
    store = FakeStore()
    _block_primaries(store)
    _set_tiingo_used(store, 34)
    now = datetime(2026, 9, 11, 16, 40, tzinfo=timezone.utc)
    for asset in list_target_assets():
        store._settings[f"last_candle:{asset.db_symbol}"] = (now - timedelta(minutes=20)).isoformat()
        store._settings[f"stored:{asset.db_symbol}"] = 500

    store.session.execute.return_value.all.return_value = [("NVDA", 5)]

    with patch(
        "quantara_engine.market_data.tiingo_fallback_scheduler._open_positions_by_symbol",
        return_value={"NVDA": 5},
    ):
        plan = build_tiingo_fallback_plan(store, now)

    assert plan.fallback_active
    assert "NVDA" in plan.allowed_symbols
    assert plan.safe_calls <= tiingo_candle_remaining(store)


def test_stale_assets_ranked_above_fresh_when_no_open_positions():
    store = FakeStore()
    _block_primaries(store)
    _set_tiingo_used(store, 40)
    now = datetime(2026, 9, 11, 16, 40, tzinfo=timezone.utc)
    for asset in list_target_assets():
        age_min = 60 if asset.db_symbol == "TSLA" else 8
        store._settings[f"last_candle:{asset.db_symbol}"] = (now - timedelta(minutes=age_min)).isoformat()
        store._settings[f"stored:{asset.db_symbol}"] = 500

    with patch(
        "quantara_engine.market_data.tiingo_fallback_scheduler._open_positions_by_symbol",
        return_value={},
    ):
        plan = build_tiingo_fallback_plan(store, now)

    if plan.allowed_symbols:
        assert "TSLA" in plan.allowed_symbols


def test_candle_sufficient_assets_deferred_without_tiingo_call():
    store = FakeStore()
    _block_primaries(store)
    now = datetime(2026, 9, 11, 16, 9, tzinfo=timezone.utc)
    for asset in list_target_assets():
        store._settings[f"last_candle:{asset.db_symbol}"] = "2026-09-11T16:05:00+00:00"
        store._settings[f"stored:{asset.db_symbol}"] = 500

    with patch(
        "quantara_engine.market_data.tiingo_fallback_scheduler._open_positions_by_symbol",
        return_value={},
    ):
        plan = build_tiingo_fallback_plan(store, now)

    assert all(
        sym in plan.deferred for sym in [a.db_symbol for a in list_target_assets()]
    ) or len(plan.allowed_symbols) == 0


def test_twelve_hour_simulation_never_exceeds_usable_budget():
    store = FakeStore()
    _block_primaries(store)
    now = datetime(2026, 9, 11, 10, 0, tzinfo=timezone.utc)
    total_requests = 0

    for cycle in range(12):
        cycle_now = now + timedelta(minutes=5 * cycle)
        for asset in list_target_assets():
            store._settings[f"last_candle:{asset.db_symbol}"] = (
                cycle_now - timedelta(minutes=20)
            ).isoformat()
            store._settings[f"stored:{asset.db_symbol}"] = 500

        with patch(
            "quantara_engine.market_data.tiingo_fallback_scheduler._open_positions_by_symbol",
            return_value={"GBPJPY": 3} if cycle % 3 == 0 else {},
        ):
            plan = build_tiingo_fallback_plan(store, cycle_now)

        batch_cost = 1 if plan.crypto_batch else 0
        per_asset = len(plan.allowed_symbols) - (len(plan.crypto_batch) - 1 if plan.crypto_batch else 0)
        cycle_cost = batch_cost + max(0, per_asset - batch_cost)
        for _ in range(cycle_cost):
            if can_request_tiingo_candle(store):
                record_request(store, "tiingo", symbol="sim", caller="test", success=True)
                total_requests += 1

    used = store._settings["provider_budget:tiingo"]["used_hour"]
    assert used <= TIINGO_USABLE_CANDLE_BUDGET
    assert used <= TIINGO_HOURLY_HARD_LIMIT


def test_budget_mode_conservation_at_threshold():
    store = FakeStore()
    _set_tiingo_used(store, 34)
    assert tiingo_budget_mode(store) == "conservation"


def test_fair_rotation_cursor_advances():
    store = FakeStore()
    _block_primaries(store)
    _set_tiingo_used(store, 34)
    now = datetime(2026, 9, 11, 16, 40, tzinfo=timezone.utc)
    for asset in list_target_assets():
        store._settings[f"last_candle:{asset.db_symbol}"] = (now - timedelta(minutes=25)).isoformat()
        store._settings[f"stored:{asset.db_symbol}"] = 500

    with patch(
        "quantara_engine.market_data.tiingo_fallback_scheduler._open_positions_by_symbol",
        return_value={},
    ):
        plan1 = build_tiingo_fallback_plan(store, now)
        cursor1 = store._settings.get("tiingo_fallback_cursor", 0)
        plan2 = build_tiingo_fallback_plan(store, now + timedelta(minutes=5))
        cursor2 = store._settings.get("tiingo_fallback_cursor", 0)

    assert cursor2 != cursor1 or not plan1.allowed_symbols


def test_primary_healthy_skips_fallback_plan():
    store = FakeStore()
    now = datetime(2026, 9, 11, 16, 40, tzinfo=timezone.utc)
    with patch(
        "quantara_engine.market_data.tiingo_fallback_scheduler._open_positions_by_symbol",
        return_value={},
    ), patch(
        "quantara_engine.market_data.tiingo_fallback_scheduler._primary_eligible",
        return_value=True,
    ):
        plan = build_tiingo_fallback_plan(store, now)
    assert not plan.fallback_active
