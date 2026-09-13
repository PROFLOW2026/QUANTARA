# QUANTARA Multi-Broker Foundation

## Overview

Prepares QUANTARA for future **IBKR + Kraken Derivatives** under one owner portfolio (~$10K target).
**No real broker connectivity** — `REAL_BROKER_SUBMISSION_ENABLED` remains `FALSE`.

## Layers

```
Owner Trading Portfolio (target_capital)
  ├── portfolio_broker_accounts (allocated_capital per broker)
  ├── Owner-global risk engine
  └── Aggregated equity / PnL / exposure

Broker Account (vendor + environment + connection_state)
  ├── Broker-specific cash / margin
  ├── Reconciliation (per account)
  └── SimulatedBrokerAdapter (today)

Routing: canonical symbol + direction → execution_product → broker_vendor → account
```

## Research vs Live Sim

| Mode | Behavior |
|------|----------|
| **Research** | Unchanged — single `quantara_paper_competition` simulated broker |
| **Live Sim default** | Legacy single `live-sim-10k` account linked to owner portfolio |
| **Live Sim multi-broker** | Disabled until owner configures IBKR + Kraken allocations summing to $10K |

## Fee Profiles

All seeded fees are **SIMULATION ASSUMPTIONS** — not guaranteed broker tariffs.
See `broker_fee_profiles` table. Supports `max(calculated, minimum_per_order)`.

## Migration

`packages/db/migrations/0015_multi_broker_foundation.sql`

DB enforces:
- `allocated_capital >= 0`, `allocation_pct` in 0–100
- `SUM(enabled allocated_capital) <= target_capital` (trigger + row lock)
- `target_capital` cannot drop below enabled allocation sum
- New broker accounts default `connection_state = DISCONNECTED`
- Known sim brokers (`quantara_paper_competition`, `live-sim-10k`) explicitly `CONNECTED`

Virtual IBKR/Kraken accounts are created by `OwnerPortfolioService.configure_multi_broker_allocations()`
when owner first opens allocation settings — not seeded in 0015.

## Activation

1. Owner sets IBKR + Kraken allocations via `/live-sim/allocation-settings`
2. Sum must equal `target_capital` ($10,000)
3. Explicit `activate: true` required — no auto 50/50 split
