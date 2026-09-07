# QUANTARA — Product Scope

## 1. מטרת המוצר

QUANTARA מאפשרת למשתמש יחיד (בעל המערכת) ל:

1. לקבל נתוני שוק אמיתיים על XAU/USD
2. להגדיר ולהריץ Strategies עם versioning
3. לבצע Backtesting reproducible על נתונים היסטוריים
4. לבצע Paper Trading עם כסף וירטואלי
5. לנהל Portfolio, Positions, Trades
6. לנתח ביצועים (Strategy + Portfolio level)
7. לעקוב אחר Decision Log — לא רק Trades
8. להריץ Workers ברקע גם כשה-UI סגור

## 2. קהל יעד (Phase 1)

| מאפיין | ערך |
|--------|-----|
| משתמשים | **1** — בעל המערכת |
| סוג | Private personal system |
| מטרה | למידה, מחקר, בדיקת infrastructure |
| כסף אמיתי | **לא** |

## 3. In Scope — Phase 1

### 3.1 Trading Modes

- ✅ BACKTEST
- ✅ PAPER

### 3.2 Instruments

- ✅ XAU/USD (Gold) — instrument ראשון
- ✅ Instrument abstraction — מוכן להוספת instruments

### 3.3 Timeframes

- 5m, 15m, 1h

### 3.4 Core Modules

| מודול | Phase 1 |
|-------|---------|
| Market Data Layer + Adapter | ✅ |
| Strategy Framework + Registry | ✅ |
| Risk Engine | ✅ |
| Paper Trading Engine | ✅ |
| Backtesting Engine | ✅ |
| Portfolio / Positions / Trades | ✅ |
| Decision Log | ✅ |
| Analytics | ✅ |
| Background Workers | ✅ |
| UI (כל המסכים המתוכננים) | ✅ |
| Risk Profiles (Conservative/Balanced/Aggressive) | ✅ |
| Experiments data model | ✅ (structure only, UI basic) |

### 3.5 Strategy ראשונה

- Gold Trend Pullback Strategy v1.0 — ראה `10_GOLD_FIRST_STRATEGY.md`
- מטרה: להוכיח את השרשרת, לא "רובוט קסם"

### 3.6 Broker

- PaperBrokerAdapter בלבד
- BrokerAdapter interface — מוכן ל-MT5 בעתיד

## 4. Out of Scope — Phase 1

| פריט | סיבה |
|------|------|
| LIVE-MANUAL / LIVE-AUTOMATED | לא עכשיו — ארכיטקטורה מוכנה |
| כסף אמיתי | למידה בלבד |
| MT5 connection | עתיד — אחרי Paper validation |
| AI / ML / LLM prediction | לא מקור החלטות |
| Registration / MFA / Password reset | מערכת פרטית |
| Organizations / Teams / Billing | לא SaaS |
| KYC / Compliance | לא רלוונטי |
| Multi-tenancy | משתמש יחיד |
| HFT / tick data | לא נדרש |
| Mobile native app | Responsive web מספיק |
| Public API | לא נדרש |

## 5. Non-Goals (מפורש)

1. **לא** לבנות "AI שמנחש את השוק"
2. **לא** להבטיח רווחיות
3. **לא** לבנות productization infrastructure
4. **לא** לערוך trading history retroactively
5. **לא** לקצר דרך עם strategy logic ב-UI

## 6. Success Criteria — Phase 1

המערכת נחשבת מוצלחת כאשר:

1. Backtest על XAU/USD רץ end-to-end ומייצר metrics מלאים
2. Paper trading רץ ברקע על candles אמיתיים
3. אותה Strategy code עובדת ב-Backtest וב-Paper
4. Risk Engine יכול לדחות signal
5. Decision Log מלא — כולל HOLD, DENIED, NO SETUP
6. UI עונה על שאלות "Today" (ראה `11_UI_UX_SPEC.md`)
7. Analytics מפריד Strategy Performance vs Portfolio Performance
8. Workers idempotent — אין duplicate trades
9. Strategy versioning עובד — כל trade יודע version
10. אין lookahead bias ב-backtest

## 7. Assumptions

| הנחה | פירוט |
|------|--------|
| Single user | אין צורך ב-RBAC מורכב |
| Internet access | ל-fetch market data |
| PostgreSQL available | local או cloud |
| Python + Node runtime | על מכונת הפיתוח / server |
| Timezone | UTC פנימית, display ב-Asia/Jerusalem (configurable) |
| Capital (virtual) | ברירת מחדל $10,000 — configurable |

## 8. Constraints

- כל הקבצים בתיקיית QUANTARA בלבד
- אין deploy/push בשלב תכנון
- אין secrets ב-repo — `.env` local only
- Market data provider — abstracted (provider ספציפי ייבחר בפיתוח)

## 9. Future Expansion (לא Phase 1)

ראה `19_FUTURE_NOT_NOW.md`:

- Instruments נוספים (NAS100, SPX, Forex, Crypto...)
- LIVE modes
- MT5 Demo
- Experiments parallel runs
- Walk-forward / out-of-sample
- AI explanation layer
- English UI
- SaaS conversion

## 10. Document Boundaries

מסמך זה מגדיר **מה** המערכת עושה.
**איך** — ב-`02_SYSTEM_ARCHITECTURE.md` ומסמכים נלווים.
