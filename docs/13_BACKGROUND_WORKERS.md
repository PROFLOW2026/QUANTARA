# QUANTARA — Background Workers

## 1. Purpose

QUANTARA **חייבת** לפעול כשה-UI סגור.

Workers מבצעים: fetch data, run strategies, execute paper orders, manage positions, snapshots.

## 2. Architecture

```
┌─────────────────────────────────────────┐
│              Scheduler                   │
│         (APScheduler — Phase 1)        │
└───────────────┬─────────────────────────┘
                │
    ┌───────────┼───────────┬──────────────┐
    ▼           ▼           ▼              ▼
┌────────┐ ┌────────┐ ┌────────┐   ┌──────────┐
│ Fetch  │ │Strategy│ │ SL/TP  │   │ Backtest │
│ Data   │ │Pipeline│ │ Check  │   │ Runner   │
└────────┘ └────────┘ └────────┘   └──────────┘
                │
                ▼
        ┌──────────────┐
        │  PostgreSQL   │
        │  worker_jobs  │
        └──────────────┘
```

## 3. Worker Processes (Phase 1)

| Worker | Schedule | Purpose |
|--------|----------|---------|
| `data_fetcher` | Every 1 min | Poll providers for new candles |
| `strategy_runner` | Triggered by new candle | Strategy → Risk → Execute |
| `sl_tp_checker` | With strategy_runner | Check open positions |
| `snapshot_creator` | After strategy_runner | Portfolio snapshots |
| `backtest_runner` | On demand (job queue) | Run backtest jobs |
| `health_monitor` | Every 5 min | Provider health, staleness, halt checks |
| `cleanup` | Daily | Archive old worker_jobs (>30 days) |

## 4. Job Queue (worker_jobs table)

### 4.1 Job Types

```python
class JobType(str, Enum):
    FETCH_DATA = "fetch_data"
    RUN_STRATEGY = "run_strategy"
    CHECK_SL_TP = "check_sl_tp"
    CREATE_SNAPSHOT = "create_snapshot"
    RUN_BACKTEST = "run_backtest"
    HEALTH_CHECK = "health_check"
```

### 4.2 Job Lifecycle

```
pending → running → completed
                  → failed (retry if attempts < max)
```

| Field | Purpose |
|-------|---------|
| idempotency_key | Prevent duplicates |
| payload | Job-specific JSON |
| attempts | Retry count |
| max_attempts | Default 3 |
| scheduled_at | When to run |

### 4.3 Idempotency Keys

| Job | Key Format |
|-----|------------|
| fetch_data | `fetch:{instrument}:{timeframe}:{timestamp}` |
| run_strategy | `strategy:{instance_id}:{candle_timestamp}` |
| check_sl_tp | `sltp:{portfolio_id}:{candle_timestamp}` |
| snapshot | `snapshot:{portfolio_id}:{candle_timestamp}` |
| backtest | `backtest:{backtest_run_id}` |

**Rule:** INSERT with ON CONFLICT (idempotency_key) DO NOTHING.
If conflict → skip silently, log info.

## 5. Data Fetcher Worker

```
Schedule: every 60 seconds

For each active instrument + timeframe (5m, 15m, 1h):
  1. Call MarketDataProvider.fetch_latest()
  2. Validate candle (OHLC, timestamp)
  3. Check duplicate → skip if exists
  4. Store candle
  5. If is_complete AND new timestamp:
     → Enqueue run_strategy job
     → Enqueue check_sl_tp job
  6. Update provider health
  7. Log event
```

## 6. Strategy Runner Worker

```
Trigger: run_strategy job

Payload: { strategy_instance_id, candle_timestamp, instrument_id, timeframe }

Steps:
  1. Idempotency check
  2. Load strategy instance + version + parameters
  3. Load candles up to candle_timestamp (NO future)
  4. Instantiate Strategy from registry
  5. strategy.evaluate(candles, context) → Signal
  6. Write signal record
  7. Write decision log (always)
  8. If signal.action in (buy, sell):
     → RiskEngine.evaluate() → OrderIntent | DENIED
     → Write decision log (include target_risk_amount + actual_risk_amount)
     → If approved: queue intent for next candle open (pending_execution)
  9. If signal.action == close:
     → Queue close for next open (default) or immediate SL/TP path
  10. Mark job completed

Note: Actual fill at candle N+1 open is executed when processing candle N+1 (step 1 of that cycle).
```

## 7. SL/TP Checker Worker

```
Trigger: check_sl_tp job (same candle as strategy)

For each open position in paper portfolio:
  1. Load current candle OHLC
  2. Check SL/TP triggers (see 07_PAPER_TRADING_ENGINE.md)
  3. If triggered: execute exit, create trade
  4. Write decision log (SL_TRIGGERED / TP_TRIGGERED)
  5. Idempotency: one exit per position per candle
```

## 8. Snapshot Creator

```
Trigger: after strategy + sl_tp complete for a candle cycle

For each active paper portfolio:
  1. Compute equity, drawdown, unrealized P&L
  2. Insert portfolio_snapshot
  3. Check halt conditions (drawdown, daily loss)
  4. Update portfolio.peak_equity if needed
```

## 9. Backtest Runner

```
Trigger: user creates backtest via UI → job enqueued

Payload: { backtest_run_id }

Steps:
  1. Load backtest config
  2. Create virtual portfolio
  3. Load all candles for range
  4. Loop candle-by-candle (see 08_BACKTESTING_SPEC.md)
  5. Compute metrics
  6. Update backtest_run status → completed
  7. On error → status failed, preserve error message

Runs in background — UI polls status.
Long backtests: update progress in backtest_run metadata.
```

## 10. Health Monitor

```
Schedule: every 5 minutes

Checks:
  1. Provider last_success_at — stale?
  2. Latest candle age per instrument/timeframe
  3. Worker last run times
  4. Failed jobs in last hour
  5. Portfolio halt status

Actions:
  - Emit events for UI
  - Auto-halt if data stale during trading hours
  - Log warnings
```

## 11. Concurrency & Locking

Phase 1 (single user, single instance):

| Concern | Solution |
|---------|----------|
| Same candle processed twice | Idempotency key UNIQUE |
| Parallel strategy instances | Sequential per candle (simple) |
| Backtest + Paper simultaneously | Allowed — different portfolios |
| DB race conditions | Transaction per job, SELECT FOR UPDATE on portfolio |

Future: Redis lock if multi-instance deployment.

## 12. Worker Status API

```
GET /api/v1/workers/status

Response:
{
  "workers": {
    "data_fetcher": { "last_run": "...", "status": "healthy", "next_run": "..." },
    "strategy_runner": { "last_run": "...", "jobs_pending": 0 },
    "backtest_runner": { "active_jobs": 1 }
  },
  "data_health": {
    "XAUUSD_1h": { "last_candle": "...", "stale": false }
  },
  "trading_status": "active" | "halted"
}
```

Displayed on Home / Today screen.

## 13. Failure & Retry

| Failure | Retry | Max Attempts | After Max |
|---------|-------|--------------|-----------|
| Provider timeout | Yes, exponential backoff | 3 | Halt + event |
| Strategy exception | No retry same candle | 1 | Log error, continue |
| DB connection | Yes | 3 | Critical event |
| Risk engine error | No | 1 | Log, skip trade |
| Backtest crash | No auto-retry | 1 | Mark failed |

See `14_ERROR_AND_RECOVERY_RULES.md`.

## 14. Deployment

```yaml
# docker-compose.yml (conceptual)
services:
  worker:
    command: python -m workers.main
    depends_on: [postgres, engine]
    restart: unless-stopped
```

Single worker process runs all schedulers in Phase 1.

## 15. Logging

Each worker run creates `worker_runs` record:

```json
{
  "worker_name": "strategy_runner",
  "started_at": "...",
  "completed_at": "...",
  "status": "success",
  "jobs_processed": 3,
  "errors": []
}
```

## 16. Cross-References

- Idempotency in flow: `03_CANONICAL_TRADING_FLOW.md`
- Paper cycle: `07_PAPER_TRADING_ENGINE.md`
- Backtest: `08_BACKTESTING_SPEC.md`
- Errors: `14_ERROR_AND_RECOVERY_RULES.md`
- DB: `04_DATABASE_MODEL.md` (worker_jobs, worker_runs)
