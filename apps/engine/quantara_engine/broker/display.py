"""User-facing broker rejection labels — Hebrew only, no internal codes."""

from __future__ import annotations

BROKER_REASON_HE: dict[str, str] = {
    "short_not_allowed": "החשבון הנוכחי אינו מאפשר עסקת שורט בנכס זה",
    "insufficient_buying_power": "אין מספיק כוח קנייה לביצוע העסקה",
    "insufficient_margin": "אין מספיק מרווח זמין לביצוע העסקה",
    "max_leverage": "חריגת מינוף נטו",
    "max_gross_leverage": "חריגת מינוף ברוטו",
    "max_asset_exposure": "חריגת חשיפה לנכס",
    "max_order_notional": "גודל העסקה חורג מהמגבלה",
    "invalid_quantity": "כמות לא תקינה",
    "market_closed": "המסחר בנכס סגור כעת",
    "stale_market_data": "נתוני שוק לא מעודכנים",
    "risk_limit": "מגבלת סיכון בברוקר",
    "duplicate_opportunity": "הזדמנות כבר טופלה",
    "account_paused": "חשבון הברוקר מושהה",
    "margin_call": "קריאת מרווח — לא ניתן לפתוח עסקה חדשה",
    "liquidation": "חשבון בליקווידציה",
    "unsupported_asset": "הנכס אינו נתמך בחשבון המסחר הנוכחי",
    "broker_rejected": "הברוקר דחה את ההזמנה",
    "broker_capability_denied": "לא ניתן לביצוע בחשבון הנוכחי",
    "submission_unknown": "מצב שליחה לא ידוע — נדרשת התאמה לפני המשך",
    "reconciliation_halted": "פער התאמה — ביצוע חדש מושהה",
    "short_locate_unavailable": "אין אפשרות לשאילת מניה לשורט — לא נפתחה עסקה",
    "liquidation_proximity": "קרוב מדי לסף ליקווידציה — לא נפתחה עסקה",
}

EXECUTION_STAGE_HE: dict[str, str] = {
    "strategy_setup": "זוהה תנאי אסטרטגיה",
    "signal_created": "נוצר איתות",
    "product_check": "בדיקת מוצר/יכולת",
    "risk_check": "בדיקת סיכון",
    "execution_approved": "אושרה לביצוע",
    "order_sent": "פקודה נשלחה",
    "broker_accepted": "התקבלה אצל הברוקר",
    "partial_fill": "בוצעה חלקית",
    "filled": "בוצעה במלואה",
    "rejected": "נדחתה",
    "not_opened": "לא נפתחה עסקה",
}


def execution_stage_he(stage: str | None) -> str:
    if not stage:
        return EXECUTION_STAGE_HE["not_opened"]
    return EXECUTION_STAGE_HE.get(stage.strip().lower(), stage)


def broker_reason_he(reason: str | None) -> str:
    if not reason:
        return BROKER_REASON_HE["broker_rejected"]
    key = reason.strip().lower()
    if key.startswith("broker_reject:"):
        key = key.split(":", 1)[1].strip().split()[0]
    return BROKER_REASON_HE.get(key, BROKER_REASON_HE["broker_rejected"])
