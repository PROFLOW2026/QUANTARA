"""Optional DB read counters for egress regression tests."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class EgressMetrics:
    queries: int = 0
    candle_rows: int = 0
    trade_rows: int = 0
    snapshot_rows: int = 0
    query_labels: dict[str, int] = field(default_factory=dict)

    def reset(self) -> None:
        self.queries = 0
        self.candle_rows = 0
        self.trade_rows = 0
        self.snapshot_rows = 0
        self.query_labels.clear()

    def note_query(self, label: str, *, candle_rows: int = 0, trade_rows: int = 0, snapshot_rows: int = 0) -> None:
        self.queries += 1
        self.candle_rows += candle_rows
        self.trade_rows += trade_rows
        self.snapshot_rows += snapshot_rows
        self.query_labels[label] = self.query_labels.get(label, 0) + 1
