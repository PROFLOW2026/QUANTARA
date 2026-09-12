"""Optional DB read counters and payload estimates for egress regression tests."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from quantara_engine.domain.types import Candle


def candle_row_payload_bytes(candle: Candle) -> int:
    """Serialized OHLCV payload estimate (timestamp + 5 numeric columns)."""
    payload = {
        "ts": candle.timestamp.isoformat(),
        "o": str(candle.open),
        "h": str(candle.high),
        "l": str(candle.low),
        "c": str(candle.close),
        "v": str(candle.volume),
    }
    return len(json.dumps(payload, separators=(",", ":")).encode("utf-8"))


def estimate_rows_payload_bytes(rows: list[Any], *, kind: str) -> int:
    if not rows:
        return 0
    if kind == "candle" and isinstance(rows[0], Candle):
        return sum(candle_row_payload_bytes(c) for c in rows)
    if kind == "trade":
        total = 0
        for row in rows:
            total += len(
                json.dumps(
                    {
                        "id": getattr(row, "id", None),
                        "pnl": str(getattr(row, "realized_pnl", Decimal("0"))),
                        "qty": str(getattr(row, "quantity", Decimal("0"))),
                    },
                    separators=(",", ":"),
                ).encode("utf-8")
            )
        return total
    if kind == "snapshot":
        total = 0
        for row in rows:
            total += len(
                json.dumps(
                    {
                        "balance": str(getattr(row, "balance", Decimal("0"))),
                        "equity": str(getattr(row, "equity", Decimal("0"))),
                    },
                    separators=(",", ":"),
                ).encode("utf-8")
            )
        return total
    return sum(len(str(row).encode("utf-8")) for row in rows)


@dataclass
class EgressMetrics:
    queries: int = 0
    candle_rows: int = 0
    trade_rows: int = 0
    snapshot_rows: int = 0
    decision_rows: int = 0
    position_rows: int = 0
    portfolio_rows: int = 0
    payload_bytes: int = 0
    query_labels: dict[str, int] = field(default_factory=dict)

    def reset(self) -> None:
        self.queries = 0
        self.candle_rows = 0
        self.trade_rows = 0
        self.snapshot_rows = 0
        self.decision_rows = 0
        self.position_rows = 0
        self.portfolio_rows = 0
        self.payload_bytes = 0
        self.query_labels.clear()

    def note_query(
        self,
        label: str,
        *,
        candle_rows: int = 0,
        trade_rows: int = 0,
        snapshot_rows: int = 0,
        decision_rows: int = 0,
        position_rows: int = 0,
        portfolio_rows: int = 0,
        payload_bytes: int = 0,
        candle_payload_source: list[Candle] | None = None,
    ) -> None:
        self.queries += 1
        self.candle_rows += candle_rows
        self.trade_rows += trade_rows
        self.snapshot_rows += snapshot_rows
        self.decision_rows += decision_rows
        self.position_rows += position_rows
        self.portfolio_rows += portfolio_rows
        if candle_payload_source is not None:
            payload_bytes += estimate_rows_payload_bytes(candle_payload_source, kind="candle")
        self.payload_bytes += payload_bytes
        self.query_labels[label] = self.query_labels.get(label, 0) + 1

    def summary(self) -> dict[str, int | float]:
        return {
            "queries": self.queries,
            "candle_rows": self.candle_rows,
            "trade_rows": self.trade_rows,
            "snapshot_rows": self.snapshot_rows,
            "decision_rows": self.decision_rows,
            "position_rows": self.position_rows,
            "portfolio_rows": self.portfolio_rows,
            "payload_bytes": self.payload_bytes,
            "payload_kb": round(self.payload_bytes / 1024, 2),
        }
