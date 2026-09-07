# QUANTARA — Testing & Acceptance

## 1. Testing Strategy

| Level | Scope | Tools |
|-------|-------|-------|
| Unit | Strategy, Risk, P&L, Fill calc, Validation | pytest |
| Integration | Full pipeline with mock data | pytest + test DB |
| API | Engine endpoints | pytest + httpx |
| E2E | UI critical paths | Playwright (Phase 8) |
| Parity | Backtest vs Paper same decisions | Custom test |

**Principle:** Test behavior from specs, not implementation details.

## 1.1 Testing Philosophy — Private Project

QUANTARA is a **private personal system**. Tests should be **good and focused** on what actually matters:

| Priority | Area |
|----------|------|
| ✅ | P&L correctness (no double-counting spread/slippage) |
| ✅ | Portfolio accounting (balance / equity / unrealized) |
| ✅ | Position sizing (target vs actual risk) |
| ✅ | Spread/slippage in fill_price |
| ✅ | SL/TP triggers |
| ✅ | No lookahead + next_open execution |
| ✅ | Backtest/Paper parity |
| ✅ | Duplicate trade prevention |
| ✅ | Canonical trading flow |
| ✅ | Core DB integrity |

**Avoid:** endless tests, theoretical edge cases, enterprise hardening, security bureaucracy, QA that doesn't add real value for private use.

**Prefer:** practical correctness and progress over perfection. It's OK to build, discover errors, and fix.

E2E (Playwright) — minimal smoke tests in Phase 8 only, not exhaustive.

## 2. Unit Tests — Strategy

### 2.1 Gold Trend Pullback v1.0.0

| Test Case | Input | Expected |
|-----------|-------|----------|
| Insufficient data | 50 candles | HOLD, INSUFFICIENT_DATA |
| No trend (below EMA200) | 200 candles, downtrend | HOLD, NO_SETUP |
| Valid long setup | Uptrend + pullback fixture | BUY with SL/TP |
| Valid short setup | Downtrend + rally fixture | SELL with SL/TP |
| RSI out of range | Trend ok, RSI=75 | HOLD, NO_SETUP |
| Trend reversal exit | EMA50 crosses below EMA200 | CLOSE signal |
| No future data | Mutate future candles | Past signals unchanged |
| Deterministic | Same input twice | Identical output |

### 2.2 Strategy Framework

| Test Case | Expected |
|-----------|----------|
| Registry lookup | Returns correct class by slug+version |
| Parameter validation | Rejects invalid params via JSON Schema |
| Unknown strategy | Raises not found |

## 3. Unit Tests — Risk Engine

| Test Case | Expected |
|-----------|----------|
| Approved long | OrderIntent with correct quantity |
| Denied — max positions | RISK_DENIED: MAX_OPEN_POSITIONS |
| Denied — exposure | RISK_DENIED: MAX_EXPOSURE |
| Denied — daily loss | RISK_DENIED: DAILY_LOSS_LIMIT |
| Halt on drawdown | Portfolio halted |
| Denied — missing SL | RISK_DENIED: INVALID_STOP_LOSS |
| SL too tight | RISK_DENIED |
| Conservative sizing | 0.5% risk → smaller quantity than Aggressive |
| Cross-strategy exposure | Denied when combined exposure exceeded |
| CLOSE signal | Bypasses sizing, uses position quantity |

### 3.1 Position Sizing Verification

```
Given: equity=$10,000, target risk=1%, entry_ref=2650, SL=2637.65
SL distance = 12.35
target_risk_amount = $100
desired_quantity = 100 / 12.35 ≈ 8.09 units

If exposure cap limits to $40 actual risk:
  actual_risk_amount = $40
  Verify both target and actual stored on OrderIntent
```

### 3.2 No Double-Counting

| Test Case | Expected |
|-----------|----------|
| Long round-trip P&L | net = gross - fees only; spread/slippage NOT subtracted again |
| Attribution fields | spread_total + slippage_total populated but net unchanged if removed |

## 4. Unit Tests — Execution & P&L

| Test Case | Expected |
|-----------|----------|
| Long entry fill (next_open) | base = candle[N+1].open; fill_price includes spread+slippage |
| Long exit fill | fill_price = base - spread/2 - slippage |
| SL trigger (long) | Exit when candle.low <= SL |
| TP trigger (long) | Exit when candle.high >= TP |
| SL+TP same candle | SL wins |
| LONG gross P&L | (exit_fill - entry_fill) × qty |
| SHORT gross P&L | (entry_fill - exit_fill) × qty |
| Net P&L | gross - entry_fees - exit_fees |
| Balance on close | balance += net_pnl only (no notional deduction on entry) |
| Equity | balance + unrealized_pnl |
| Idempotent order | Second submit skipped |
| next_open timing | Signal on N does not fill until N+1 open |

## 5. Unit Tests — Market Data

| Test Case | Expected |
|-----------|----------|
| Valid OHLC | Accepted |
| Invalid OHLC (high < low) | Rejected |
| Duplicate timestamp | Skipped (idempotent) |
| Future timestamp | Rejected |
| Gap detection | Event logged, no fill |
| Dataset fingerprint stability | Same candles → same hash; one OHLC change → different hash |

## 6. Integration Tests

### 6.1 Full Paper Pipeline

```
Setup: Mock provider, fresh paper portfolio, active strategy instance

1. Inject candle N → no setup → decision logged
2. Inject candle N → buy signal → risk approved → intent pending (not filled yet)
3. Inject candle N+1 → execute at N+1 open → position opened
4. Inject candle → SL hit → trade closed → balance += net_pnl
5. Verify: equity = balance + unrealized; no spread/slippage double-count; no duplicates
```

### 6.2 Full Backtest Pipeline

```
Setup: 500 mock candles, backtest config

1. Run backtest
2. Verify: status=completed, metrics populated, trades recorded
3. Verify: no candle processed twice
4. Verify: strategy_version referenced on all trades
```

### 6.3 Backtest/Paper Parity

```
1. Load same 30-day candle set
2. Run backtest with params X
3. Run paper simulation (offline, not live) with same candles + params X
4. Assert: identical signals at each candle
5. Assert: identical trades (given same execution assumptions)
```

### 6.4 Worker Idempotency

```
1. Process same candle job twice
2. Assert: only one signal, one trade (if applicable)
3. Assert: no duplicate portfolio snapshots
```

### 6.5 Halt & Recovery

```
1. Trigger drawdown halt
2. Inject buy signal → denied (TRADING_HALTED)
3. Inject SL hit on existing position → still closes
4. Resume trading
5. Inject buy signal → processed normally
```

## 7. API Tests

| Endpoint | Test |
|----------|------|
| GET /health | 200 |
| GET /candles | Returns filtered candles |
| GET /portfolio | Returns equity, balance |
| GET /decisions | Paginated, filterable |
| POST /backtests | Creates run, returns ID |
| GET /backtests/{id} | Returns metrics when complete |
| GET /analytics/portfolio | Returns metric object |
| GET /workers/status | Returns health info |

## 8. E2E Tests (Phase 8)

| Flow | Steps |
|------|-------|
| Home dashboard | Load → see portfolio summary |
| Create backtest | Form → submit → see in list → view detail |
| Decision log | Filter by type → see results |
| Settings | Change risk profile → saved |

## 9. Acceptance Criteria — Full System

System accepted when ALL pass:

### 9.1 Core Pipeline
- [ ] Market data fetched and stored automatically
- [ ] Strategy evaluates on each new candle
- [ ] All decisions logged (including HOLD/NO_SETUP)
- [ ] Risk engine approves/denies correctly
- [ ] Paper trades execute with spread/slippage
- [ ] SL/TP closes positions
- [ ] P&L accurate (realized + unrealized)

### 9.2 Backtesting
- [ ] Backtest runs end-to-end
- [ ] All metrics computed
- [ ] No lookahead bias (test proven)
- [ ] Reproducible results

### 9.3 Parity
- [ ] Same strategy code in backtest and paper
- [ ] Same risk code in backtest and paper
- [ ] Parity test passes

### 9.4 Integrity
- [ ] No duplicate trades on retry
- [ ] Trade history immutable
- [ ] Strategy version on every trade
- [ ] Idempotency on all write operations

### 9.5 UI
- [ ] All screens from `11_UI_UX_SPEC.md` functional
- [ ] Home answers all "Today" questions
- [ ] Hebrew UI with i18n keys
- [ ] Responsive on mobile

### 9.6 Background
- [ ] Workers run when UI closed
- [ ] Health monitoring works
- [ ] Error recovery per spec

### 9.7 Analytics
- [ ] All v1 metrics computed server-side
- [ ] Strategy vs Portfolio separation
- [ ] Expectancy uses signed average_loss convention
- [ ] Default fill_timing is next_open (Paper + Backtest)

## 10. Test Data

| Fixture | Location |
|---------|----------|
| Uptrend candles | tests/fixtures/candles_uptrend.json |
| Downtrend candles | tests/fixtures/candles_downtrend.json |
| Pullback setup | tests/fixtures/candles_pullback_long.json |
| SL trigger | tests/fixtures/candles_sl_hit.json |
| 500 candle backtest set | tests/fixtures/candles_backtest_500.json |

Mock provider returns fixtures deterministically.

## 11. Cross-References

- Final DoD: `18_FINAL_DEFINITION_OF_DONE.md`
- Dev phases: `16_DEVELOPMENT_PHASES.md`
- All module specs for behavior reference
