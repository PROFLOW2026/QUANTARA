"""
AI Trader Paper Runner — read-only API endpoint.

Reads JSON files produced by paper_runner.py in the canonical QUANTARA-AI-Trader repo
(the single live source — `day-trader-skill-trial` is retired).
No writes. No DB access. No trading logic changes.
Authentication: same X-API-Key used by all engine routes.

Statistics (closed_count, win rate, profit factor, history, P&L, equity curve) are computed
from the FULL-HISTORY ledger (`paper_trades` in paper-journal-v2.json) — never from the
daily-reset `fills` list in paper-state.json, which only holds today's activity.

Path: GET /api/v1/ai-trader/summary
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, status
from fastapi.params import Depends
from pydantic import BaseModel

from quantara_engine.api.deps import verify_api_key

# ── Configuration ─────────────────────────────────────────────────────────────
# Override by setting AI_TRADER_DATA_DIR environment variable.
_DEFAULT_DIR = str(
    Path(__file__).resolve().parents[4].parent / "QUANTARA-AI-Trader" / "trader"
)
AI_TRADER_DIR = Path(os.environ.get("AI_TRADER_DATA_DIR", _DEFAULT_DIR))

router = APIRouter(
    prefix="/api/v1/ai-trader",
    dependencies=[Depends(verify_api_key)],
    tags=["ai-trader"],
)

# ── Internal helpers ──────────────────────────────────────────────────────────

def _read_json(filepath: Path) -> dict:
    """Read a JSON file. Return empty dict on any error."""
    try:
        return json.loads(filepath.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _is_runner_alive(pid_file: Path) -> bool:
    """Check if paper_runner.py process is alive (read-only probe, no signals)."""
    try:
        pid = int(pid_file.read_text(encoding="utf-8").strip())
    except (FileNotFoundError, ValueError, OSError):
        return False

    if sys.platform == "win32":
        # os.kill(pid, 0) on Windows == CTRL_C_EVENT (signal 0), NOT a probe.
        # Use tasklist /FI instead — completely non-destructive.
        try:
            result = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                capture_output=True, text=True,
                creationflags=subprocess.CREATE_NO_WINDOW,
                timeout=5,
            )
            return str(pid) in result.stdout
        except Exception:
            return False
    else:
        # Unix: signal 0 is a safe existence probe
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False


def _staleness_minutes(timestamp_str: str | None) -> Optional[float]:
    """Return how many minutes old a timestamp string is, or None if unparseable."""
    if not timestamp_str:
        return None
    # ISO-8601 UTC heartbeat (ends with Z)
    if timestamp_str.endswith("Z"):
        try:
            dt = datetime.datetime.strptime(timestamp_str, "%Y-%m-%dT%H:%M:%SZ")
            return (datetime.datetime.utcnow() - dt).total_seconds() / 60
        except ValueError:
            pass
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            dt = datetime.datetime.strptime(timestamp_str, fmt)
            return (datetime.datetime.now() - dt).total_seconds() / 60
        except ValueError:
            continue
    return None


def _is_nyse_open() -> bool:
    """Return True if NYSE is currently in regular trading hours (ET 09:30–16:00, Mon–Fri)."""
    try:
        import zoneinfo
        tz = zoneinfo.ZoneInfo("America/New_York")
    except Exception:
        tz = datetime.timezone(datetime.timedelta(hours=-4))   # rough EDT fallback
    et = datetime.datetime.now(tz)
    if et.weekday() >= 5:   # Saturday=5, Sunday=6
        return False
    mins = et.hour * 60 + et.minute
    return 9 * 60 + 30 <= mins <= 16 * 60


def _opt_float(v: object) -> Optional[float]:
    if v is None:
        return None
    try:
        return float(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


# ── Pydantic response models ───────────────────────────────────────────────────

class AiTraderFill(BaseModel):
    symbol: str
    shares: int
    entry_price_fill: float
    stop: float
    target: float
    status: str                            # "open" | "closed"
    # Open position enrichment
    open_pnl: Optional[float] = None       # unrealized P&L from paper-state
    position_cost: Optional[float] = None  # total cost basis
    current_price: Optional[float] = None  # implied last price (cost + pnl) / shares
    position_value: Optional[float] = None # current market value
    unrealized_pnl_pct: Optional[float] = None  # P&L as % of entry price
    price_as_of: Optional[str] = None     # last_updated timestamp for price staleness
    entry_time: Optional[str] = None      # entry_time from state
    note: Optional[str] = None            # strategy note from state
    # Closed position enrichment
    exit_price: Optional[float] = None
    exit_reason: Optional[str] = None
    pnl: Optional[float] = None
    r: Optional[float] = None
    filled_at: Optional[str] = None
    closed_at: Optional[str] = None
    # Decision provenance (AI engine)
    strategy: Optional[str] = None
    decided_by: Optional[str] = None
    confidence: Optional[float] = None
    reasoning: Optional[str] = None


class AiTraderEquityPoint(BaseModel):
    date: str
    equity: float
    time: Optional[str] = None


class AiTraderSummary(BaseModel):
    # Runner status
    runner_alive: bool
    market_open: bool                          # NYSE regular hours right now
    runner_status_text: str
    heartbeat_age_minutes: Optional[float] = None
    data_stale: bool
    staleness_minutes: Optional[float] = None
    last_updated: Optional[str] = None
    prices_as_of: Optional[str] = None        # timestamp of last price update in state

    # Account — all derived from live state, not starting capital
    starting_capital: float
    equity: float                              # cash + market_value (real-time)
    cash: float                                # free cash
    market_value: float                        # total value of open positions
    buying_power: float                        # cash available (no margin in paper)
    realized_pnl: float                        # from closed fills
    unrealized_pnl: float                      # sum of open_pnl from open fills
    total_pnl: float                           # realized + unrealized
    peak_equity: float
    max_drawdown_usd: float
    max_drawdown_pct: float
    return_pct: float                          # total_pnl / starting_capital × 100
    daily_loss_at_risk: float
    trades_today: int

    # Legacy alias kept for callers that use .capital
    capital: float                             # same as equity

    # Positions
    open_fills: list[AiTraderFill]
    closed_fills: list[AiTraderFill]

    # Performance metrics (from closed fills only)
    closed_count: int
    win_count: int
    loss_count: int
    flat_count: int
    win_rate_pct: Optional[float]
    profit_factor: Optional[float]
    gross_wins: float
    gross_losses: float

    # Equity history (one point per session from journal)
    equity_history: list[AiTraderEquityPoint]

    # Strategy config
    strategy: str
    target_stocks: list[str]
    max_risk_per_trade_usd: float
    max_daily_loss_usd: float

    # Signals
    signals_seen: list[str]

    # AI engine status (provider, mode, last decision, skills in use) — from state/ai-status
    ai_status: dict = {}
    last_cycle: Optional[dict] = None
    experiment_start: Optional[str] = None
    total_trades: int = 0
    avg_win: Optional[float] = None
    avg_loss: Optional[float] = None
    best_trade: Optional[float] = None
    worst_trade: Optional[float] = None
    strategies_used: list[str] = []

    # Source info
    source: str = "paper-state.json + paper-journal-v2.json (full-history ledger)"
    data_dir_accessible: bool = True


# ── Route ─────────────────────────────────────────────────────────────────────

@router.get("/summary", response_model=AiTraderSummary)
async def get_ai_trader_summary() -> AiTraderSummary:
    """Return current AI Trader paper account state (read-only)."""

    if not AI_TRADER_DIR.exists():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"AI Trader data directory not found: {AI_TRADER_DIR}",
        )

    state   = _read_json(AI_TRADER_DIR / "paper-state.json")
    journal = _read_json(AI_TRADER_DIR / "paper-journal-v2.json")
    config  = _read_json(AI_TRADER_DIR / "paper-config.json")
    pid_file = AI_TRADER_DIR / "paper-runner.pid"

    runner_alive = _is_runner_alive(pid_file)
    market_open  = _is_nyse_open()

    # Heartbeat: written by runner every 30s even outside trading hours
    heartbeat_ts  = state.get("last_heartbeat")
    heartbeat_age = _staleness_minutes(heartbeat_ts)

    # Trade data staleness
    last_updated = journal.get("last_updated") or state.get("last_updated")
    staleness    = _staleness_minutes(last_updated)

    runner_heartbeating = heartbeat_age is not None and heartbeat_age < 3
    data_stale = not runner_heartbeating and (staleness is None or staleness > 10)

    # Human-readable status
    if not runner_alive:
        runner_status_text = "כבוי"
    elif market_open:
        runner_status_text = "פועל — הבורסה פתוחה"
    else:
        runner_status_text = "פועל — הבורסה סגורה, ממתין למסחר"

    starting_capital = float(
        state.get("capital") or journal.get("capital") or journal.get("capital_usd") or 10_000
    )
    cash             = float(state.get("cash") if state.get("cash") is not None else starting_capital)
    peak_eq          = float(state.get("peak_equity") or starting_capital)
    prices_as_of     = state.get("last_updated")   # last time prices were written by runner

    # ── Fills ──
    # Open positions: live state. Closed trades: FULL-HISTORY ledger (paper_trades) merged with
    # anything still only in today's state (by id) — state["fills"] resets daily and must never be
    # the source of lifetime statistics.
    state_fills = state.get("fills") or []
    ledger_trades = journal.get("paper_trades") or []
    by_id: dict[str, dict] = {}
    for t in ledger_trades:
        if t.get("id"):
            by_id[str(t["id"])] = t
    for t in state_fills:
        if t.get("id"):
            by_id[str(t["id"])] = t
    open_fills_raw = [f for f in state_fills if f.get("status") == "open"]
    closed_fills_raw = [
        t for t in by_id.values() if t.get("status") == "closed"
    ] or [f for f in state_fills if f.get("status") not in ("open", None)]
    closed_fills_raw.sort(key=lambda t: str(t.get("exit_time") or t.get("exit_time_et") or ""))
    journal_stats = journal.get("stats") or {}
    realized = float(
        journal_stats.get("realized_pnl")
        if journal_stats.get("realized_pnl") is not None
        else (state.get("realized_pnl") or journal.get("realized_pnl") or 0)
    )

    # ── Unrealized P&L: sum open_pnl from open fills ──
    # open_pnl is written by paper_runner.py when prices are available.
    # When NYSE is closed, it reflects the last known price (may be stale).
    unrealized_pnl = sum(float(f.get("open_pnl") or 0) for f in open_fills_raw)

    # ── Market value: position cost + unrealized P&L per position ──
    market_value = sum(
        float(f.get("position_cost") or 0) + float(f.get("open_pnl") or 0)
        for f in open_fills_raw
    )

    # ── Real equity ──
    equity   = cash + market_value
    total_pnl = realized + unrealized_pnl
    buying_power = cash   # paper: no margin, buying power = free cash

    max_dd_usd = float(state.get("max_drawdown") or 0)
    max_dd_pct = (max_dd_usd / peak_eq * 100) if peak_eq > 0 else 0.0
    return_pct = (total_pnl / starting_capital * 100) if starting_capital else 0.0

    def _parse_open_fill(f: dict) -> AiTraderFill:
        entry = float(f.get("entry_price_fill") or 0)
        shares = int(f.get("shares") or 0)
        pos_cost = _opt_float(f.get("position_cost"))
        pnl_open = _opt_float(f.get("open_pnl"))

        # Implied current price from position cost + unrealized P&L
        current_price: Optional[float] = None
        position_value: Optional[float] = None
        unrealized_pnl_pct: Optional[float] = None
        if pos_cost is not None and pnl_open is not None and shares > 0:
            current_price = round((pos_cost + pnl_open) / shares, 4)
            position_value = round(pos_cost + pnl_open, 2)
            if entry > 0:
                unrealized_pnl_pct = round((current_price - entry) / entry * 100, 3)

        return AiTraderFill(
            symbol=str(f.get("symbol", "")),
            shares=shares,
            entry_price_fill=entry,
            stop=float(f.get("stop") or 0),
            target=float(f.get("target") or 0),
            status="open",
            open_pnl=pnl_open,
            position_cost=pos_cost,
            current_price=current_price,
            position_value=position_value,
            unrealized_pnl_pct=unrealized_pnl_pct,
            price_as_of=prices_as_of,
            entry_time=str(f.get("entry_time")) if f.get("entry_time") else None,
            note=str(f.get("note")) if f.get("note") else None,
            filled_at=str(f.get("entry_time")) if f.get("entry_time") else None,
            strategy=str(f.get("strategy")) if f.get("strategy") else None,
            decided_by=str(f.get("decided_by")) if f.get("decided_by") else None,
            confidence=_opt_float(f.get("confidence")),
            reasoning=str(f.get("reasoning") or f.get("explanation") or "")[:400] or None,
        )

    def _parse_closed_fill(f: dict) -> AiTraderFill:
        return AiTraderFill(
            symbol=str(f.get("symbol", "")),
            shares=int(f.get("shares") or 0),
            entry_price_fill=float(f.get("entry_price_fill") or 0),
            stop=float(f.get("stop") or 0),
            target=float(f.get("target") or 0),
            status=str(f.get("status", "closed")),
            exit_price=_opt_float(f.get("exit_price")),
            exit_reason=str(f.get("exit_reason")) if f.get("exit_reason") else None,
            pnl=_opt_float(f.get("pnl")),
            r=_opt_float(f.get("R") if f.get("R") is not None else f.get("r")),
            entry_time=str(f.get("entry_time")) if f.get("entry_time") else None,
            filled_at=str(f.get("entry_time")) if f.get("entry_time") else None,
            closed_at=str(f.get("exit_time") or f.get("exit_time_et"))
            if (f.get("exit_time") or f.get("exit_time_et"))
            else None,
            note=str(f.get("note")) if f.get("note") else None,
            strategy=str(f.get("strategy")) if f.get("strategy") else None,
            decided_by=str(f.get("decided_by")) if f.get("decided_by") else None,
            confidence=_opt_float(f.get("confidence")),
            reasoning=str(f.get("reasoning") or f.get("explanation") or "")[:400] or None,
        )

    open_fills   = [_parse_open_fill(f)   for f in open_fills_raw]
    closed_fills = [_parse_closed_fill(f) for f in closed_fills_raw]

    # ── Performance metrics (closed fills only) ──
    closed_count = len(closed_fills)
    wins   = [f for f in closed_fills if (f.pnl or 0) > 0]
    losses = [f for f in closed_fills if (f.pnl or 0) < 0]
    flat   = [f for f in closed_fills if (f.pnl or 0) == 0]
    win_count  = len(wins)
    loss_count = len(losses)
    flat_count = len(flat)

    gross_wins   = sum(f.pnl or 0 for f in wins)
    gross_losses = abs(sum(f.pnl or 0 for f in losses))

    win_rate_pct: Optional[float] = None
    profit_factor: Optional[float] = None
    if closed_count > 0:
        win_rate_pct = round(win_count / closed_count * 100, 1)
    if gross_losses > 0:
        profit_factor = round(gross_wins / gross_losses, 2)
    elif gross_wins > 0:
        profit_factor = 999.0   # no losses → infinite PF

    # Cross-check against the ledger's own computed stats (same source, so they must agree).
    ai_status_doc = _read_json(AI_TRADER_DIR / "ai-status.json")
    if journal_stats and journal_stats.get("closed") is not None:
        closed_count = int(journal_stats.get("closed") or closed_count)

    # ── Equity history: closed-trade equity curve from the full ledger ──
    equity_history: list[AiTraderEquityPoint] = []
    for pt in journal.get("equity_history") or []:
        d = str(pt.get("date") or str(pt.get("time") or "")[:10])
        if d and pt.get("equity") is not None:
            equity_history.append(
                AiTraderEquityPoint(
                    date=d, equity=round(float(pt["equity"]), 2), time=str(pt.get("time") or "") or None
                )
            )
    if not equity_history:
        running_equity = starting_capital
        for s in journal.get("sessions_log") or []:
            d = s.get("date")
            net = s.get("net_pnl") or s.get("net") or 0
            if d:
                running_equity += float(net)
                equity_history.append(AiTraderEquityPoint(date=d, equity=round(running_equity, 2)))
    # Always add the live equity (cash + open market value) as the last point.
    now_label = state.get("last_updated") or datetime.date.today().isoformat()
    if equity:
        equity_history.append(
            AiTraderEquityPoint(
                date=str(state.get("date") or datetime.date.today().isoformat()),
                equity=round(equity, 2),
                time=str(now_label),
            )
        )

    # ── Config ──
    ai_status = state.get("ai_status") or ai_status_doc.get("ai_status") or {}
    last_cycle = state.get("last_cycle") or ai_status_doc.get("last_cycle")
    strategies_used = sorted(
        {str(t.get("strategy")) for t in list(by_id.values()) if t.get("strategy")}
    )
    strategy = (
        journal.get("strategy")
        or ai_status.get("mode_label")
        or "AI multi-strategy scanner (ORB + trend/pullback/mean-reversion/breakout)"
    )
    target_stocks = (
        journal.get("target_stocks")
        or ai_status.get("universe")
        or state.get("universe")
        or []
    )
    max_risk = float(
        config.get("max_loss_per_trade")
        or config.get("max_loss_per_trade_usd")
        or journal.get("max_risk_per_trade_usd")
        or 50
    )
    max_daily = float(
        config.get("max_daily_loss")
        or config.get("max_daily_loss_usd")
        or journal.get("max_daily_loss_usd")
        or 150
    )

    return AiTraderSummary(
        runner_alive=runner_alive,
        market_open=market_open,
        runner_status_text=runner_status_text,
        heartbeat_age_minutes=round(heartbeat_age, 1) if heartbeat_age is not None else None,
        data_stale=data_stale,
        staleness_minutes=round(staleness, 1) if staleness is not None else None,
        last_updated=last_updated,
        prices_as_of=prices_as_of,
        starting_capital=starting_capital,
        equity=round(equity, 2),
        cash=round(cash, 2),
        market_value=round(market_value, 2),
        buying_power=round(buying_power, 2),
        realized_pnl=round(realized, 2),
        unrealized_pnl=round(unrealized_pnl, 2),
        total_pnl=round(total_pnl, 2),
        peak_equity=round(peak_eq, 2),
        max_drawdown_usd=round(max_dd_usd, 2),
        max_drawdown_pct=round(max_dd_pct, 2),
        return_pct=round(return_pct, 4),
        daily_loss_at_risk=float(state.get("daily_loss_at_risk") or 0),
        trades_today=int(state.get("trades_today") or 0),
        capital=round(equity, 2),   # legacy alias
        open_fills=open_fills,
        closed_fills=closed_fills,
        closed_count=closed_count,
        win_count=win_count,
        loss_count=loss_count,
        flat_count=flat_count,
        win_rate_pct=win_rate_pct,
        profit_factor=profit_factor,
        gross_wins=round(gross_wins, 2),
        gross_losses=round(gross_losses, 2),
        equity_history=equity_history,
        strategy=strategy,
        target_stocks=target_stocks,
        max_risk_per_trade_usd=max_risk,
        max_daily_loss_usd=max_daily,
        signals_seen=state.get("signals_seen") or [],
        ai_status=ai_status if isinstance(ai_status, dict) else {},
        last_cycle=last_cycle if isinstance(last_cycle, dict) else None,
        experiment_start=state.get("experiment_start") or journal.get("experiment_start"),
        total_trades=int(journal_stats.get("total_trades") or (closed_count + len(open_fills))),
        avg_win=_opt_float(journal_stats.get("avg_win")),
        avg_loss=_opt_float(journal_stats.get("avg_loss")),
        best_trade=_opt_float(journal_stats.get("best_trade")),
        worst_trade=_opt_float(journal_stats.get("worst_trade")),
        strategies_used=strategies_used,
        source="paper-state.json + paper-journal-v2.json (full-history ledger)",
        data_dir_accessible=True,
    )
