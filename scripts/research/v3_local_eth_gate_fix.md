# ETH gate false negative (v3-research proposal)

**Finding:** No open Live Sim `ETHUSD` position → `_tmp_final_gate` `eth_ok = false`.

**Proposed rule:**

```python
open_eth = session.execute(
    text(
        """
        SELECT COUNT(*) FROM live_sim_positions p
        JOIN instruments i ON i.id = p.instrument_id
        WHERE p.status = 'open' AND i.symbol = 'ETHUSD'
        """
    )
).scalar()
if not open_eth:
    eth_ok = True  # nothing to protect
else:
    # existing kraken row qty / SL / TP / mark checks
    ...
```

Apply in release gate helper (not `_tmp_*` only) when merging to main with V3 release.
