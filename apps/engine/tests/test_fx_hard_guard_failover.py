"""HARD_GUARD must block Twelve Data only, not Tiingo."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from quantara_engine.execution.fx_protection_sources import plan_fx_protection_fetch


def _store() -> MagicMock:
    store = MagicMock()
    store.get_settings_dict.return_value = {}
    return store


@patch(
    "quantara_engine.execution.fx_protection_sources._interval_elapsed",
    return_value=True,
)
def test_hard_guard_tiingo_eligible(_interval: MagicMock) -> None:
    plan = plan_fx_protection_fetch(
        _store(),
        "XAUUSD",
        quota_mode="HARD_GUARD",
        tiingo_eligible=True,
        td_eligible=True,
        near_sl=False,
        has_fresh_stored_1m=False,
        canonical_5m_stale=True,
    )
    assert plan.fetch_provider == "tiingo"
    assert plan.source == "tiingo_1m"


@patch(
    "quantara_engine.execution.fx_protection_sources._interval_elapsed",
    return_value=False,
)
def test_hard_guard_tiingo_throttled_fresh_stored(_interval: MagicMock) -> None:
    plan = plan_fx_protection_fetch(
        _store(),
        "GBPJPY",
        quota_mode="HARD_GUARD",
        tiingo_eligible=True,
        td_eligible=True,
        near_sl=True,
        has_fresh_stored_1m=True,
        canonical_5m_stale=False,
    )
    assert plan.fetch_provider is None
    assert plan.source == "stored_1m"


@patch(
    "quantara_engine.execution.fx_protection_sources._interval_elapsed",
    return_value=False,
)
def test_hard_guard_tiingo_throttled_fresh_5m(_interval: MagicMock) -> None:
    plan = plan_fx_protection_fetch(
        _store(),
        "XAUUSD",
        quota_mode="HARD_GUARD",
        tiingo_eligible=True,
        td_eligible=True,
        near_sl=False,
        has_fresh_stored_1m=False,
        canonical_5m_stale=False,
    )
    assert plan.fetch_provider is None
    assert plan.source == "5m_fallback"
    assert plan.reason == "tiingo_throttle_use_5m"


@patch(
    "quantara_engine.execution.fx_protection_sources._interval_elapsed",
    return_value=True,
)
def test_hard_guard_never_twelve_data_even_if_td_eligible(_interval: MagicMock) -> None:
    plan = plan_fx_protection_fetch(
        _store(),
        "GBPJPY",
        quota_mode="HARD_GUARD",
        tiingo_eligible=False,
        td_eligible=True,
        near_sl=True,
        has_fresh_stored_1m=False,
        canonical_5m_stale=True,
    )
    assert plan.fetch_provider != "twelvedata"
    assert plan.fetch_provider is None
    assert plan.reason == "hard_guard_no_twelve_data"


@patch(
    "quantara_engine.execution.fx_protection_sources._interval_elapsed",
    side_effect=lambda _s, _sym, prov, *_a: prov == "tiingo",
)
def test_hard_guard_tiingo_interval_only(_interval: MagicMock) -> None:
    """Tiingo cadence gate still applies under HARD_GUARD."""
    plan = plan_fx_protection_fetch(
        _store(),
        "XAUUSD",
        quota_mode="HARD_GUARD",
        tiingo_eligible=True,
        td_eligible=True,
        near_sl=False,
        has_fresh_stored_1m=False,
        canonical_5m_stale=True,
        now=datetime(2026, 10, 2, 12, 0, tzinfo=timezone.utc),
    )
    assert plan.fetch_provider == "tiingo"
