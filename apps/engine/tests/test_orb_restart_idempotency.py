"""ORB canonical opportunity idempotency — restart-safe, five-tier cap."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock

from quantara_engine.competition.orb_constants import ORB_COMPETITION_PORTFOLIOS
from quantara_engine.domain.types import Direction, SignalAction
from quantara_engine.risk.opportunity import (
    opportunity_idempotency_key,
    orb_opportunity_key,
)
from quantara_engine.risk.engine import RiskEngine, RiskEvaluationInput
from quantara_engine.domain.types import Signal, Position, PositionStatus, Candle


OPP = orb_opportunity_key(
    symbol="BTCUSD",
    session_date="2026-09-11",
    direction="long",
    range_high=Decimal("76844.368"),
    range_low=Decimal("76523.795"),
)


def _legacy_key(instance_id: str, ts: datetime, direction: str) -> str:
    raw = f"{instance_id}:{ts.isoformat()}:{direction}"
    return hashlib.sha256(raw.encode()).hexdigest()


def _canonical_key(instance_id: str) -> str:
    return opportunity_idempotency_key(instance_id, OPP)


def test_canonical_key_differs_from_legacy_per_candle():
    instance_id = ORB_COMPETITION_PORTFOLIOS[0].instance_id
    ts = datetime(2026, 9, 11, 11, 0, tzinfo=timezone.utc)
    assert _canonical_key(instance_id) != _legacy_key(instance_id, ts, "long")


def test_same_breakout_repeated_denied_after_first_intent():
    engine = RiskEngine()
    store = MagicMock()
    store.opportunity_consumed.return_value = True
    signal = Signal(
        action=SignalAction.BUY,
        reason="breakout_long_confirmed",
        suggested_sl=Decimal("76000"),
        suggested_tp=Decimal("78000"),
        metadata={
            "session_date": "2026-09-11",
            "opening_range_high": 76844.368,
            "opening_range_low": 76523.795,
            "opportunity_key": OPP,
        },
    )
    candle = Candle(
        instrument_id="btc",
        timeframe="5m",
        timestamp=datetime(2026, 9, 11, 11, 0, tzinfo=timezone.utc),
        open=Decimal("77000"),
        high=Decimal("77100"),
        low=Decimal("76900"),
        close=Decimal("77050"),
    )
    instance = MagicMock()
    instance.id = ORB_COMPETITION_PORTFOLIOS[0].instance_id
    instance.strategy_slug = "opening-range-breakout"
    instance.timeframe = "5m"
    from quantara_engine.domain.types import Portfolio, PortfolioStatus, Mode

    portfolio = Portfolio(
        id=ORB_COMPETITION_PORTFOLIOS[0].portfolio_id,
        name="ORB",
        mode=Mode.PAPER,
        initial_capital=Decimal("2000"),
        balance=Decimal("2000"),
        equity=Decimal("2000"),
        peak_equity=Decimal("2000"),
        status=PortfolioStatus.ACTIVE,
    )
    instrument = MagicMock()
    instrument.symbol = "BTCUSD"
    instrument.id = "inst-btc"
    from quantara_engine.domain.types import RiskProfile
    from quantara_engine.portfolio.currency import FxRateTable

    profile = RiskProfile(
        id="rp1",
        slug="balanced",
        name="Balanced",
        risk_per_trade_pct=Decimal("1"),
        max_open_positions=1,
        max_total_exposure_pct=Decimal("100"),
        daily_loss_limit_pct=Decimal("3"),
        max_drawdown_pct=Decimal("10"),
    )
    fx = FxRateTable.usd_only()

    denied = 0
    for _ in range(100):
        decision = engine.evaluate(
            RiskEvaluationInput(
                signal=signal,
                strategy_instance=instance,
                portfolio=portfolio,
                open_positions=[],
                risk_profile=profile,
                current_candle=candle,
                instrument=instrument,
                fx_rates=fx,
                store=store,
            )
        )
        if not decision.approved:
            denied += 1
    assert denied == 100
    assert store.opportunity_consumed.call_count == 100


def test_five_tiers_share_opportunity_one_intent_per_tier():
    keys = {
        opportunity_idempotency_key(p.instance_id, OPP)
        for p in ORB_COMPETITION_PORTFOLIOS
        if p.symbol == "BTCUSD"
    }
    assert len(keys) == 5


def test_intent_idempotency_uses_canonical_not_legacy():
    from quantara_engine.persistence.store import _intent_idempotency_key

    instance_id = ORB_COMPETITION_PORTFOLIOS[0].instance_id
    ts = datetime(2026, 9, 11, 11, 0, tzinfo=timezone.utc)
    canonical = _intent_idempotency_key(instance_id, ts, "long", opportunity_key=OPP)
    legacy = _intent_idempotency_key(instance_id, ts, "long", opportunity_key=None)
    assert canonical == _canonical_key(instance_id)
    assert canonical != legacy


def test_opposite_direction_breakout_allowed():
    engine = RiskEngine()
    store = MagicMock()
    store.opportunity_consumed.return_value = False
    store._position_to_domain = MagicMock(side_effect=lambda r: r)
    store._hydrate_position_strategy_versions = MagicMock()
    store.session.scalars.return_value.all.return_value = []
    store.session.scalar.return_value = Decimal("320000")
    short_opp = orb_opportunity_key(
        symbol="BTCUSD",
        session_date="2026-09-11",
        direction="short",
        range_high=Decimal("76844.368"),
        range_low=Decimal("76523.795"),
    )
    signal = Signal(
        action=SignalAction.SELL,
        reason="breakout_short_confirmed",
        suggested_sl=Decimal("77500"),
        suggested_tp=Decimal("75500"),
        metadata={
            "session_date": "2026-09-11",
            "opening_range_high": 76844.368,
            "opening_range_low": 76523.795,
            "opportunity_key": short_opp,
        },
    )
    from quantara_engine.domain.types import Mode, Portfolio, PortfolioStatus, RiskProfile, Instrument
    from quantara_engine.portfolio.currency import FxRateTable

    decision = engine.evaluate(
        RiskEvaluationInput(
            signal=signal,
            strategy_instance=MagicMock(
                id=ORB_COMPETITION_PORTFOLIOS[0].instance_id,
                strategy_slug="opening-range-breakout",
                timeframe="5m",
            ),
            portfolio=Portfolio(
                id=ORB_COMPETITION_PORTFOLIOS[0].portfolio_id,
                name="ORB",
                mode=Mode.PAPER,
                initial_capital=Decimal("2000"),
                balance=Decimal("2000"),
                equity=Decimal("2000"),
                peak_equity=Decimal("2000"),
                status=PortfolioStatus.ACTIVE,
            ),
            open_positions=[
                Position(
                    id="p1",
                    portfolio_id=ORB_COMPETITION_PORTFOLIOS[0].portfolio_id,
                    strategy_instance_id=ORB_COMPETITION_PORTFOLIOS[0].instance_id,
                    instrument_id="btc",
                    direction=Direction.LONG,
                    quantity=Decimal("0.01"),
                    entry_price=Decimal("77000"),
                    stop_loss=Decimal("76000"),
                    take_profit=Decimal("78000"),
                    current_price=Decimal("77000"),
                    status=PositionStatus.OPEN,
                )
            ],
            risk_profile=RiskProfile(
                id="rp1",
                slug="balanced",
                name="Balanced",
                risk_per_trade_pct=Decimal("1"),
                max_open_positions=3,
                max_total_exposure_pct=Decimal("100"),
                daily_loss_limit_pct=Decimal("3"),
                max_drawdown_pct=Decimal("10"),
            ),
            current_candle=Candle(
                instrument_id="btc",
                timeframe="5m",
                timestamp=datetime(2026, 9, 11, 12, 0, tzinfo=timezone.utc),
                open=Decimal("76500"),
                high=Decimal("76600"),
                low=Decimal("76400"),
                close=Decimal("76500"),
            ),
            instrument=Instrument(
                id="btc",
                symbol="BTCUSD",
                name="BTC",
                asset_class="crypto",
                quote_currency="USD",
            ),
            fx_rates=FxRateTable.usd_only(),
            store=store,
        )
    )
    assert decision.approved, decision.denial_reason
