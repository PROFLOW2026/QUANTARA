"""ORB-specific backtest analytics — weekday and opening-range width research."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from quantara_engine.domain.types import Direction, Trade
from quantara_engine.market_data.sessions import US_EASTERN

ET = US_EASTERN
WEEKDAY_ORDER = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday")

# Fixed baseline buckets (not optimized from backtest results).
RANGE_WIDTH_BUCKETS: tuple[tuple[str, float, float | None], ...] = (
    ("narrow", 0.0, 0.25),       # width_pct < 0.25%
    ("medium", 0.25, 0.60),      # 0.25% <= width_pct < 0.60%
    ("wide", 0.60, None),        # width_pct >= 0.60%
)


@dataclass(frozen=True)
class OrbTradeContext:
    trade: Trade
    session_date: str | None
    weekday: str
    opening_range_high: float | None
    opening_range_low: float | None
    opening_range_width: float | None
    opening_range_width_pct: float | None
    range_bucket: str | None
    r_multiple: float | None


def _to_et(ts: datetime) -> datetime:
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(ET)


def _weekday_name(ts: datetime) -> str:
    return _to_et(ts).strftime("%A")


def _width_pct(high: float, low: float) -> float:
    mid = (high + low) / 2.0
    if mid <= 0:
        return 0.0
    return (high - low) / mid * 100.0


def _range_bucket(width_pct: float | None) -> str | None:
    if width_pct is None:
        return None
    for label, low, high in RANGE_WIDTH_BUCKETS:
        if high is None and width_pct >= low:
            return label
        if high is not None and low <= width_pct < high:
            return label
    return None


def _trade_r_multiple(trade: Trade) -> float | None:
    risk = trade.actual_risk_amount or trade.target_risk_amount
    if not risk or risk <= 0:
        return None
    return float(trade.realized_pnl / risk)


def _signal_direction(action: str) -> str | None:
    action = action.lower()
    if action == "buy":
        return Direction.LONG.value
    if action == "sell":
        return Direction.SHORT.value
    return None


def match_trades_to_signals(
    trades: list[Trade],
    signals: list[dict],
    *,
    timeframe_minutes: int = 5,
) -> list[OrbTradeContext]:
    """Pair each trade with its entry signal metadata (next-candle-open execution)."""
    entry_signals = [
        s
        for s in signals
        if _signal_direction(str(s.get("action", ""))) is not None
        and s.get("metadata")
    ]
    entry_signals.sort(key=lambda s: s["candle_timestamp"])

    contexts: list[OrbTradeContext] = []
    used_indices: set[int] = set()
    for trade in sorted(trades, key=lambda t: t.opened_at):
        meta = None
        for idx, signal in enumerate(entry_signals):
            if idx in used_indices:
                continue
            sig_dir = _signal_direction(str(signal.get("action", "")))
            if sig_dir != trade.direction.value:
                continue
            candle_ts = signal["candle_timestamp"]
            if candle_ts.tzinfo is None:
                candle_ts = candle_ts.replace(tzinfo=timezone.utc)
            expected_open = candle_ts + timedelta(minutes=timeframe_minutes)
            opened = trade.opened_at
            if opened.tzinfo is None:
                opened = opened.replace(tzinfo=timezone.utc)
            delta_sec = abs((opened - expected_open).total_seconds())
            if delta_sec <= 60:
                meta = signal.get("metadata") or {}
                used_indices.add(idx)
                break

        high = low = width = width_pct = None
        session_date = None
        weekday = _weekday_name(trade.opened_at)
        if meta:
            session_date = meta.get("session_date")
            if session_date:
                weekday = datetime.fromisoformat(session_date).strftime("%A")
            high = meta.get("opening_range_high")
            low = meta.get("opening_range_low")
            width = meta.get("opening_range_size")
            if high is not None and low is not None:
                width = float(width) if width is not None else float(high) - float(low)
                width_pct = _width_pct(float(high), float(low))

        contexts.append(
            OrbTradeContext(
                trade=trade,
                session_date=session_date,
                weekday=weekday,
                opening_range_high=float(high) if high is not None else None,
                opening_range_low=float(low) if low is not None else None,
                opening_range_width=float(width) if width is not None else None,
                opening_range_width_pct=width_pct,
                range_bucket=_range_bucket(width_pct),
                r_multiple=_trade_r_multiple(trade),
            )
        )

    return contexts


def _aggregate_group(items: list[OrbTradeContext]) -> dict:
    trades = [c.trade for c in items]
    total = len(trades)
    wins = [t for t in trades if t.realized_pnl > 0]
    losses = [t for t in trades if t.realized_pnl <= 0]
    win_count = len(wins)
    loss_count = len(losses)
    net_pnl = float(sum(t.realized_pnl for t in trades))
    win_rate = (win_count / total * 100.0) if total else 0.0

    r_values = [c.r_multiple for c in items if c.r_multiple is not None]
    average_r = sum(r_values) / len(r_values) if r_values else None

    avg_win = float(sum(t.realized_pnl for t in wins) / len(wins)) if wins else 0.0
    avg_loss = float(sum(t.realized_pnl for t in losses) / len(losses)) if losses else 0.0
    loss_rate = loss_count / total if total else 0.0
    win_rate_frac = win_count / total if total else 0.0
    expectancy = win_rate_frac * avg_win + loss_rate * avg_loss

    sum_wins = sum(t.realized_pnl for t in wins)
    sum_losses = sum(t.realized_pnl for t in losses)
    if sum_losses != 0:
        profit_factor = float(sum_wins / abs(sum_losses))
    elif sum_wins:
        profit_factor = None
    else:
        profit_factor = 0.0

    return {
        "trades": total,
        "wins": win_count,
        "losses": loss_count,
        "win_rate": round(win_rate, 2),
        "net_pnl": round(net_pnl, 2),
        "average_R": round(average_r, 3) if average_r is not None else None,
        "expectancy": round(expectancy, 2),
        "profit_factor": round(profit_factor, 2) if profit_factor is not None else None,
    }


def compute_orb_weekday_breakdown(contexts: list[OrbTradeContext]) -> list[dict]:
    by_day: dict[str, list[OrbTradeContext]] = {d: [] for d in WEEKDAY_ORDER}
    for ctx in contexts:
        if ctx.weekday in by_day:
            by_day[ctx.weekday].append(ctx)
    rows = []
    for day in WEEKDAY_ORDER:
        row = _aggregate_group(by_day[day])
        row["weekday"] = day
        rows.append(row)
    return rows


def compute_orb_range_width_breakdown(contexts: list[OrbTradeContext]) -> list[dict]:
    by_bucket: dict[str, list[OrbTradeContext]] = {label: [] for label, _, _ in RANGE_WIDTH_BUCKETS}
    for ctx in contexts:
        if ctx.range_bucket and ctx.range_bucket in by_bucket:
            by_bucket[ctx.range_bucket].append(ctx)
    rows = []
    for label, low, high in RANGE_WIDTH_BUCKETS:
        row = _aggregate_group(by_bucket[label])
        row["range_bucket"] = label
        row["width_pct_min"] = low
        row["width_pct_max"] = high
        rows.append(row)
    return rows


def orb_trade_details(contexts: list[OrbTradeContext]) -> list[dict]:
    return [
        {
            "trade_id": ctx.trade.id,
            "session_date": ctx.session_date,
            "weekday": ctx.weekday,
            "opening_range_high": ctx.opening_range_high,
            "opening_range_low": ctx.opening_range_low,
            "opening_range_width": ctx.opening_range_width,
            "opening_range_width_pct": (
                round(ctx.opening_range_width_pct, 4) if ctx.opening_range_width_pct is not None else None
            ),
            "range_bucket": ctx.range_bucket,
            "realized_pnl": float(ctx.trade.realized_pnl),
            "r_multiple": round(ctx.r_multiple, 3) if ctx.r_multiple is not None else None,
        }
        for ctx in contexts
    ]


def attach_orb_analytics(
    metrics: dict,
    trades: list[Trade],
    signals: list[dict],
) -> dict:
    """Merge ORB segmented analytics into backtest metrics JSON."""
    contexts = match_trades_to_signals(trades, signals)
    enriched = dict(metrics)
    enriched["orb_analytics"] = {
        "weekday_breakdown": compute_orb_weekday_breakdown(contexts),
        "range_width_breakdown": compute_orb_range_width_breakdown(contexts),
        "range_width_buckets": [
            {"label": label, "width_pct_min": low, "width_pct_max": high}
            for label, low, high in RANGE_WIDTH_BUCKETS
        ],
        "trade_details": orb_trade_details(contexts),
    }
    return enriched
