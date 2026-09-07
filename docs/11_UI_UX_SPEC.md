# QUANTARA — UI/UX Specification

## 1. Design Principle

**Simple outside, sophisticated inside.**

המשתמש מתחיל בעולם המסחר — UI חייב לענות מהר על שאלות יומיות.
Trading logic **לעולם לא** ב-Frontend.

## 2. Visual Language

| Aspect | Guideline |
|--------|-----------|
| Style | Clean, professional, trading-oriented |
| Theme | Dark mode default (light optional later) |
| Colors | Green (profit/long), Red (loss/short), Neutral grays |
| Typography | Clear hierarchy, monospace for numbers/prices |
| Density | Information-rich but not cluttered |
| Responsive | Desktop-first, mobile-friendly |
| Language | **Hebrew** (Phase 1) |
| i18n | All strings via translation keys — `next-intl` |

### 2.1 Localization Readiness

```tsx
// CORRECT
t('home.portfolio_status')

// FORBIDDEN
<p>מצב התיק</p>  // hardcoded in component without t()
```

Translation files: `apps/web/messages/he.json`, `en.json` (en prepared, empty/minimal).

## 3. Navigation Structure

```
Sidebar / Bottom Nav (mobile)
├── 🏠 Home / Today          (/ )
├── 💼 Portfolio             (/portfolio)
├── 📊 Open Positions        (/positions)
├── 📓 Trade Journal         (/journal)
├── 📋 Decision Log          (/decisions)
├── 🎯 Strategies            (/strategies)
│   └── Versions             (/strategies/[slug]/versions)
├── 🔬 Backtests             (/backtests)
│   └── Detail               (/backtests/[id])
├── 🧪 Experiments           (/experiments)
├── 📈 Analytics             (/analytics)
├── 🥇 Market Data / Gold    (/market/gold)
└── ⚙️ Settings              (/settings)
```

## 4. Screen Specifications

### 4.1 Home / Today (`/`)

**Purpose:** Answer "what's happening right now?" in < 5 seconds.

**Sections:**

| Section | Content | Data Source |
|---------|---------|-------------|
| Portfolio Summary | Equity, daily P&L, drawdown | GET /portfolio |
| Gold Snapshot | Current price, change, last update | GET /candles/latest |
| Active Signal | Signal or "no signal" + reason | GET /decisions/latest |
| Open Positions | Count, total unrealized P&L | GET /positions?status=open |
| Risk Status | Profile, halt state, exposure % | GET /portfolio/risk-status |
| Today's Activity | Trades today, decisions count | GET /analytics/today |
| Worker Status | Green/red indicator | GET /workers/status |

**Signal Card — When NO signal:**

```
אין Signal פעיל
סיבה: NO_SETUP — ממתין ל-pullback ל-EMA20
Strategy: Gold Trend Pullback v1.0.0
Timeframe: 1h
עודכן: לפני 12 דקות
```

**Signal Card — When signal exists:**

```
🟢 BUY Signal
Instrument: XAU/USD
Entry: ~2650.50 (candle close)
Stop Loss: 2637.65
Take Profit: 2675.55
Risk: $100 target / $40 actual (Balanced, capped by exposure)
Execution: Candle N+1 open (default)
Strategy: Gold Trend Pullback v1.0.0
סיבה: Pullback to EMA20 in uptrend, RSI confirmed
```

**Quick Actions:**
- Pause/Resume trading
- Link to Decision Log
- Link to Backtests

### 4.2 Portfolio (`/portfolio`)

| Element | Description |
|---------|-------------|
| Equity chart | Mini equity curve (30 days) |
| Cash balance | balance (initial + realized) |
| Unrealized P&L | |
| Realized P&L (total) | |
| Peak equity | |
| Current drawdown | |
| Risk profile | Active profile name |
| Mode | Paper |
| Snapshots table | Historical snapshots |

### 4.3 Open Positions (`/positions`)

| Column | Content |
|--------|---------|
| Instrument | XAU/USD |
| Direction | LONG / SHORT |
| Size | Quantity |
| Entry | Price + time |
| Current | Price |
| SL / TP | Levels |
| Unrealized P&L | $ and % |
| Duration | Time open |
| Strategy | Name + version |

Empty state: "אין פוזיציות פתוחות"

### 4.4 Trade Journal (`/journal`)

Filterable table of **closed trades**:

| Column | Content |
|--------|---------|
| Date | Close time |
| Instrument | |
| Direction | |
| Entry / Exit | Prices |
| P&L | Net $ |
| Duration | |
| Exit reason | SL / TP / Strategy / etc. |
| Strategy version | |
| Fees | |

Filters: date range, strategy, direction, exit reason, P&L positive/negative.

Click row → trade detail modal with full metadata, linked decisions.

### 4.5 Decision Log (`/decisions`)

**Every** strategy evaluation — not just trades.

| Column | Content |
|--------|---------|
| Timestamp | |
| Type | BUY_SIGNAL, HOLD, RISK_DENIED, etc. |
| Message | Human-readable reason |
| Strategy | Instance name |
| Instrument | |
| Candle time | |

Filters: type, date, strategy, decision_type.

Color coding:
- Green: approved/executed
- Yellow: hold/no setup
- Red: denied/halted

### 4.6 Strategies (`/strategies`)

List of registered strategies:

| Column | Content |
|--------|---------|
| Name | |
| Slug | |
| Status | active/draft/deprecated |
| Versions | Count |
| Instruments | |
| Timeframes | |
| Active instances | |

Actions: View versions, Create instance, Deactivate.

### 4.7 Strategy Versions (`/strategies/[slug]/versions`)

| Column | Content |
|--------|---------|
| Version | 1.0.0 |
| Status | |
| Created | |
| Parameters | View JSON |
| Trades count | |
| Backtests count | |
| Logic hash | Short hash |

Read-only for frozen versions. Create new version = developer action.

### 4.8 Backtests (`/backtests`)

| Column | Content |
|--------|---------|
| Name/ID | |
| Strategy + version | |
| Period | Start — End |
| Status | pending/running/completed/failed |
| Return | % |
| Win rate | |
| Max DD | |
| Trades | Count |
| Created | |

Action: **New Backtest** button → configuration form.

**New Backtest Form:**
- Strategy + version (dropdown)
- Instrument (XAU/USD)
- Timeframe
- Date range
- Initial capital
- Risk profile
- Parameter overrides (optional, JSON editor)
- Execution assumptions (spread, slippage, fees)

### 4.9 Backtest Detail (`/backtests/[id]`)

| Section | Content |
|---------|---------|
| Summary cards | Return, win rate, profit factor, max DD, trades |
| Equity curve | Full chart |
| Drawdown chart | |
| Parameters used | JSON display |
| Execution assumptions | |
| Dataset fingerprint | SHA256 of OHLCV content |
| Trade list | Same as journal, filtered to this backtest |
| Comparison | vs buy-and-hold (if available) |

Running state: progress indicator (candles processed / total).

### 4.10 Experiments (`/experiments`)

Phase 1: **Basic structure** — list experiments, show linked strategy instances.

Full parallel comparison UI → future. Data model ready.

| Column | Content |
|--------|---------|
| Name | |
| Status | |
| Instances | Count |
| Period | |

### 4.11 Analytics (`/analytics`)

Tabs:

**Portfolio Performance:**
- Equity curve (selectable range)
- Drawdown chart
- Daily returns heatmap/calendar
- Monthly returns table
- Summary metrics card

**Strategy Performance:**
- Filter by strategy/version
- Win rate, profit factor, expectancy
- Long vs short breakdown
- Performance by session (when data available)
- Performance by day of week
- Average holding time

**Costs:**
- Total fees
- Total slippage
- Spread impact estimate

All metrics from `12_ANALYTICS_METRICS.md` — UI displays, never calculates.

### 4.12 Market Data / Gold (`/market/gold`)

| Section | Content |
|---------|-------------|
| Current price | Large display |
| Change | % and $ (vs prev close) |
| Chart | Candlestick, selectable timeframe |
| Provider status | Health, last fetch |
| Data quality | Gap warnings, staleness |
| Recent candles | Table (last 20) |

Timeframe toggle: 5m / 15m / 1h

### 4.13 Settings (`/settings`)

| Setting | Type |
|---------|------|
| Display timezone | Select |
| Default risk profile | Select |
| Paper trading enabled | Toggle |
| Initial capital | Number (read-only after first trade) |
| Trading halt/resume | Button |
| Execution defaults | Spread, slippage, fees |
| API key display | Read-only (for local setup) |
| Language | Hebrew (English disabled Phase 1) |

## 5. Responsive Behavior

| Breakpoint | Layout |
|------------|--------|
| Desktop (≥1024px) | Sidebar navigation, multi-column dashboards |
| Tablet (768-1023px) | Collapsible sidebar, 2-column |
| Mobile (<768px) | Bottom navigation, single column, cards stack |

Critical mobile views: Home, Positions, Decision Log (latest).

## 6. Component System

Based on shadcn/ui pattern:

| Component | Usage |
|-----------|-------|
| Card | Summary sections |
| Table | Journal, decisions, backtests |
| Badge | Status, direction, decision type |
| Chart | Equity, drawdown, candles |
| Dialog | Trade detail, confirm halt |
| Tabs | Analytics sections |
| Skeleton | Loading states |
| Toast | Action confirmations |

Shared components in `apps/web/components/`:
- `PriceDisplay` — formatted with color
- `PnLDisplay` — green/red with +/- prefix
- `StrategyBadge` — name + version
- `DirectionBadge` — LONG/SHORT
- `DecisionTypeBadge` — colored by type
- `MetricCard` — label + value + change

## 7. Data Fetching

| Pattern | Usage |
|---------|-------|
| Server Components | Initial page load (portfolio, lists) |
| Client polling | Home/Today — refresh every 60s |
| SWR/React Query | Client-side cache for charts |
| Engine API | All trading data |
| Direct Drizzle | Optional for read-heavy static pages |

**UI never:**
- Computes P&L
- Runs strategy logic
- Submits orders without Engine API
- Modifies trade history

## 8. Empty States

Every list screen needs meaningful empty state with guidance:

| Screen | Empty Message |
|--------|---------------|
| Positions | "אין פוזיציות פתוחות. המערכת תפתח פוזיציה כשתתקבל Signal מאושרת." |
| Journal | "אין trades עדיין. הרץ Backtest או המתן ל-Paper trading." |
| Backtests | "אין backtests. צור Backtest ראשון לבדיקת Strategy." |
| Decisions | "אין decisions. ודא ש-Worker פעיל ו-Strategy instance מוגדר." |

## 9. Cross-References

- Analytics calculations: `12_ANALYTICS_METRICS.md`
- API: `02_SYSTEM_ARCHITECTURE.md`
- Decision types: `03_CANONICAL_TRADING_FLOW.md`
- Gold strategy display: `10_GOLD_FIRST_STRATEGY.md`
