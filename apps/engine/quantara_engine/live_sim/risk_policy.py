"""Account-level risk gates for live simulation — entries only, never block exits."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from quantara_engine.broker.execution_service import BrokerExecutionService
from quantara_engine.broker.types import BrokerAccountSnapshot
from quantara_engine.live_sim.constants import (
    DEFAULT_CONCENTRATION_MODE,
    DEFAULT_DAILY_LOSS_GATE_PCT,
    DEFAULT_MAX_DRAWDOWN_GATE_PCT,
    DEFAULT_MAX_GROUP_SL_RISK_PCT,
    DEFAULT_MAX_SYMBOL_SL_RISK_PCT,
    DEFAULT_MAX_TOTAL_OPEN_SL_RISK_PCT,
    DEFAULT_RISK_PER_TRADE_PCT,
)
from quantara_engine.persistence.store import TradingStore
from quantara_engine.risk.concentration import RISK_GROUPS


@dataclass(frozen=True)
class LiveSimRiskSettings:
    risk_per_trade_pct: Decimal
    max_total_open_sl_risk_pct: Decimal
    max_symbol_sl_risk_pct: Decimal
    max_group_sl_risk_pct: Decimal
    daily_loss_gate_pct: Decimal
    max_drawdown_gate_pct: Decimal
    concentration_mode: str
    high_water_mark: Decimal
    daily_start_equity: Decimal
    daily_start_date: str


def _dec(value: Any, default: Decimal) -> Decimal:
    if value is None:
        return default
    return Decimal(str(value))


def load_risk_settings(account_row: dict) -> LiveSimRiskSettings:
    raw = account_row.get("risk_settings") or {}
    if isinstance(raw, str):
        import json

        raw = json.loads(raw) if raw else {}
    equity = _dec(account_row.get("equity"), Decimal("10000"))
    starting = _dec(account_row.get("starting_cash"), Decimal("10000"))
    return LiveSimRiskSettings(
        risk_per_trade_pct=_dec(raw.get("risk_per_trade_pct"), DEFAULT_RISK_PER_TRADE_PCT),
        max_total_open_sl_risk_pct=_dec(
            raw.get("max_total_open_sl_risk_pct"), DEFAULT_MAX_TOTAL_OPEN_SL_RISK_PCT
        ),
        max_symbol_sl_risk_pct=_dec(raw.get("max_symbol_sl_risk_pct"), DEFAULT_MAX_SYMBOL_SL_RISK_PCT),
        max_group_sl_risk_pct=_dec(raw.get("max_group_sl_risk_pct"), DEFAULT_MAX_GROUP_SL_RISK_PCT),
        daily_loss_gate_pct=_dec(raw.get("daily_loss_gate_pct"), DEFAULT_DAILY_LOSS_GATE_PCT),
        max_drawdown_gate_pct=_dec(raw.get("max_drawdown_gate_pct"), DEFAULT_MAX_DRAWDOWN_GATE_PCT),
        concentration_mode=str(raw.get("concentration_mode") or DEFAULT_CONCENTRATION_MODE),
        high_water_mark=_dec(raw.get("high_water_mark"), max(equity, starting)),
        daily_start_equity=_dec(raw.get("daily_start_equity"), equity),
        daily_start_date=str(raw.get("daily_start_date") or ""),
    )


def symbol_risk_group(symbol: str) -> str | None:
    sym = symbol.upper().replace("/", "")
    for group, assets in RISK_GROUPS.items():
        if sym in assets or symbol.upper() in assets:
            return group
    return None


@dataclass
class OpenRiskSnapshot:
    total_sl_risk_usd: Decimal
    by_symbol: dict[str, Decimal]
    by_group: dict[str, Decimal]


def compute_open_sl_risk(store: TradingStore, account_id: str) -> OpenRiskSnapshot:
    from sqlalchemy import text

    rows = store.session.execute(
        text(
            """
            SELECT i.symbol, p.planned_sl_risk_usd
            FROM live_sim_positions p
            JOIN instruments i ON i.id = p.instrument_id
            WHERE p.broker_account_id = :aid AND p.status = 'open'
            """
        ),
        {"aid": account_id},
    ).mappings().all()

    by_symbol: dict[str, Decimal] = {}
    by_group: dict[str, Decimal] = {}
    total = Decimal("0")
    for row in rows:
        sym = str(row["symbol"]).upper()
        risk = Decimal(str(row["planned_sl_risk_usd"] or 0))
        total += risk
        by_symbol[sym] = by_symbol.get(sym, Decimal("0")) + risk
        grp = symbol_risk_group(sym)
        if grp:
            by_group[grp] = by_group.get(grp, Decimal("0")) + risk
    return OpenRiskSnapshot(total_sl_risk_usd=total, by_symbol=by_symbol, by_group=by_group)


def maybe_roll_daily_start(
    store: TradingStore,
    account_id: str,
    settings: LiveSimRiskSettings,
    equity: Decimal,
    now: datetime,
) -> LiveSimRiskSettings:
    today = now.astimezone(timezone.utc).strftime("%Y-%m-%d")
    if settings.daily_start_date == today:
        return settings
    from sqlalchemy import text

    new_settings = {
        **(store.session.execute(
            text("SELECT risk_settings FROM broker_accounts WHERE id = :id"),
            {"id": account_id},
        ).scalar() or {}),
        "daily_start_equity": float(equity),
        "daily_start_date": today,
    }
    store.session.execute(
        text(
            """
            UPDATE broker_accounts
            SET risk_settings = CAST(:settings AS jsonb), updated_at = NOW()
            WHERE id = :id
            """
        ),
        {"id": account_id, "settings": __import__("json").dumps(new_settings)},
    )
    return LiveSimRiskSettings(
        risk_per_trade_pct=settings.risk_per_trade_pct,
        max_total_open_sl_risk_pct=settings.max_total_open_sl_risk_pct,
        max_symbol_sl_risk_pct=settings.max_symbol_sl_risk_pct,
        max_group_sl_risk_pct=settings.max_group_sl_risk_pct,
        daily_loss_gate_pct=settings.daily_loss_gate_pct,
        max_drawdown_gate_pct=settings.max_drawdown_gate_pct,
        concentration_mode=settings.concentration_mode,
        high_water_mark=settings.high_water_mark,
        daily_start_equity=equity,
        daily_start_date=today,
    )


def update_high_water_mark(store: TradingStore, account_id: str, equity: Decimal) -> Decimal:
    from sqlalchemy import text

    row = store.session.execute(
        text("SELECT risk_settings FROM broker_accounts WHERE id = :id"),
        {"id": account_id},
    ).scalar()
    raw = row or {}
    hwm = Decimal(str(raw.get("high_water_mark") or equity))
    if equity > hwm:
        hwm = equity
        raw = {**raw, "high_water_mark": float(hwm)}
        store.session.execute(
            text(
                """
                UPDATE broker_accounts
                SET risk_settings = CAST(:settings AS jsonb), updated_at = NOW()
                WHERE id = :id
                """
            ),
            {"id": account_id, "settings": __import__("json").dumps(raw)},
        )
    return hwm


@dataclass
class GateResult:
    allowed: bool
    reason: str | None = None
    detail: str | None = None


def evaluate_entry_gates(
    *,
    settings: LiveSimRiskSettings,
    equity: Decimal,
    realized_pnl_today: Decimal,
    open_risk: OpenRiskSnapshot,
    proposed_risk_usd: Decimal,
    symbol: str,
    unrealized_pnl: Decimal = Decimal("0"),
) -> GateResult:
    if equity <= 0:
        return GateResult(False, "ACCOUNT_INACTIVE", "equity <= 0")

    hwm = settings.high_water_mark
    if hwm > 0:
        dd_pct = (hwm - equity) / hwm * Decimal("100")
        if dd_pct >= settings.max_drawdown_gate_pct:
            return GateResult(
                False,
                "DRAWDOWN_GATE",
                f"drawdown {dd_pct:.2f}% >= {settings.max_drawdown_gate_pct}%",
            )

    daily_start = settings.daily_start_equity
    if daily_start > 0:
        daily_loss = daily_start - equity
        if daily_loss > 0:
            daily_loss_pct = daily_loss / daily_start * Decimal("100")
            if daily_loss_pct >= settings.daily_loss_gate_pct:
                return GateResult(
                    False,
                    "DAILY_LOSS_GATE",
                    f"daily loss {daily_loss_pct:.2f}% >= {settings.daily_loss_gate_pct}%",
                )

    max_total = equity * settings.max_total_open_sl_risk_pct / Decimal("100")
    if open_risk.total_sl_risk_usd + proposed_risk_usd > max_total:
        return GateResult(
            False,
            "TOTAL_SL_RISK_LIMIT",
            f"total SL risk would exceed {settings.max_total_open_sl_risk_pct}%",
        )

    sym = symbol.upper().replace("/", "")
    sym_risk = open_risk.by_symbol.get(sym, Decimal("0"))
    max_sym = equity * settings.max_symbol_sl_risk_pct / Decimal("100")
    if sym_risk + proposed_risk_usd > max_sym:
        return GateResult(
            False,
            "SYMBOL_SL_RISK_LIMIT",
            f"{sym} SL risk would exceed {settings.max_symbol_sl_risk_pct}%",
        )

    grp = symbol_risk_group(sym)
    if grp and settings.concentration_mode == "ENFORCE":
        grp_risk = open_risk.by_group.get(grp, Decimal("0"))
        max_grp = equity * settings.max_group_sl_risk_pct / Decimal("100")
        if grp_risk + proposed_risk_usd > max_grp:
            return GateResult(
                False,
                "GROUP_SL_RISK_LIMIT",
                f"{grp} SL risk would exceed {settings.max_group_sl_risk_pct}%",
            )

    return GateResult(True)


def target_risk_for_equity(equity: Decimal, settings: LiveSimRiskSettings) -> Decimal:
    return (equity * settings.risk_per_trade_pct / Decimal("100")).quantize(Decimal("0.01"))
