#!/usr/bin/env python3
"""Read-only replay of 2026-09-11 entries through QUANTARA_STANDARD_PAPER."""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "engine"))

from quantara_engine.broker.replay import replay_entries_for_day  # noqa: E402
from quantara_engine.db.session import session_scope  # noqa: E402
from quantara_engine.persistence.store import TradingStore  # noqa: E402

DAY_START = datetime(2026, 9, 11, 0, 0, 0, tzinfo=timezone.utc)
DAY_END = datetime(2026, 9, 12, 0, 0, 0, tzinfo=timezone.utc)


def main() -> None:
    with session_scope() as session:
        store = TradingStore(session)
        result = replay_entries_for_day(store, DAY_START, DAY_END)
    out_path = ROOT / "docs" / "audits" / "2026-09-11-BROKER-REPLAY.md"
    lines = [
        "# 2026-09-11 Broker Replay (QUANTARA_STANDARD_PAPER)",
        "",
        "Read-only replay. Historical DB unchanged.",
        "",
        f"- Total entries replayed: **{result['total']}**",
        f"- Would accept: **{result['accepted']}**",
        f"- Would reject: **{result['rejected']}**",
        "",
        "## Rejection reasons",
        "",
    ]
    for reason, count in sorted(result.get("by_reason", {}).items(), key=lambda x: -x[1]):
        lines.append(f"- {reason}: {count}")
    lines.extend(["", "## Sample rejections", ""])
    for sample in result.get("sample_rejections", [])[:15]:
        lines.append(
            f"- {sample['symbol']} qty={sample['qty']} @ {sample['opened_at']}: "
            f"{sample['reason']} — {sample.get('detail', '')}"
        )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps(result, indent=2))
    print(f"\nWrote {out_path}")


if __name__ == "__main__":
    main()
