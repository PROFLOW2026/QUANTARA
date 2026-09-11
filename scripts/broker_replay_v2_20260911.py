#!/usr/bin/env python3
"""Generate 2026-09-11-BROKER-REPLAY-V2.md — full-day lifecycle simulation."""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "engine"))

from quantara_engine.broker.replay_v2 import replay_full_day  # noqa: E402
from quantara_engine.db.session import session_scope  # noqa: E402
from quantara_engine.persistence.store import TradingStore  # noqa: E402

DAY_START = datetime(2026, 9, 11, 0, 0, 0, tzinfo=timezone.utc)
DAY_END = datetime(2026, 9, 12, 0, 0, 0, tzinfo=timezone.utc)


def main() -> None:
    with session_scope() as session:
        result = replay_full_day(TradingStore(session), DAY_START, DAY_END)

    out = ROOT / "docs" / "audits" / "2026-09-11-BROKER-REPLAY-V2.md"
    lines = [
        "# 2026-09-11 Broker Replay V2 (full lifecycle)",
        "",
        "Simulates entries **and** exits sequentially through QUANTARA_STANDARD_PAPER.",
        "Read-only — historical DB unchanged.",
        "",
        f"- Original entries: **{result['entries_total']}**",
        f"- Accepted: **{result['entries_accepted']}**",
        f"- Rejected: **{result['entries_rejected']}**",
        f"- Exits replayed: **{result['exits_replayed']}**",
        "",
        "## End state",
        "",
        f"- Ending balance: **${result['ending_balance']:,.2f}**",
        f"- Ending equity: **${result['ending_equity']:,.2f}**",
        f"- Open broker positions: **{result['ending_positions']}**",
        f"- Realized P&L: **${result['realized_pnl']:,.2f}**",
        f"- Max gross exposure: **${result['max_gross_exposure']:,.2f}**",
        f"- Max initial margin used: **${result['max_initial_margin_used']:,.2f}**",
        "",
        "## Entry rejection reasons",
        "",
    ]
    for reason, count in sorted(result.get("by_reason", {}).items(), key=lambda x: -x[1]):
        lines.append(f"- {reason}: {count}")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
