"""Canonical Live Sim funnel rejection categories."""

from __future__ import annotations

REJECTION_TO_FUNNEL: dict[str, str] = {
    "STALE_SIGNAL": "STALE",
    "EXECUTION_WINDOW": "STALE",
    "SESSION_CLOSED": "SESSION",
    "live_sim_integrity_containment": "CONTAINMENT",
    "ROBOT_LIVE_PAUSED": "OTHER",
    "ASSET_RESEARCH_ONLY": "OTHER",
    "COMBINATION_BLOCKED": "OTHER",
    "ASSET_TRADING_PAUSED": "CONTAINMENT",
    "BROKER_CAPABILITY_DENIED": "OTHER",
    "INVALID_STOP_LOSS": "INVALID_SIZE",
    "MIN_QUANTITY": "MIN_QTY",
    "MIN_QUANTITY_EXCEEDS_RISK_BUDGET": "MIN_QTY",
    "BROKER_REJECTED": "MARGIN",
    "BROKER_HALTED": "CONTAINMENT",
    "asset_cash_insufficient": "MARGIN",
    "asset_sl_risk_exceeds_envelope": "ASSET_EXPOSURE",
    "asset_leverage_cap_exceeded": "ASSET_EXPOSURE",
    "OWNER_GLOBAL_RISK": "OWNER_EXPOSURE",
    "global_execution_halted": "CONTAINMENT",
    "TOTAL_SL_RISK_LIMIT": "RISK_LIMIT",
    "SYMBOL_SL_RISK_LIMIT": "RISK_LIMIT",
    "GROUP_SL_RISK_LIMIT": "CORRELATION",
    "DAILY_LOSS_GATE": "RISK_LIMIT",
    "DRAWDOWN_GATE": "RISK_LIMIT",
    "buying_power_cap": "MARGIN",
}


def funnel_category(rejection_reason: str | None) -> str:
    if not rejection_reason:
        return "OTHER"
    if rejection_reason in REJECTION_TO_FUNNEL:
        return REJECTION_TO_FUNNEL[rejection_reason]
    upper = rejection_reason.upper()
    if "STALE" in upper:
        return "STALE"
    if "RISK" in upper or "GATE" in upper:
        return "RISK_LIMIT"
    if "MARGIN" in upper or "CASH" in upper:
        return "MARGIN"
    return "OTHER"
