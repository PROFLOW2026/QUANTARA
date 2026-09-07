# QUANTARA — Master Context

> **QUANTARA DOCUMENTATION = FINAL BASELINE**

> מסמך זה הוא נקודת הכניסה לכל מי שעובד על הפרויקט. קרא אותו לפני כל פיתוח.

## מה זה QUANTARA?

QUANTARA היא **מערכת פרטית** למסחר אלגוריתמי, מחקר ובדיקת אסטרטגיות.

| שלב | תיאור |
|-----|--------|
| **עכשיו** | למידה ובדיקה בלבד — **ללא כסף אמיתי** |
| **עתיד אפשרי** | הרחבה למוצר — **לא נבנה עכשיו** |

## המודל הראשוני

```
REAL MARKET DATA
+ VIRTUAL MONEY
+ REAL STRATEGY LOGIC
+ PAPER TRADING
+ BACKTESTING
+ ANALYTICS
```

## Instrument ראשון

**XAU/USD (Gold)** — כל הארכיטקטורה תומכת בהוספת instruments נוספים בעתיד ללא שינוי מבני.

## עקרון ליבה

QUANTARA **אינה** מערכת AI שמנחשת את השוק.

הליבה: **מנוע מסחר דטרמיניסטי, מדיד ושקוף**.

AI (בעתיד) = שכבת הסבר, לימוד וניתוח **בלבד** — לא מקור החלטות פיננסיות.

## Modes

| Mode | שלב 1 | תיאור |
|------|-------|--------|
| BACKTEST | ✅ | סימולציה על נתונים היסטוריים |
| PAPER | ✅ | נתוני שוק אמיתיים + כסף וירטואלי |
| LIVE-MANUAL | 🔜 | Signal + אישור ידני |
| LIVE-AUTOMATED | 🔜 | מסחר אוטומטי דרך Broker Adapter |

## זרימה קנונית

```
Market Data → Strategy → Signal → Risk Engine → Order Intent
→ Execution → Order / Fill → Position → Exit → P&L
→ Analytics → Research / Improvement
```

## מה **לא** בונים עכשיו

- SaaS, Billing, Teams, Organizations
- MFA, KYC, Compliance מסחרי
- Live Trading עם כסף אמיתי
- AI Prediction / ML / Neural Networks
- Authentication מורכב

ראה `19_FUTURE_NOT_NOW.md` לרשימה מלאה.

## מקור האמת

| סוג | מיקום |
|-----|--------|
| תכנון | `/docs/*.md` |
| הוראות Cursor | `CURSOR_INSTRUCTIONS.md` |
| קוד | ייבנה **רק** לאחר אישור המסמכים |

## מסמכי תכנון — מפת קריאה

| # | מסמך | תוכן |
|---|------|------|
| 00 | MASTER_CONTEXT | מסמך זה |
| 01 | PRODUCT_SCOPE | גבולות מוצר |
| 02 | SYSTEM_ARCHITECTURE | ארכיטקטורה טכנית |
| 03 | CANONICAL_TRADING_FLOW | זרימת מסחר |
| 04 | DATABASE_MODEL | מודל נתונים |
| 05 | STRATEGY_FRAMEWORK | חוזה Strategy |
| 06 | RISK_ENGINE | מנוע סיכון |
| 07 | PAPER_TRADING_ENGINE | Paper Trading |
| 08 | BACKTESTING_SPEC | Backtesting |
| 09 | MARKET_DATA_SPEC | Market Data |
| 10 | GOLD_FIRST_STRATEGY | Strategy ראשונה |
| 11 | UI_UX_SPEC | ממשק משתמש |
| 12 | ANALYTICS_METRICS | Analytics |
| 13 | BACKGROUND_WORKERS | Workers |
| 14 | ERROR_AND_RECOVERY_RULES | שגיאות והתאוששות |
| 15 | PROJECT_STRUCTURE | מבנה פרויקט |
| 16 | DEVELOPMENT_PHASES | שלבי פיתוח |
| 17 | TESTING_AND_ACCEPTANCE | בדיקות |
| 18 | FINAL_DEFINITION_OF_DONE | Definition of Done |
| 19 | FUTURE_NOT_NOW | עתיד — לא עכשיו |

## החלטות טכנולוגיות (סיכום)

| שכבה | טכנולוגיה |
|------|-----------|
| Frontend | Next.js + TypeScript + Tailwind |
| Trading Engine | Python (FastAPI) |
| Database | PostgreSQL |
| ORM (TypeScript) | **Drizzle** — ראה `02_SYSTEM_ARCHITECTURE.md` |
| ORM (Python) | SQLAlchemy 2.0 |
| Workers | Python + APScheduler / Celery Beat |
| Auth (Phase 1) | Single-user session — ללא multi-tenancy |

## עקרונות מחייבים (תמצית)

1. הפרדה ברורה בין Market Data, Strategy, Risk, Execution, Portfolio, Analytics, UI
2. Strategy **לא יודעת** אם היא ב-Backtest, Paper או Live
3. אותו Strategy Code בכל ה-Modes (דרך abstractions)
4. כל Strategy change = Version חדש
5. Trading History **אינה ניתנת לעריכה**
6. Backtest = reproducible, no lookahead
7. Idempotency ב-Workers
8. Localization readiness — UI בעברית, טקסט לא קבור בקוד

## סטטוס

**Documentation:** FINAL BASELINE  
**Implementation:** Full build in progress / local dev ready

**Repository:** https://github.com/PROFLOW2026/QUANTARA.git (`main`)
