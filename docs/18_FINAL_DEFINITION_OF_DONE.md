# QUANTARA — Final Definition of Done

## 1. Purpose

מסמך זה מגדיר מתי QUANTARA Phase 1 **הושלם במלואו** ומוכן לשימוש יומיומי.

## 2. Product Definition of Done

### 2.1 Trading Capabilities

| # | Criterion | Verified By |
|---|-----------|-------------|
| 1 | XAU/USD market data fetched automatically (5m, 15m, 1h) | Integration test + UI |
| 2 | Gold Trend Pullback v1.0.0 registered and active | Strategy page |
| 3 | Paper trading runs in background without UI | Worker test |
| 4 | Backtest runs on historical data end-to-end | Backtest detail page |
| 5 | Same strategy code in backtest and paper | Parity test |
| 6 | Risk engine sizes positions per profile | Unit + integration test |
| 7 | SL/TP auto-closes positions | Integration test |
| 8 | fill_price includes spread/slippage; net P&L = gross - fees only | Unit test |
| 9 | Trading can be halted and resumed | Settings + test |
| 10 | All decision types logged | Decision log UI |

### 2.2 Data Integrity

| # | Criterion | Verified By |
|---|-----------|-------------|
| 11 | Trades are immutable (no edit/delete) | DB triggers + code review |
| 12 | Strategy version on every trade and signal | DB query |
| 13 | No duplicate trades on worker retry | Idempotency test |
| 14 | No lookahead in backtest | Dedicated test |
| 15 | UTC storage, local display | Settings + UI check |
| 16 | Decision log is append-only | Code review |

### 2.3 Analytics

| # | Criterion | Verified By |
|---|-----------|-------------|
| 17 | Equity curve displayed | Analytics page |
| 18 | Drawdown calculated correctly | Unit test |
| 19 | Win rate, profit factor, expectancy computed | Unit test |
| 20 | Strategy vs Portfolio performance separated | Analytics UI |
| 21 | Long vs short breakdown | Analytics UI |
| 22 | Backtest metrics match spec | Backtest detail |

### 2.4 UI/UX

| # | Criterion | Verified By |
|---|-----------|-------------|
| 23 | Home/Today answers all key questions | Manual review |
| 24 | All 13 screens functional | E2E checklist |
| 25 | Hebrew UI via i18n keys | Code review |
| 26 | Responsive on mobile | Manual test |
| 27 | Empty states with guidance | Manual review |
| 28 | Error/halt banners displayed | Error scenario test |

### 2.5 Infrastructure

| # | Criterion | Verified By |
|---|-----------|-------------|
| 29 | Docker Compose starts full stack | `docker compose up` |
| 30 | Migrations apply cleanly | Fresh DB test |
| 31 | Seed data loads | Seed script |
| 32 | Workers recover from crash | Crash test |
| 33 | Stale data detection halts trading | Integration test |
| 34 | Provider failure handled gracefully | Integration test |

### 2.6 Architecture Compliance

| # | Criterion | Verified By |
|---|-----------|-------------|
| 35 | No trading logic in UI | Code review |
| 36 | Strategy doesn't know mode | Code review |
| 37 | Market data adapter abstracted | Code review |
| 38 | Broker adapter abstracted (Paper only) | Code review |
| 39 | Module boundaries respected | Code review |
| 40 | New strategy can be added without core changes | Registry test |

## 3. Documentation Definition of Done (Planning Phase)

Planning phase complete when:

- [x] All 20 docs exist in /docs
- [x] CURSOR_INSTRUCTIONS.md exists
- [x] README.md exists
- [x] No contradictions between docs
- [x] All entities have clear ownership
- [x] All UI screens have backend source
- [x] No blocking product decisions remain

## 4. Explicit Non-Requirements (Phase 1)

These are **NOT** part of Done:

- Live trading
- MT5 connection
- AI features
- Additional instruments beyond XAU/USD
- SaaS/auth/billing
- Walk-forward optimization
- Full experiments comparison UI
- English UI (prepared but not required)
- Sharpe ratio / advanced risk metrics
- Mobile native app
- CI/CD pipeline
- Production deployment

## 5. Sign-Off Process

```
1. Developer completes all Phase 1-8 acceptance criteria
2. All tests pass
3. Owner reviews:
   a. Home/Today dashboard live
   b. Run backtest on 1 year data
   c. Observe paper trading for 24 hours
   d. Review decision log completeness
   e. Verify analytics accuracy
4. Owner sign-off → Phase 1 COMPLETE
```

## 6. Post-Completion

After sign-off, next steps are **owner decision**:

- Tune strategy parameters (→ v1.1.0)
- Add instruments
- Connect MT5 demo
- Build experiments comparison
- See `19_FUTURE_NOT_NOW.md`

## 7. Cross-References

- Testing details: `17_TESTING_AND_ACCEPTANCE.md`
- Dev phases: `16_DEVELOPMENT_PHASES.md`
- Product scope: `01_PRODUCT_SCOPE.md`
