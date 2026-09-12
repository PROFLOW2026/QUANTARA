"""Default live-sim risk policy (configurable via broker_accounts.risk_settings)."""

from __future__ import annotations

from decimal import Decimal

DEFAULT_RISK_PER_TRADE_PCT = Decimal("1.00")
DEFAULT_MAX_TOTAL_OPEN_SL_RISK_PCT = Decimal("3.00")
DEFAULT_MAX_SYMBOL_SL_RISK_PCT = Decimal("1.00")
DEFAULT_MAX_GROUP_SL_RISK_PCT = Decimal("2.00")
DEFAULT_DAILY_LOSS_GATE_PCT = Decimal("2.00")
DEFAULT_MAX_DRAWDOWN_GATE_PCT = Decimal("10.00")
DEFAULT_CONCENTRATION_MODE = "ENFORCE"

REJECTION_HE: dict[str, str] = {
    "DUPLICATE_OPPORTUNITY": "הזדמנות כפולה — כבר טופלה בחשבון הסימולציה",
    "TOTAL_SL_RISK_LIMIT": "מגבלת הסיכון הכוללת בחשבון הגיעה",
    "SYMBOL_SL_RISK_LIMIT": "מגבלת סיכון לסמל הגיעה",
    "GROUP_SL_RISK_LIMIT": "קבוצת הסיכון הגיעה למגבלה",
    "DAILY_LOSS_GATE": "שער הפסד יומי — כניסות חדשות חסומות",
    "DRAWDOWN_GATE": "שער drawdown — כניסות חדשות חסומות",
    "MARGIN_INSUFFICIENT": "אין מספיק מרווח / buying power",
    "MIN_QUANTITY": "גודל העסקה קטן מהמינימום",
    "MIN_QUANTITY_EXCEEDS_RISK_BUDGET": "מינימום כמות חורג מתקציב הסיכון",
    "INVALID_STOP_LOSS": "מרחק stop loss לא תקין",
    "SESSION_CLOSED": "שוק סגור — אין כניסות חדשות",
    "STALE_SIGNAL": "אות ישן מדי לביצוע",
    "ACCOUNT_INACTIVE": "חשבון הסימולציה לא פעיל",
    "BROKER_REJECTED": "הברוקר דחה את ההזמנה",
    "DATA_NOT_FRESH": "נתוני שוק לא מעודכנים",
    "MARKET_CLOSED": "שוק סגור",
    "EXECUTION_FAILED": "ביצוע נכשל",
}
