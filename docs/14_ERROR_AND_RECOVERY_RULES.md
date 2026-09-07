# QUANTARA — Error Handling & Recovery Rules

## 1. Principles

1. **Never silently fail** — every error logged to events + worker_jobs
2. **Never corrupt trading state** — transactions, idempotency
3. **Never duplicate trades** — idempotency keys on all write operations
4. **Fail safe** — on uncertainty, halt new trades (don't halt SL/TP on open positions)
5. **Never modify history** — errors don't justify editing trades
6. **Recoverable vs fatal** — distinguish clearly

## 2. Error Categories

| Category | Severity | Example | Action |
|----------|----------|---------|--------|
| DATA | warning → critical | Stale candles, provider down | Halt new trades |
| STRATEGY | error | Exception in evaluate() | Skip candle, log, continue |
| RISK | warning | Denied signal | Log decision, continue |
| EXECUTION | error | Fill simulation failure | Skip order, log, no partial state |
| SYSTEM | critical | DB unreachable | Halt all, alert |
| WORKER | error | Job failure | Retry per policy |

## 3. Market Data Errors

### 3.1 Provider Failure

```
Attempt 1: immediate retry
Attempt 2: wait 30s
Attempt 3: wait 60s
After 3 failures:
  → Event: provider_error (critical)
  → Halt new paper trades
  → Existing SL/TP still monitored on last known price
  → UI: red status on Market Data + Home
```

Recovery: auto-resume when provider health_check passes + fresh candle received.
Manual override: Settings → Resume trading.

### 3.2 Stale Data

```
stale = (now - last_candle.timestamp) > (timeframe × 2)

During expected trading hours:
  → Halt new trades
  → Event: data_stale
  → UI warning

Weekend (Sat/Sun for Gold):
  → Do NOT flag stale
  → Do NOT halt
```

### 3.3 Invalid Candle

```
OHLC validation fails:
  → Reject candle (don't store)
  → Log event: invalid_candle
  → Do NOT trigger strategy for this fetch
  → Continue polling
```

### 3.4 Duplicate Candle

```
UNIQUE constraint violation on insert:
  → Skip (idempotent)
  → Log info (not error)
  → Do NOT re-trigger strategy
```

### 3.5 Data Gap

```
Missing expected candle detected:
  → Log event: data_gap (warning)
  → Continue with available data
  → Strategy handles gap (may return INSUFFICIENT_DATA or NO_SETUP)
  → Backtest: warn if gap > 1% of range
  → Do NOT interpolate/fill
```

## 4. Strategy Errors

### 4.1 Exception in evaluate()

```
Catch exception:
  → Log full stack trace to events (severity: error)
  → Write decision: decision_type = system_error, message = exception summary
  → Do NOT create signal
  → Do NOT retry same candle
  → Continue to next instance / next candle
  → Do NOT halt entire system (single strategy failure)
```

### 4.2 Invalid Parameters

```
Parameter validation fails at startup:
  → Strategy instance cannot activate
  → UI shows validation errors
  → No worker processing for this instance
```

## 5. Risk Engine Errors

### 5.1 Denied Signal

Not an error — normal operation. Log as RISK_DENIED.

### 5.2 Halt Triggered

```
Drawdown or daily loss breach:
  → portfolio.status = halted
  → portfolio.halt_reason = specific reason
  → Event: trading_halted
  → All new signals denied with TRADING_HALTED
  → Open positions: SL/TP still active
  → UI: prominent halt banner
```

Recovery:
- Drawdown halt: manual resume only (Settings)
- Daily loss halt: auto-resume at 00:00 UTC next day OR manual
- Manual halt: manual resume only
- System halt: auto-resume when root cause fixed + manual confirm

### 5.3 Risk Calculation Error

```
e.g., division by zero in sizing:
  → Deny signal: RISK_DENIED: CALCULATION_ERROR
  → Log error event
  → Do NOT submit order
```

## 6. Execution Errors

### 6.1 Insufficient Available Capital

```
Exposure or available_capital cap prevents intended quantity:
  → DENY: INSUFFICIENT_AVAILABLE_CAPITAL
  → Log decision with target vs actual risk if partially sized down to zero
  → No order created
```

### 6.2 Partial State Prevention

```
All execution steps in DB transaction:
  BEGIN
    Create order
    Create fill
    Create/update position
    Update portfolio balance (on trade close only)
  COMMIT

On any failure → ROLLBACK entire transaction
No orphaned orders or fills
```

### 6.3 Duplicate Order

```
Idempotency key exists on order_intent:
  → Skip execution
  → Log info: duplicate_intent_skipped
```

## 7. Worker Errors

### 7.1 Job Failure

```
Job fails:
  → attempts += 1
  → If attempts < max_attempts: reschedule with backoff
  → If attempts >= max_attempts:
    → status = failed
    → error_message stored
    → Event logged
    → For critical jobs (fetch_data): may trigger system halt
```

### 7.2 Worker Crash

```
Process crash (OOM, kill):
  → Docker restart: unless-stopped
  → On startup: check for jobs stuck in "running" > 10 min
  → Reset to pending (with attempt count preserved)
  → Log event: worker_restarted
  → Idempotency prevents duplicate processing
```

### 7.3 Backtest Failure

```
Mid-backtest failure:
  → backtest_run.status = failed
  → error_message stored
  → Partial trades preserved but marked with invalid flag
  → Metrics NOT computed
  → UI shows failed status with error
  → User can retry (creates new backtest_run)
```

## 8. Database Errors

| Error | Action |
|-------|--------|
| Connection lost | Retry 3× with backoff, then critical event |
| Constraint violation (non-idempotency) | Log error, rollback, investigate |
| Deadlock | Retry transaction once |
| Disk full | Critical halt, all workers stop |

## 9. UI Error Display

| Error Type | UI Behavior |
|------------|-------------|
| Data stale | Yellow/red banner on Home + Market Data |
| Trading halted | Red banner with reason + Resume button |
| Worker down | Status indicator red on Home |
| Backtest failed | Error message on Backtest Detail |
| API unreachable | Full-page error with retry |

UI shows user-friendly Hebrew messages. Technical details in expandable section.

## 10. Event Log Structure

```json
{
  "event_type": "data_stale",
  "entity_type": "instrument",
  "entity_id": "uuid",
  "severity": "warning",
  "payload": {
    "instrument": "XAUUSD",
    "timeframe": "1h",
    "last_candle": "2026-09-07T10:00:00Z",
    "threshold_minutes": 120
  }
}
```

## 11. Recovery Checklist

After any halt, before resume:

- [ ] Root cause identified and fixed
- [ ] Provider health check passes
- [ ] Latest candle is fresh
- [ ] No stuck jobs in "running"
- [ ] Portfolio state consistent (equity = balance + unrealized; exposure tracked separately)
- [ ] User confirms resume (except auto daily-loss recovery)

## 12. What We Never Do

| Forbidden Action | Why |
|-----------------|-----|
| Delete trades to "fix" results | History integrity |
| Edit trade P&L retroactively | Audit trail |
| Re-run strategy on past candles silently | Creates duplicate signals |
| Skip idempotency "just this once" | Duplicate trade risk |
| Disable SL/TP because of errors | Open position protection |
| Catch-all swallow exceptions | Silent failures |

## 13. Cross-References

- Workers retry: `13_BACKGROUND_WORKERS.md`
- Data validation: `09_MARKET_DATA_SPEC.md`
- Halt rules: `06_RISK_ENGINE.md`
- Idempotency: `03_CANONICAL_TRADING_FLOW.md`
