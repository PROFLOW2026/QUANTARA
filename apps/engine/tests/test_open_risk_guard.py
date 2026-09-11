"""Open-risk guard scope and global asset limit tests."""

from __future__ import annotations

from decimal import Decimal

from quantara_engine.domain.types import Direction, Instrument, Portfolio, PortfolioStatus, Position, PositionStatus, StrategyInstance
from quantara_engine.risk.open_risk_guard import (
    DEFAULT_OPEN_RISK_LIMITS,
    OpenRiskLimits,
    evaluate_global_asset_open_risk,
    evaluate_open_risk_guard,
)


def _portfolio(equity: str = "2000") -> Portfolio:
    return Portfolio(
        id="p1",
        name="t",
        mode="paper",
        initial_capital=Decimal(equity),
        balance=Decimal(equity),
        equity=Decimal(equity),
        status=PortfolioStatus.ACTIVE,
        peak_equity=Decimal(equity),
    )


def _inst() -> Instrument:
    return Instrument(id="gbp", symbol="GBPJPY", name="GBP/JPY", asset_class="forex")


def _si() -> StrategyInstance:
    return StrategyInstance(
        id="si1",
        portfolio_id="p1",
        strategy_version_id="sv",
        strategy_slug="opening-range-breakout",
        strategy_version="1.0.0",
        instrument_id="gbp",
        timeframe="5m",
        risk_profile_id="rp",
    )


def _pos(risk: str, *, pid: str = "p1", si: str = "si1") -> Position:
    return Position(
        id=f"pos-{risk}-{pid}",
        portfolio_id=pid,
        strategy_instance_id=si,
        instrument_id="gbp",
        direction=Direction.LONG,
        quantity=Decimal("1000"),
        entry_price=Decimal("200"),
        current_price=Decimal("200"),
        stop_loss=Decimal("198"),
        take_profit=Decimal("204"),
        actual_risk_amount=Decimal(risk),
        status=PositionStatus.OPEN,
    )


def test_portfolio_guard_is_per_portfolio_only():
    ok, _ = evaluate_open_risk_guard(
        portfolio=_portfolio(),
        open_positions=[_pos("20")],
        instrument=_inst(),
        strategy_instance=_si(),
        incremental_risk_usd=Decimal("20"),
    )
    assert ok  # 40/2000 = 2% — under portfolio/asset/strategy limits


def test_strategy_asset_guard_blocks_third_two_pct_trade():
    ok, reason = evaluate_open_risk_guard(
        portfolio=_portfolio(),
        open_positions=[_pos("40"), _pos("40")],
        instrument=_inst(),
        strategy_instance=_si(),
        incremental_risk_usd=Decimal("40"),
    )
    assert not ok
    assert reason is not None and "STRATEGY" in reason


def test_global_guard_blocks_asset_wide_pileup():
    global_positions = [_pos("200", pid=f"p{i}", si=f"si{i}") for i in range(20)]
    ok, reason = evaluate_global_asset_open_risk(
        global_open_positions_on_asset=global_positions,
        incremental_risk_usd=Decimal("2000"),
        asset_allocated_equity_usd=Decimal("40000"),
        limits=OpenRiskLimits(
            max_portfolio_open_risk_pct=Decimal("10"),
            max_asset_open_risk_pct=Decimal("6"),
            max_strategy_asset_open_risk_pct=Decimal("4"),
            max_global_asset_open_risk_pct=Decimal("3"),
        ),
    )
    assert not ok
    assert reason is not None and "GLOBAL" in reason


def test_global_guard_allows_five_tier_burst():
    # Five ORB tiers on one breakout: 5+10+20+30+40 = 105 on $40k asset-allocated equity
    tiers = [Decimal("5"), Decimal("10"), Decimal("20"), Decimal("30"), Decimal("40")]
    open_positions: list[Position] = []
    for i, risk in enumerate(tiers[:-1]):
        open_positions.append(_pos(str(risk), pid=f"p{i}", si=f"si{i}"))
    ok, reason = evaluate_global_asset_open_risk(
        global_open_positions_on_asset=open_positions,
        incremental_risk_usd=tiers[-1],
        asset_allocated_equity_usd=Decimal("40000"),
        limits=OpenRiskLimits(
            max_portfolio_open_risk_pct=Decimal("10"),
            max_asset_open_risk_pct=Decimal("6"),
            max_strategy_asset_open_risk_pct=Decimal("4"),
            max_global_asset_open_risk_pct=Decimal("3"),
        ),
    )
    assert ok, reason


def test_global_guard_default_enforces_two_percent():
    assert DEFAULT_OPEN_RISK_LIMITS.max_global_asset_open_risk_pct == Decimal("2")
    ok, reason = evaluate_global_asset_open_risk(
        global_open_positions_on_asset=[_pos("600", pid=f"p{i}", si=f"si{i}") for i in range(13)],
        incremental_risk_usd=Decimal("50"),
        asset_allocated_equity_usd=Decimal("32000"),
        limits=DEFAULT_OPEN_RISK_LIMITS,
    )
    assert not ok
    assert reason is not None and "GLOBAL" in reason
