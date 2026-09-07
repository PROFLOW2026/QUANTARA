# QUANTARA — Analytics & Metrics

## 1. Purpose

Analytics module מחשב metrics מ-Trades, Positions, Snapshots, ו-Decisions.
**Owner:** Python Analytics Service — **לא** Frontend.

## 2. Separation: Strategy vs Portfolio Performance

| Level | Scope | Use Case |
|-------|-------|----------|
| **Strategy Performance** | Trades from specific strategy/version/instance | "How good is Gold Trend Pullback v1.0.0?" |
| **Portfolio Performance** | All trades in portfolio (all strategies combined) | "How is my paper account doing?" |

Same formulas, different filters.

## 3. Core Metrics

### 3.1 Return Metrics

| Metric | Formula | Notes |
|--------|---------|-------|
| Total Return ($) | `final_equity - initial_capital` | |
| Total Return (%) | `(final - initial) / initial × 100` | |
| Daily Return (%) | `(equity_t - equity_t-1) / equity_t-1 × 100` | From snapshots |
| Monthly Return (%) | Compound daily returns per month | |
| CAGR | Future — requires >1 year data | Placeholder |

### 3.2 Trade Statistics

| Metric | Formula |
|--------|---------|
| Total Trades | `COUNT(trades)` |
| Winning Trades | `COUNT WHERE realized_pnl > 0` |
| Losing Trades | `COUNT WHERE realized_pnl <= 0` |
| Win Rate (%) | `winning / total × 100` |
| Average Win ($) | `AVG(realized_pnl) WHERE > 0` |
| Average Loss ($) | `AVG(realized_pnl) WHERE <= 0` — **signed (negative value)** |
| Largest Win | `MAX(realized_pnl)` |
| Largest Loss | `MIN(realized_pnl)` |
| Average Duration | `AVG(duration_seconds)` |
| Average Duration (hours) | `AVG(duration_seconds) / 3600` |

### 3.3 Risk-Adjusted Metrics

| Metric | Formula | Phase |
|--------|---------|-------|
| Profit Factor | `SUM(wins) / ABS(SUM(losses))` | v1 ✅ |
| Expectancy | `(win_rate × average_win) + (loss_rate × average_loss)` where `average_loss` is **signed negative** — equivalent to `win_rate × avg_win - loss_rate × abs(avg_loss)` | v1 ✅ |
| Maximum Drawdown (%) | Max `(peak - trough) / peak × 100` from snapshots | v1 ✅ |
| Sharpe Ratio | `(avg_return - rf) / std_return × √252` | Future |
| Sortino Ratio | Similar, downside std only | Future |
| Calmar Ratio | `CAGR / max_drawdown` | Future |

### 3.4 Cost Metrics

| Metric | Formula |
|--------|---------|
| Total Fees | `SUM(trades.fees_total)` |
| Total Slippage | `SUM(trades.slippage_total)` — attribution |
| Total Spread | `SUM(trades.spread_total)` — attribution |
| Average Fee per Trade | `total_fees / total_trades` |
| Cost Impact (%) | `(total_fees) / abs(sum(gross_wins)) × 100` — fees only; spread/slippage already in gross P&L |

## 4. Breakdown Dimensions

### 4.1 By Strategy

Filter: `strategy_version_id`

Returns all core metrics for that version.

### 4.2 By Strategy Version Comparison

Multiple versions side-by-side:

```
v1.0.0: 45 trades, 52% win rate, PF 1.3
v1.1.0: 38 trades, 55% win rate, PF 1.5
```

### 4.3 Long vs Short

| Metric | Filter |
|--------|--------|
| Long trades | `direction = long` |
| Short trades | `direction = short` |
| Long win rate | wins_long / total_long |
| Short win rate | wins_short / total_short |

### 4.4 By Session (Future-Ready)

When session tagging available on trades:

```
Asia:   12 trades, 58% win rate
London: 20 trades, 48% win rate
NY:     13 trades, 54% win rate
```

Phase 1: compute if entry hour available (UTC hour buckets as proxy).

### 4.5 By Day of Week

```
Monday:    8 trades, avg P&L $12
Tuesday:  10 trades, avg P&L -$5
...
```

### 4.6 By Exit Reason

```
SL:       20 trades, avg loss -$85
TP:       15 trades, avg win $170
Strategy:  5 trades, avg P&L $30
```

## 5. Equity Curve

```
Source: portfolio_snapshots
X: timestamp
Y: equity
Optional overlay: balance, drawdown_pct
```

Computed server-side, returned as time series array.

## 6. Drawdown Series

```
For each snapshot:
  running_peak = max(equity up to this point)
  drawdown = (running_peak - equity) / running_peak × 100

Return: [{timestamp, drawdown_pct, equity, peak}]
```

## 7. Daily Returns

```
From consecutive daily snapshots (last snapshot per day):
  daily_return = (equity_t - equity_t-1) / equity_t-1
```

## 8. Backtest Metrics

Stored in `backtest_runs.metrics` on completion — see `08_BACKTESTING_SPEC.md`.

Analytics service can recompute for verification but stored metrics are authoritative.

## 9. Today Summary (Home Page)

| Metric | Source |
|--------|--------|
| Today P&L | Trades closed today + unrealized change |
| Today trades | COUNT trades closed today |
| Today decisions | COUNT decisions today |
| Latest signal | Latest decision with type BUY/SELL |
| Current drawdown | From latest snapshot |

## 10. API Endpoints (Conceptual)

```
GET /api/v1/analytics/portfolio?range=30d
GET /api/v1/analytics/strategy/{version_id}?range=90d
GET /api/v1/analytics/equity-curve?portfolio_id=&range=
GET /api/v1/analytics/drawdown?portfolio_id=&range=
GET /api/v1/analytics/breakdown/direction?strategy_version_id=
GET /api/v1/analytics/breakdown/day-of-week?portfolio_id=
GET /api/v1/analytics/breakdown/exit-reason?strategy_version_id=
GET /api/v1/analytics/today
GET /api/v1/analytics/compare/versions?ids=v1,v2
```

## 11. Calculation Ownership

| Calculation | Owner | UI Role |
|-------------|-------|---------|
| All metrics above | Analytics Service (Python) | Display only |
| Formatting (%, $) | UI | Format numbers |
| Chart rendering | UI | Render server data |
| Aggregation filters | Analytics Service | Send filter params |

## 12. P&L Clarity Rules

| Term | Definition | Where Used |
|------|------------|------------|
| fill_price | Includes spread + slippage | fills, trades entry/exit |
| Gross P&L | `(exit_fill - entry_fill) × qty` (LONG) | trades.gross_pnl |
| Net P&L (Realized) | `gross_pnl - entry_fees - exit_fees` | trades.realized_pnl |
| spread_total / slippage_total | Attribution sums — **not subtracted again** | trades, analytics |
| Unrealized P&L | Mark-to-market from fill_price | positions.unrealized_pnl |
| Balance | initial + cumulative net realized | portfolios.balance |
| Equity | balance + unrealized_pnl | portfolios.equity |
| Daily P&L | Change in equity over day | Analytics computed |

**Never mix gross and net** in same display without labeling.
**Never double-count spread/slippage** — they live in fill_price AND as attribution fields only.

## 13. Performance by Mode

Analytics filterable by mode (backtest, paper) — never mix in default views.

Backtest detail → backtest mode only.
Portfolio page → paper mode only.

## 14. Cross-References

- DB: `04_DATABASE_MODEL.md` (trades, snapshots)
- Backtest metrics: `08_BACKTESTING_SPEC.md`
- UI display: `11_UI_UX_SPEC.md`
- Paper P&L: `07_PAPER_TRADING_ENGINE.md`
