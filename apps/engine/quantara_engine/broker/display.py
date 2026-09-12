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
}


def broker_reason_he(reason: str | None) -> str:
    if not reason:
        return BROKER_REASON_HE["broker_rejected"]
    key = reason.strip().lower()
    if key.startswith("broker_reject:"):
        key = key.split(":", 1)[1].strip().split()[0]
    return BROKER_REASON_HE.get(key, BROKER_REASON_HE["broker_rejected"])
