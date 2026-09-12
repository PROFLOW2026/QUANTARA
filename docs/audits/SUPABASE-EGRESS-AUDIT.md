# Supabase Egress Audit — QUANTARA

**Date:** 2026-09-12  
**Base commit:** b6c8fea  
**Owner DB access during audit:** code-path analysis only (no table downloads)

## Executive summary

Steady-state egress was dominated by **repeated full candle window reads** on the 5-minute worker cycle, amplified by **duplicate status queries** and **unbounded processed-decision history scans**. Home dashboard polling added a secondary read multiplier while tabs were hidden.

Optimizations in this closure:

1. Worker in-memory candle working set (warm once, incremental tail)
2. Single shared candle window per strategy cycle (no extra 500-row status fetch)
3. Bounded `fully_processed_candle_timestamps(since=window_start)`
4. Runtime portfolio state without trade/snapshot history on live path
5. PM candle prefetch via shared working set
6. Snapshot dedupe (material change or 60m heartbeat)
7. Home poll pauses when `document.visibilityState !== "visible"` + 45s API TTL cache

---

## Top recurring reads (before)

| Rank | Callsite | Frequency | Rows/call | Calls/24h | Est. rows/day |
|------|----------|-----------|-----------|-----------|---------------|
| 1 | `run_strategy._ensure_candles` → `list_recent_candles(limit=250)` | per asset×TF group / 5m | ~250 OHLCV + ORM metadata | 8×3×288 ≈ **6,912** | **~1.73M** |
| 2 | `get_timeframe_execution_status` → `list_recent_candles(limit=500)` | up to 4× per group / 5m | ~500 | ~8×3×4×288 ≈ **27,648** | **~13.8M** |
| 3 | `fully_processed_candle_timestamps` (unbounded) | per group / 5m | growing decision timestamps | ~8×3×288 ≈ **6,912** | **~350k–2M+** (history-dependent) |
| 4 | `load_portfolio_state` (template eval) | per group / 5m | open + **all trades + all snapshots** | ~8×3×288 ≈ **6,912** | **~500k–5M+** |
| 5 | `position_management._prefetch_candles_by_pair` | per PM cycle / 5m | up to 200/position pair | ~288 | **~50k–500k** |
| 6 | Home dashboard poll (`useHomeDashboardPoll`) | 8 endpoints / 60s | varies | **1,440 refreshes** | **~200k–1M** |
| 7 | `snapshot_job` writes + later reads | 160 snapshots / 5m | 1 row write | 46,080/day | writes; amplifies future reads |

**Estimated old steady-state DB result payload:** **~8–10 GB/day** (consistent with Supabase Fair Use warning at ~20 GB / 2 days).

---

## After optimization (steady state, post-warmup)

| Metric | Target | Mechanism |
|--------|--------|-----------|
| Candle rows / 5m cycle (8 assets, 3 TF) | **≤100** | incremental tail only |
| Repeated trade-history rows / live cycle | **0** | `batch_load_portfolio_states` + `load_portfolio_runtime_state` |
| Repeated snapshot-history rows / live cycle | **0** | runtime state excludes snapshots |
| Processed-decision rows scanned | **window-bounded** | `since=working_window_start` |
| Snapshot inserts / day | **≪ 46,080** | material change or 60m heartbeat |
| Home poll while hidden | **0** | Page Visibility API |
| Duplicate `/analytics/today` within 45s | **0** | in-memory TTL cache |

**Estimated new steady-state payload:** **~30–80 MB/day** (well under Free 5 GB/month quota).

---

## Callsite classification

### LIVE HOT PATH (optimized)

| Function | File | Change |
|----------|------|--------|
| `_ensure_candles` | `run_strategy.py` | → `ensure_strategy_candles` + `WorkerCandleCache` |
| `get_timeframe_execution_status` | `store.py` | accepts preloaded candles/processed |
| `fully_processed_candle_timestamps` | `store.py` | optional `since` bound |
| `list_recent_candles` / `list_candles` | `store.py` | OHLCV-only column select |
| `_prefetch_candles_by_pair` | `position_management.py` | shared working set |
| `snapshot_job` | `snapshot.py` | dedupe before insert |
| Home poll | `useHomeDashboardPoll.ts` | visibility-gated |

### MANUAL UI / API (cached, not worker)

| Endpoint | Cache TTL |
|----------|-----------|
| `/analytics/today` | 45s |
| `dashboard_candle_bundle` | 45s |

### BACKTEST / REPLAY / AUDIT (unchanged)

Scripts under `scripts/_*.py` retain full reads for forensic use — not on live worker schedule.

---

## Trading output equivalence

Data-loading changes only. Catch-up indices and backlog status computed from the **same candle window** and **same processed set within that window**. Regression tests:

- `tests/test_candle_cache_equivalence.py`
- `tests/test_egress_budget.py`

---

## Owner DB policy during closure

- **Reads:** metadata/code analysis only
- **Writes:** 0
- **Load tests:** local PostgreSQL 17 / disposable test DB only

---

## Remaining egress sources (monitor)

1. **fetch_live / fetch_bulk** — necessary market data ingestion (writes + read-back for derivation)
2. **First worker warmup** after restart — one bounded lookback per instrument×TF (expected)
3. **Manual UI exploration** — user-driven, not scheduled

No architectural database migration required.
