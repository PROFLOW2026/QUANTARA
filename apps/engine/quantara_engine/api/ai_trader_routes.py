"""
AI Trader Paper Runner — read-only API endpoint.

Reads JSON files produced by paper_runner.py (day-trader-skill-trial).
No writes. No DB access. No trading logic changes.
Authentication: same X-API-Key used by all engine routes.

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
_DEFAULT_DIR = (
    r"C:\Users\ERAN YOSEF\Desktop\final projects"
    r"\FINAL-WEB\day-trader-skill-trial\trader"
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
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            dt = datetime.datetime.strptime(timestamp_str, fmt)
            return (datetime.datetime.now() - dt).total_seconds() / 60
        except ValueError:
            continue
    return None


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
    status: str                        # "open" | "closed"
    exit_price: Optional[float] = None
    exit_reason: Optional[str] = None
    pnl: Optional[float] = None
    r: Optional[float] = None
    filled_at: Optional[str] = None
    closed_at: Optional[str] = None


class AiTraderEquityPoint(BaseModel):
    date: str
    equity: float


class AiTraderSummary(BaseModel):
    # Runner status
    runner_alive: bool
    data_stale: bool               # True if last_updated > 10 min ago
    staleness_minutes: Optional[float] = None
    last_updated: Optional[str] = None

    # Account
    starting_capital: float
    capital: float                 # current equity
    cash: float
    realized_pnl: float
    unrealized_pnl: float          # sum of open positions P&L (0 for paper runner)
    peak_equity: float
    max_drawdown_usd: float
    max_drawdown_pct: float
    return_pct: float
    daily_loss_at_risk: float
    trades_today: int

    # Positions
    open_fills: list[AiTraderFill]
    closed_fills: list[AiTraderFill]

    # Performance metrics (from closed fills)
    closed_count: int
    win_count: int
    loss_count: int
    flat_count: int
    win_rate_pct: Optional[float]   # None if no closed trades
    profit_factor: Optional[float]  # None if no closed trades
    gross_wins: float
    gross_losses: float

    # Equity history (one point per session)
    equity_history: list[AiTraderEquityPoint]

    # Strategy config
    strategy: str
    target_stocks: list[str]
    max_risk_per_trade_usd: float
    max_daily_loss_usd: float
    max_trades_per_day: int

    # Signals
    signals_seen: list[str]

    # Source info
    source: str = "paper-state.json + paper-journal-v2.json"
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
    last_updated = journal.get("last_updated") or state.get("last_updated")
    staleness    = _staleness_minutes(last_updated)
    data_stale   = staleness is not None and staleness > 10

    starting_capital = float(journal.get("capital_usd") or 10_000)
    capital  = float(state.get("capital")  or journal.get("capital")  or starting_capital)
    cash     = float(state.get("cash")     or starting_capital)
    realized = float(state.get("realized_pnl") or journal.get("realized_pnl") or 0)
    peak_eq  = float(state.get("peak_equity")  or capital)
    max_dd_usd = float(state.get("max_drawdown") or 0)
    max_dd_pct = (max_dd_usd / peak_eq * 100) if peak_eq > 0 else 0.0
    return_pct = ((capital - starting_capital) / starting_capital * 100) if starting_capital else 0.0

    # ── Fills ──
    fills_raw = state.get("fills") or []

    def _parse_fill(f: dict) -> AiTraderFill:
        return AiTraderFill(
            symbol=str(f.get("symbol", "")),
            shares=int(f.get("shares", 0)),
            entry_price_fill=float(f.get("entry_price_fill", 0)),
            stop=float(f.get("stop", 0)),
            target=float(f.get("target", 0)),
            status=str(f.get("status", "closed")),
            exit_price=_opt_float(f.get("exit_price")),
            exit_reason=str(f.get("exit_reason")) if f.get("exit_reason") else None,
            pnl=_opt_float(f.get("pnl")),
            r=_opt_float(f.get("r")),
            filled_at=str(f.get("filled_at")) if f.get("filled_at") else None,
            closed_at=str(f.get("closed_at")) if f.get("closed_at") else None,
        )

    open_fills   = [_parse_fill(f) for f in fills_raw if f.get("status") == "open"]
    closed_fills = [_parse_fill(f) for f in fills_raw if f.get("status") != "open"]

    # ── Performance metrics ──
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
        profit_factor = 999.0  # no losses → infinite PF

    # ── Equity history from sessions_log ──
    sessions_log = journal.get("sessions_log") or []
    equity_history: list[AiTraderEquityPoint] = []
    running_equity = starting_capital
    for s in sessions_log:
        date = s.get("date")
        net  = s.get("net_pnl") or s.get("net") or 0
        if date:
            running_equity += float(net)
            equity_history.append(AiTraderEquityPoint(date=date, equity=round(running_equity, 2)))

    # ── Config ──
    strategy      = journal.get("strategy")  or "E: or-low stop / 1R target"
    target_stocks = journal.get("target_stocks") or []
    max_risk      = float(config.get("max_loss_per_trade_usd") or journal.get("max_risk_per_trade_usd") or 50)
    max_daily     = float(config.get("max_daily_loss_usd")    or journal.get("max_daily_loss_usd")    or 150)
    max_trades    = int(config.get("max_trades_per_day")       or journal.get("max_trades_per_day")    or 3)

    return AiTraderSummary(
        runner_alive=runner_alive,
        data_stale=data_stale,
        staleness_minutes=round(staleness, 1) if staleness is not None else None,
        last_updated=last_updated,
        starting_capital=starting_capital,
        capital=capital,
        cash=cash,
        realized_pnl=realized,
        unrealized_pnl=0.0,   # paper runner only tracks realized (no live mark-to-market)
        peak_equity=peak_eq,
        max_drawdown_usd=max_dd_usd,
        max_drawdown_pct=round(max_dd_pct, 2),
        return_pct=round(return_pct, 4),
        daily_loss_at_risk=float(state.get("daily_loss_at_risk") or 0),
        trades_today=int(state.get("trades_today") or 0),
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
        max_trades_per_day=max_trades,
        signals_seen=state.get("signals_seen") or [],
        source="paper-state.json + paper-journal-v2.json",
        data_dir_accessible=True,
    )
