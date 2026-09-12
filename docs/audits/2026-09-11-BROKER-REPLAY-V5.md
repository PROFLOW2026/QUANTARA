# Broker Replay V5 — 2026-09-11

## Summary
- Historical entries in DB: 274
- Historical exits in DB: 140
- Broker accepted entries: 252
- Broker rejected entries: 22
- Valid physical exits: 126
- Shadow-only exits: 14
- Orphan/unexecuted exits skipped: 14

## Rejection reasons
- max_asset_exposure: 2
- max_gross_leverage: 10
- short_not_allowed: 10

## Methodology
- Mark timing: bar_close_timestamp(5m)
- Freshness: data_fresh_for_instrument(5m)
- Unknown market session rejections: 0
- Stale data rejections: 0
- Market closed rejections: 0

## Ending state
- Ending cash: $311,756.33
- Ending balance: $319,074.84
- Ending equity: $319,886.43

## Physical broker accounting
- Physical gross realized: $-925.16
- Fees: $0.00
- Physical net realized: $-925.16
- Physical unrealized: $811.59
- Physical total P&L: $-113.57

## Strategy attribution accounting
- Attributed strategy realized: $-1,009.38
- Attributed strategy unrealized: $895.84
- Attributed total P&L: $-113.54

## Dual-layer basis reconciliation
- REALIZED BASIS DIFFERENCE (physical − attributed): $84.22
- UNREALIZED BASIS DIFFERENCE (attributed − physical): $84.25
- Basis carry check (realized − unrealized): $-0.03
- TOTAL P&L RECONCILIATION (physical − attributed): $-0.03
- **EXPECTED NETTING BASIS CARRY** — realized difference explained by remaining-lot basis; not a failure.

- Financial reconciliation (balance): 0.00
- Cash reconciliation: 0.00
- Initial margin reconciliation: 0.00
- Maintenance margin reconciliation: 0.00

## FX methodology
- resolve_dashboard_fx_rates at 2026-09-11T00:00:00+00:00
- JPY: 153.6895
- USD: 1.0

## Per-asset quantity reconciliation (attribution − broker)
- AMD: 0.0
- BTCUSD: 0.0
- COIN: 0.0
- ETHUSD: 0.0
- GBPJPY: 0.0
- NVDA: 0.0
- TSLA: 0.0
- XAUUSD: 0.0

