# QUANTARA — Market Data Specification

## 1. Purpose

Market Data Layer מספקת candles אמינים ל-Strategy, Backtest, Paper, ו-Analytics.
**לא קשורה** לספק יחיד — Adapter pattern חובה.

## 2. Architecture

```
┌─────────────────────────────────────────┐
│           MarketDataService             │
│  (orchestration, validation, storage)   │
└───────────────┬─────────────────────────┘
                │
    ┌───────────┼───────────┐
    ▼           ▼           ▼
┌────────┐ ┌────────┐ ┌────────┐
│Adapter │ │Adapter │ │Adapter │  ... future providers
│   A    │ │   B    │ │   C    │
└────────┘ └────────┘ └────────┘
```

## 3. MarketDataProvider Interface

```python
class MarketDataProvider(Protocol):
    def fetch_candles(
        self,
        instrument: Instrument,
        timeframe: Timeframe,
        start: datetime,
        end: datetime,
    ) -> list[Candle]: ...

    def fetch_latest(
        self,
        instrument: Instrument,
        timeframe: Timeframe,
        count: int = 1,
    ) -> list[Candle]: ...

    def health_check(self) -> ProviderHealth: ...
```

## 4. Candle Data Model

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| instrument_id | UUID | ✅ | |
| timeframe | enum | ✅ | 5m, 15m, 1h |
| timestamp | timestamptz | ✅ | **Open time, UTC** |
| open | decimal | ✅ | |
| high | decimal | ✅ | |
| low | decimal | ✅ | |
| close | decimal | ✅ | |
| volume | decimal | ❌ | When available |
| source | string | ✅ | Provider identifier |
| is_complete | bool | ✅ | false = forming candle |

### 4.1 OHLC Validation

```
low <= open <= high
low <= close <= high
low <= high
All prices > 0
```

Invalid candle → reject, log event, do not store.

## 5. Supported Timeframes (Phase 1)

| Timeframe | Duration | Use Case |
|-----------|----------|----------|
| 5m | 5 minutes | Short-term signals |
| 15m | 15 minutes | Primary for first strategy (optional) |
| 1h | 1 hour | **Primary for Gold Strategy v1** |

## 6. Instrument Metadata (XAU/USD Seed)

```json
{
  "symbol": "XAUUSD",
  "name": "Gold / US Dollar",
  "asset_class": "commodity",
  "base_currency": "XAU",
  "quote_currency": "USD",
  "pip_size": 0.01,
  "contract_size": 100,
  "price_tick_size": 0.01,
  "quantity_step": 0.01,
  "min_quantity": 0.01,
  "default_spread": 0.30,
  "trading_sessions": {
    "primary": {
      "name": "Global",
      "days": ["mon","tue","wed","thu","fri"],
      "nightly_break": {
        "start_utc": "22:00",
        "end_utc": "23:00",
        "note": "Configurable — depends on provider"
      }
    }
  }
}
```

## 7. Timezone Rules

| Rule | Value |
|------|-------|
| Storage | **UTC always** |
| Display | User timezone (default: Asia/Jerusalem) via Settings |
| Candle timestamp | Open time of period, UTC |
| Session calculations | Convert to UTC internally |

**Never** store local time without timezone.

## 8. Trading Sessions & Market Closure

### 8.1 Gold (XAU/USD)

- Trades ~23 hours/day, 5 days/week
- Weekend: no new candles expected Sat/Sun
- Worker should not flag missing candles on weekends

### 8.2 Session Awareness (Strategy Context)

Strategy may receive session info via metadata (not required v1):

```python
context.parameters.get("session_filter")  # optional future param
```

Gold Strategy v1: trades during all available candles (no session filter).

## 9. Data Quality Validation

### 9.1 On Ingest

| Check | Action |
|-------|--------|
| Duplicate timestamp | Skip duplicate, keep first, log warning |
| OHLC invalid | Reject candle |
| Timestamp in future | Reject |
| Gap detection | Log gap, continue (don't fill silently) |
| Price spike (>5% from prev close) | Log warning, store (don't auto-reject) |
| Zero volume | Accept (volume optional for Gold) |

### 9.2 Staleness

```
stale_threshold = timeframe_duration × 2

If latest candle age > stale_threshold during trading hours:
  → Event: data_stale
  → Halt new paper trades
  → UI warning on Market Data / Home
```

### 9.3 Missing Candles

```
Expected candles computed from timeframe grid.
Missing = no row in DB for expected timestamp.

Action:
  - Log gap in events
  - Do NOT interpolate/fill gaps in Phase 1
  - Strategy receives candles with gaps — must handle
  - Backtest: warn if >1% missing in range
```

## 10. Provider Adapter (Phase 1)

Specific provider chosen during implementation. Requirements:

| Requirement | Detail |
|-------------|--------|
| Historical data | For backtesting (months/years) |
| Latest candle | For paper trading |
| XAU/USD support | Required |
| Rate limits | Respect, with backoff |
| Failover | Support priority chain in market_data_providers |

### 10.1 Candidate Providers (Implementation Decision)

To be selected in Phase 2 development — options include:
- Alpha Vantage, Polygon, Twelve Data, OANDA (demo), MetaTrader export

Document choice in implementation README — not locked in planning.

### 10.2 Mock Provider (Development)

```
MockMarketDataAdapter — for tests and offline dev
Generates deterministic candle sequences
```

## 11. Storage Strategy

| Data | Retention |
|------|-----------|
| Candles | Permanent (append-only) |
| Provider health | Last status in market_data_providers |
| Fetch logs | events table |

**Index:** `(instrument_id, timeframe, timestamp DESC)` — primary query pattern.

### 11.1 Backfill

```
On new instrument or gap detected:
  Worker job: fetch_data
  Range: last_stored + 1 period → now
  Batch insert with ON CONFLICT DO NOTHING
```

## 12. Historical Data for Backtest

```
Before backtest run:
  1. Verify candle coverage for requested range
  2. If gaps > threshold → warn user
  3. dataset_fingerprint computed from actual OHLCV content (see 08_BACKTESTING_SPEC)
  4. Do NOT fetch during backtest loop — pre-loaded
```

## 13. Real-Time Data for Paper

```
Worker schedule (per timeframe):
  - Poll provider for latest complete candle
  - Compare with last stored timestamp
  - If new complete candle → store → trigger strategy pipeline
  - Forming candle (is_complete=false) → optional store, never trigger strategy
```

## 14. API Endpoints (Conceptual)

| Endpoint | Purpose |
|----------|---------|
| GET /api/v1/candles | Query candles (instrument, timeframe, start, end) |
| GET /api/v1/market-data/status | Provider health, last update, staleness |
| POST /api/v1/market-data/refresh | Manual fetch trigger |
| GET /api/v1/instruments | List instruments |

## 15. UI Requirements (Market Data / Gold)

- Current price (latest close)
- Last update timestamp
- Provider status (green/yellow/red)
- Candle chart (recent 100 candles)
- Timeframe selector
- Data gap warnings
- Staleness indicator

## 16. Cross-References

- DB: `04_DATABASE_MODEL.md` (candles, instruments, market_data_providers)
- Workers: `13_BACKGROUND_WORKERS.md` (fetch_data job)
- Errors: `14_ERROR_AND_RECOVERY_RULES.md`
- Gold strategy: `10_GOLD_FIRST_STRATEGY.md`
