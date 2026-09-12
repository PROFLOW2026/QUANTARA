"""One-shot lineage repair for positions missing paper_run_id (provable intent chain only)."""

from __future__ import annotations

import json
import sys

sys.path.insert(0, "apps/engine")

from quantara_engine.competition.paper_run import repair_positions_paper_run_from_lineage
from quantara_engine.db.session import session_scope
from quantara_engine.persistence.store import TradingStore


def main() -> None:
    with session_scope() as session:
        store = TradingStore(session)
        report = repair_positions_paper_run_from_lineage(store)
        session.commit()
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
