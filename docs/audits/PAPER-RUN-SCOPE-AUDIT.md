# Paper Run Scope Audit — Pre-0007 Gate

Audit date: 2026-09-12  
Scope helpers: `quantara_engine.competition.paper_run` (`position_scope_clause`, `trade_scope_clause`, `intent_scope_clause`, `decision_scope_clause`, `signal_scope_clause`, `snapshot_scope_clause`)

When `paper_runs` + `paper_run_id` columns exist and an active run is set, **SCOPED** queries filter `paper_run_id = current_run`.  
When 0007 is not applied or no active run, fallback is `backtest_run_id IS NULL` (paper mode).

## Current-run queries — SCOPED

| Area | Location | Tables | Scope |
|------|----------|--------|-------|
| Open position count (Home) | `store.count_open_competition_positions` | positions | SCOPED |
| Open position financials / sync | `store.sum_open_position_financials_for_portfolio_ids` | positions | SCOPED |
| Portfolio PM load | `store.load_portfolio_state` | positions, trades, snapshots | SCOPED |
| Snapshot job load | `store.load_portfolio_for_snapshot` | positions | SCOPED |
| Batch PM | `store.batch_load_portfolio_states` | positions | SCOPED |
| List open positions | `store.list_positions` (open_only) | positions | SCOPED |
| Performance snapshots | `store.list_snapshots` | portfolio_snapshots | SCOPED |
| Today counters | `store.get_competition_today_stats` | decisions, intents, positions, trades | SCOPED |
| Pending intents (PM) | `store.list_pending_intents*` | order_intents | SCOPED |
| Realized sums | `store.sum_realized_pnl_for_portfolio_ids` | trades | SCOPED |
| Batch open positions | `batch_summary.batch_open_positions_by_portfolio` | positions | SCOPED |
| Batch entry risk | `batch_summary.batch_entry_actual_risk_by_position` | order_intents | SCOPED |
| Exposure / risk dashboard | `batch_summary.batch_competition_exposure_risk_summary` | positions | SCOPED |
| Asset metrics | `batch_summary.batch_asset_metrics` | positions, trades | SCOPED |
| List positions (batch) | `batch_summary.list_positions_for_portfolios` | positions | SCOPED |
| Trade batch metrics | `batch_summary.batch_trade_metrics_by_portfolio` | trades | SCOPED |
| Global open risk | `open_risk_guard.load_global_risk_context` | positions | SCOPED |
| Persist stamp | `stamp_paper_run_id` on save | positions, trades, intents, decisions, signals, snapshots | STAMP on write |

## Deliberately historical / explicit — HISTORICAL

| Area | Location | Notes |
|------|----------|-------|
| Backtest runs | `store.list_backtest_*`, `backtest_run_id = :id` | Explicit run id |
| Legacy competition archive | `list_all_competition_entries` (includes archived) | Historical experiment metadata |
| Replay V5 read-only | `broker/replay_v5.py` | All competition history `backtest_run_id IS NULL` — audit only |
| Journal / closed trade lists with date filters | `store.list_recent_trades` etc. | May span runs; UI labels historical |
| Owner recovery | `packages/db/owner/0006_owner_recovery.sql` | Manual, not runtime |

## Ambiguous — NONE remaining for current-run paths

All identified competition **current-run** loaders for balance, equity, risk, exposure, SL/TP, flatten, PM, and Home counters now use paper-run scope helpers when 0007 is active.

## Migration / reset

- `0007_paper_competition_runs.sql`: adds `paper_run_id` to positions, trades, order_intents, portfolio_snapshots, decisions, signals + indexes.
- `scripts/paper_broker_reset.py`: first-generation path retires legacy `paper_run_id IS NULL` open positions and pending intents before creating active run.
