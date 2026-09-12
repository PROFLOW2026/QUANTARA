#!/usr/bin/env python3
"""Read-only Broker Replay V5 audit generator."""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "apps" / "engine"))

from quantara_engine.broker.replay_v5 import write_replay_v5_audit_markdown
from quantara_engine.db.session import session_scope
from quantara_engine.persistence.store import TradingStore


def main() -> None:
    day_start = datetime(2026, 9, 11, tzinfo=timezone.utc)
    day_end = day_start + timedelta(days=1)
    out = ROOT / "docs" / "audits" / "2026-09-11-BROKER-REPLAY-V5.md"
    with session_scope() as session:
        store = TradingStore(session)
        result = write_replay_v5_audit_markdown(store, str(out), day_start, day_end)
    print(f"Wrote {out}")
    print(f"total_pnl_reconciliation={result.get('total_pnl_reconciliation')}")
    print(f"realized_basis_difference={result.get('realized_basis_difference')}")
    print(f"expected_netting_basis_carry={result.get('expected_netting_basis_carry')}")


if __name__ == "__main__":
    main()
