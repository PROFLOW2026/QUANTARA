"""ORB backtest analytics — weekday and opening-range width buckets."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from quantara_engine.backtesting.orb_analytics import (
    attach_orb_analytics,
    compute_orb_range_width_breakdown,
    compute_orb_weekday_breakdown,
    match_trades_to_signals,
    _range_bucket,
)
from quantara_engine.domain.types import Direction, Trade

ET = ZoneInfo("America/New_York")


def _trade(
    *,
    trade_id: str,
    opened_at: datetime,
    pnl: Decimal,
    direction: Direction = Direction.LONG,
    risk: Decimal = Decimal("10"),
) -> Trade:
    return Trade(
        id=trade_id,
        position_id=f"pos-{trade_id}",
        portfolio_id="bt-1",
        strategy_instance_id="si-orb",
        strategy_version_id="sv-orb",
        instrument_id="spy",
        direction=direction,
        quantity=Decimal("1"),
        entry_price=Decimal("500"),
        exit_price=Decimal("502"),
        gross_pnl=pnl,
        realized_pnl=pnl,
        fees_total=Decimal("0"),
        slippage_total=Decimal("0"),
        spread_total=Decimal("0"),
        target_risk_amount=risk,
        actual_risk_amount=risk,
        exit_reason="tp",
        duration_seconds=300,
        opened_at=opened_at,
        closed_at=opened_at + timedelta(minutes=30),
    )


def _signal(session_date: str, action: str, candle_ts: datetime, high: float, low: float):
    width = high - low
    mid = (high + low) / 2
    return {
        "id": f"sig-{session_date}-{action}",
        "action": action,
        "candle_timestamp": candle_ts,
        "metadata": {
            "session_date": session_date,
            "opening_range_high": high,
            "opening_range_low": low,
            "opening_range_size": width,
            "opening_range_width_pct": width / mid * 100,
        },
    }


def test_range_bucket_deterministic_thresholds():
    assert _range_bucket(0.10) == "narrow"
    assert _range_bucket(0.25) == "medium"
    assert _range_bucket(0.59) == "medium"
    assert _range_bucket(0.60) == "wide"


def test_match_trades_to_signals_next_candle_open():
    session = "2026-03-10"
    signal_ts = datetime(2026, 3, 10, 10, 0, tzinfo=ET).astimezone(timezone.utc)
    entry_ts = datetime(2026, 3, 10, 10, 5, tzinfo=ET).astimezone(timezone.utc)
    trade = _trade(trade_id="t1", opened_at=entry_ts, pnl=Decimal("20"))
    signals = [_signal(session, "buy", signal_ts, 501.0, 499.0)]
    contexts = match_trades_to_signals([trade], signals)
    assert len(contexts) == 1
    assert contexts[0].opening_range_width == 2.0
    assert contexts[0].weekday == "Tuesday"
    assert contexts[0].range_bucket == "medium"


def test_weekday_breakdown_aggregates():
    mon_signal = datetime(2026, 3, 9, 10, 0, tzinfo=ET).astimezone(timezone.utc)
    tue_signal = datetime(2026, 3, 10, 10, 0, tzinfo=ET).astimezone(timezone.utc)
    trades = [
        _trade(
            trade_id="t1",
            opened_at=mon_signal + timedelta(minutes=5),
            pnl=Decimal("10"),
        ),
        _trade(
            trade_id="t2",
            opened_at=tue_signal + timedelta(minutes=5),
            pnl=Decimal("-5"),
        ),
    ]
    signals = [
        _signal("2026-03-09", "buy", mon_signal, 500.5, 500.0),
        _signal("2026-03-10", "buy", tue_signal, 500.3, 500.0),
    ]
    contexts = match_trades_to_signals(trades, signals)
    breakdown = compute_orb_weekday_breakdown(contexts)
    mon = next(r for r in breakdown if r["weekday"] == "Monday")
    tue = next(r for r in breakdown if r["weekday"] == "Tuesday")
    assert mon["trades"] == 1
    assert mon["wins"] == 1
    assert mon["net_pnl"] == 10.0
    assert tue["trades"] == 1
    assert tue["losses"] == 1


def test_range_width_breakdown_groups():
    signal_ts = datetime(2026, 3, 10, 10, 0, tzinfo=ET).astimezone(timezone.utc)
    entry_ts = signal_ts + timedelta(minutes=5)
    trade = _trade(trade_id="t1", opened_at=entry_ts, pnl=Decimal("15"))
    # width = 0.5 on mid ~500 => ~0.10% => narrow
    signals = [_signal("2026-03-10", "buy", signal_ts, 500.25, 499.75)]
    contexts = match_trades_to_signals([trade], signals)
    breakdown = compute_orb_range_width_breakdown(contexts)
    narrow = next(r for r in breakdown if r["range_bucket"] == "narrow")
    assert narrow["trades"] == 1
    assert narrow["wins"] == 1


def test_attach_orb_analytics_merges_into_metrics():
    signal_ts = datetime(2026, 3, 10, 10, 0, tzinfo=ET).astimezone(timezone.utc)
    trade = _trade(
        trade_id="t1",
        opened_at=signal_ts + timedelta(minutes=5),
        pnl=Decimal("8"),
    )
    signals = [_signal("2026-03-10", "buy", signal_ts, 501.0, 499.5)]
    base = {"total_trades": 1, "win_rate": 100.0}
    merged = attach_orb_analytics(base, [trade], signals)
    assert "orb_analytics" in merged
    assert len(merged["orb_analytics"]["weekday_breakdown"]) == 5
    assert len(merged["orb_analytics"]["range_width_breakdown"]) == 3
    assert merged["orb_analytics"]["trade_details"][0]["opening_range_high"] == 501.0
