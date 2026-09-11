"""Regression tests for canonical strategy slug resolution on ORM instances."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from quantara_engine.competition.constants import ACTIVE_COMPETITION_PORTFOLIOS
from quantara_engine.competition.orb_constants import ORB_COMPETITION_PORTFOLIOS
from quantara_engine.domain.types import (
    Direction,
    IntentStatus,
    Mode,
    OrderIntent,
    SignalAction,
    new_id,
)
from quantara_engine.models.enums import SignalAction as OrmSignalAction
from quantara_engine.persistence.store import (
    TradingStore,
    _intent_idempotency_key,
)
from quantara_engine.risk.opportunity import (
    legacy_idempotency_key,
    opportunity_idempotency_key,
    orb_opportunity_key,
    pullback_opportunity_key,
)
from quantara_workers.jobs.run_strategy import strategy_freshness_summary


ORB = ORB_COMPETITION_PORTFOLIOS[0]
ROBOT_A = ACTIVE_COMPETITION_PORTFOLIOS[0]

OPP = orb_opportunity_key(
    symbol="BTCUSD",
    session_date="2026-09-11",
    direction="long",
    range_high=Decimal("76844.368"),
    range_low=Decimal("76523.795"),
)
PULLBACK_OPP = pullback_opportunity_key(
    symbol="GBPJPY",
    timeframe="5m",
    direction="long",
    setup_candle_timestamp=datetime(2026, 9, 11, 10, 0, tzinfo=timezone.utc),
)


def _uuid(s: str) -> uuid.UUID:
    return uuid.UUID(s)


def _orm_instance(*, instance_id: str, instrument_id: str, version_id: str) -> SimpleNamespace:
    """ORM row without strategy_slug — mirrors production StrategyInstance model."""
    return SimpleNamespace(
        id=_uuid(instance_id),
        instrument_id=_uuid(instrument_id),
        strategy_version_id=_uuid(version_id),
        timeframe="5m",
    )


def _wire_slug_chain(
    store: TradingStore,
    *,
    instance,
    slug: str,
    version_id: str | None = None,
    strategy_id: str = "00000000-0000-0000-0000-000000000701",
) -> None:
    version_id = version_id or str(instance.strategy_version_id)
    version_row = SimpleNamespace(strategy_id=_uuid(strategy_id))
    strategy_row = SimpleNamespace(slug=slug)

    def _get(model, pk):
        name = getattr(model, "__name__", str(model))
        if "StrategyInstance" in name and pk == instance.id:
            return instance
        if "StrategyVersion" in name and pk == _uuid(version_id):
            return version_row
        if name.endswith("Strategy") and pk == _uuid(strategy_id):
            return strategy_row
        return None

    store.session.get = MagicMock(side_effect=_get)


def _entry_intent(
    *,
    portfolio_id: str,
    instance_id: str,
    signal_id: str,
    ts: datetime | None = None,
) -> OrderIntent:
    ts = ts or datetime(2026, 9, 11, 12, 0, tzinfo=timezone.utc)
    return OrderIntent(
        id=new_id(),
        signal_id=signal_id,
        strategy_instance_id=instance_id,
        portfolio_id=portfolio_id,
        direction=Direction.LONG,
        quantity=Decimal("0.01"),
        stop_loss=Decimal("76000"),
        take_profit=Decimal("78000"),
        target_risk_amount=Decimal("20"),
        actual_risk_amount=Decimal("20"),
        signal_candle_timestamp=ts,
        risk_profile_id="00000000-0000-0000-0000-000000000801",
        status=IntentStatus.PENDING_EXECUTION,
    )


class TestResolveStrategySlug:
    def test_orm_without_strategy_slug_attribute_resolves_via_relation(self):
        store = TradingStore(MagicMock(), mode=Mode.PAPER)
        instance = _orm_instance(
            instance_id=ORB.instance_id,
            instrument_id="00000000-0000-0000-0000-000000000601",
            version_id="00000000-0000-0000-0000-000000000602",
        )
        assert not hasattr(instance, "strategy_slug")
        _wire_slug_chain(store, instance=instance, slug="opening-range-breakout")

        assert store.resolve_strategy_slug(instance) == "opening-range-breakout"
        assert store.resolve_strategy_slug(ORB.instance_id) == "opening-range-breakout"

    def test_missing_strategy_returns_none_without_crash(self):
        store = TradingStore(MagicMock(), mode=Mode.PAPER)
        instance = _orm_instance(
            instance_id=ORB.instance_id,
            instrument_id="00000000-0000-0000-0000-000000000601",
            version_id="00000000-0000-0000-0000-000000000602",
        )
        version_row = SimpleNamespace(strategy_id=_uuid("00000000-0000-0000-0000-000000000701"))

        def _get(model, pk):
            name = getattr(model, "__name__", str(model))
            if "StrategyInstance" in name:
                return instance
            if "StrategyVersion" in name:
                return version_row
            return None

        store.session.get = MagicMock(side_effect=_get)
        assert store.resolve_strategy_slug(instance) is None


class TestCanonicalOpportunityEnforcement:
    def test_requires_canonical_for_orb_competition_without_strategy_slug_field(self):
        store = TradingStore(MagicMock(), mode=Mode.PAPER)
        store._bt_uuid = MagicMock(return_value=None)
        instance = _orm_instance(
            instance_id=ORB.instance_id,
            instrument_id="00000000-0000-0000-0000-000000000601",
            version_id="00000000-0000-0000-0000-000000000602",
        )
        _wire_slug_chain(store, instance=instance, slug="opening-range-breakout")

        intent = _entry_intent(
            portfolio_id=ORB.portfolio_id,
            instance_id=ORB.instance_id,
            signal_id=new_id(),
        )
        assert store._requires_canonical_opportunity_key(intent) is True

    def test_requires_canonical_for_robot_a(self):
        store = TradingStore(MagicMock(), mode=Mode.PAPER)
        store._bt_uuid = MagicMock(return_value=None)
        instance = _orm_instance(
            instance_id=ROBOT_A.instance_id,
            instrument_id="00000000-0000-0000-0000-000000000611",
            version_id="00000000-0000-0000-0000-000000000612",
        )
        _wire_slug_chain(store, instance=instance, slug="gold-trend-pullback")

        intent = _entry_intent(
            portfolio_id=ROBOT_A.portfolio_id,
            instance_id=ROBOT_A.instance_id,
            signal_id=new_id(),
        )
        assert store._requires_canonical_opportunity_key(intent) is True


class TestSaveOrderIntentSlugResolution:
    def _store_with_signal(
        self,
        *,
        slug: str,
        portfolio_id: str,
        instance_id: str,
        metadata: dict,
        symbol: str = "BTCUSD",
    ) -> tuple[TradingStore, OrderIntent]:
        store = TradingStore(MagicMock(), mode=Mode.PAPER)
        store._bt_uuid = MagicMock(return_value=None)
        signal_id = new_id()
        instrument_id = "00000000-0000-0000-0000-000000000621"
        version_id = "00000000-0000-0000-0000-000000000622"
        instance = _orm_instance(
            instance_id=instance_id,
            instrument_id=instrument_id,
            version_id=version_id,
        )
        _wire_slug_chain(store, instance=instance, slug=slug, version_id=version_id)
        signal_row = SimpleNamespace(
            action=OrmSignalAction.BUY,
            reason="entry",
            metadata_=metadata,
        )
        instrument_row = SimpleNamespace(symbol=symbol)

        def _get(model, pk):
            name = getattr(model, "__name__", str(model))
            if "StrategyInstance" in name:
                return instance
            if "Signal" in name:
                return signal_row
            if "Instrument" in name:
                return instrument_row
            if "StrategyVersion" in name:
                return SimpleNamespace(strategy_id=_uuid("00000000-0000-0000-0000-000000000701"))
            if name.endswith("Strategy"):
                return SimpleNamespace(slug=slug)
            return None

        store.session.get = MagicMock(side_effect=_get)
        store.session.scalar = MagicMock(return_value=None)
        store.session.merge = MagicMock()
        intent = _entry_intent(
            portfolio_id=portfolio_id,
            instance_id=instance_id,
            signal_id=signal_id,
        )
        return store, intent

    def test_robot_b_valid_signal_persists_intent(self):
        store, intent = self._store_with_signal(
            slug="opening-range-breakout",
            portfolio_id=ORB.portfolio_id,
            instance_id=ORB.instance_id,
            metadata={
                "session_date": "2026-09-11",
                "opening_range_high": 76844.368,
                "opening_range_low": 76523.795,
                "opportunity_key": OPP,
            },
        )
        saved = store.save_order_intent(intent, opportunity_key=OPP)
        assert saved.id == intent.id
        store.session.merge.assert_called_once()
        merged = store.session.merge.call_args[0][0]
        assert merged.idempotency_key == opportunity_idempotency_key(
            ORB.instance_id, OPP
        )

    def test_same_orb_opportunity_blocked_on_repeat(self):
        store, intent = self._store_with_signal(
            slug="opening-range-breakout",
            portfolio_id=ORB.portfolio_id,
            instance_id=ORB.instance_id,
            metadata={"opportunity_key": OPP},
        )
        existing = SimpleNamespace(id=_uuid(new_id()))
        store._order_intent_to_domain = MagicMock(return_value="existing-intent")
        store.session.scalar = MagicMock(return_value=existing)

        result = store.save_order_intent(intent, opportunity_key=OPP)
        assert result == "existing-intent"
        store.session.merge.assert_not_called()

    def test_restart_same_orb_opportunity_still_blocked(self):
        store, intent = self._store_with_signal(
            slug="opening-range-breakout",
            portfolio_id=ORB.portfolio_id,
            instance_id=ORB.instance_id,
            metadata={"opportunity_key": OPP},
        )
        existing = SimpleNamespace(id=_uuid(new_id()))
        store._order_intent_to_domain = MagicMock(return_value="restart-blocked")
        store.session.scalar = MagicMock(return_value=existing)

        result = store.save_order_intent(intent, opportunity_key=OPP)
        assert result == "restart-blocked"

    def test_new_orb_opportunity_allowed(self):
        new_opp = orb_opportunity_key(
            symbol="BTCUSD",
            session_date="2026-09-12",
            direction="long",
            range_high=Decimal("77000"),
            range_low=Decimal("76500"),
        )
        store, intent = self._store_with_signal(
            slug="opening-range-breakout",
            portfolio_id=ORB.portfolio_id,
            instance_id=ORB.instance_id,
            metadata={"opportunity_key": new_opp},
        )
        saved = store.save_order_intent(intent, opportunity_key=new_opp)
        assert saved.id == intent.id
        merged = store.session.merge.call_args[0][0]
        assert merged.idempotency_key == opportunity_idempotency_key(
            ORB.instance_id, new_opp
        )

    def test_robot_a_intent_persists(self):
        store, intent = self._store_with_signal(
            slug="gold-trend-pullback",
            portfolio_id=ROBOT_A.portfolio_id,
            instance_id=ROBOT_A.instance_id,
            metadata={"opportunity_key": PULLBACK_OPP},
            symbol="GBPJPY",
        )
        saved = store.save_order_intent(intent, opportunity_key=PULLBACK_OPP)
        assert saved.id == intent.id
        merged = store.session.merge.call_args[0][0]
        assert merged.idempotency_key == opportunity_idempotency_key(
            ROBOT_A.instance_id, PULLBACK_OPP
        )

    def test_robot_a_same_setup_blocked(self):
        store, intent = self._store_with_signal(
            slug="gold-trend-pullback",
            portfolio_id=ROBOT_A.portfolio_id,
            instance_id=ROBOT_A.instance_id,
            metadata={"opportunity_key": PULLBACK_OPP},
            symbol="GBPJPY",
        )
        store._order_intent_to_domain = MagicMock(return_value="blocked-a")
        store.session.scalar = MagicMock(return_value=SimpleNamespace())

        assert store.save_order_intent(intent, opportunity_key=PULLBACK_OPP) == "blocked-a"

    def test_no_legacy_entry_key_used(self):
        ts = datetime(2026, 9, 11, 12, 0, tzinfo=timezone.utc)
        canonical = _intent_idempotency_key(
            ORB.instance_id, ts, "long", opportunity_key=OPP
        )
        legacy = legacy_idempotency_key(ORB.instance_id, ts, "long")
        assert canonical == opportunity_idempotency_key(ORB.instance_id, OPP)
        assert canonical != legacy


def test_strategy_freshness_exposes_error_not_paused():
    from datetime import timedelta
    from unittest.mock import patch

    now = datetime(2026, 9, 11, 14, 0, tzinfo=timezone.utc)
    store = MagicMock()
    store.get_settings_dict.return_value = {
        "worker_status:strategy_runner": {
            "status": "error",
            "error": "'StrategyInstance' object has no attribute 'strategy_slug'",
            "last_evaluation_at": (now - timedelta(minutes=5)).isoformat(),
            "jobs_pending": 0,
        },
        "worker_status:data_fetcher": {"status": "healthy"},
    }
    store.get_instrument_by_symbol.return_value = MagicMock(
        id="00000000-0000-4000-8000-000000000001"
    )
    with patch(
        "quantara_engine.persistence.batch_summary.batch_latest_candle_timestamps",
        return_value={},
    ):
        summary = strategy_freshness_summary(store, now)
    assert summary["status"] == "error"
    assert summary["error"]
    assert summary["healthy"] is False
    assert summary["stalled"] is False


def test_hebrew_error_label_distinct_from_paused():
    import json
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[3]
    he_path = repo_root / "apps" / "web" / "messages" / "he.json"
    payload = json.loads(he_path.read_text(encoding="utf-8"))
    home = payload["home"]
    assert home["strategy_error"] == "שגיאה באסטרטגיה"
    assert home["strategy_degraded"] == "אסטרטגיה מושהית"
    assert home["strategy_error"] != home["strategy_degraded"]
