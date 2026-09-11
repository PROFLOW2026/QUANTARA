# Clean Paper Reset Plan (NOT EXECUTED)

Owner approval required before running.

## Goal

Start fresh under broker-realistic rules with one shared Paper Broker account.

## Steps (when approved)

1. Apply migration `0006_broker_account.sql` if not applied
2. Mark current competition run as `LEGACY_SIMULATION` in broker_accounts metadata
3. Flatten/close is **NOT** automatic — owner decides legacy handling
4. Reset script will:
   - Set broker account cash/balance/equity = $320,000
   - Clear broker_positions, broker_orders, broker_fills
   - Cancel pending order_intents
   - Close open strategy positions (competition scope only) — **requires explicit flag**
   - Preserve historical trades in read-only legacy tables
5. Re-seed 160 strategy portfolios at $2,000 reference allocation (attribution only)
6. Verify reconciliation = $0.00

## Command

```bash
# NOT IMPLEMENTED AS AUTO-RUN — placeholder for owner-approved reset
python scripts/paper_broker_reset.py --dry-run
python scripts/paper_broker_reset.py --execute  # owner only
```

## Do NOT run until:

- All broker realism tests pass
- Owner reviews replay report
- Runtime stopped (already stopped)
