"""Dataset fingerprint for reproducibility."""

from __future__ import annotations

import hashlib

from quantara_engine.domain.types import Candle


def compute_dataset_fingerprint(candles: list[Candle]) -> str:
    lines = []
    for c in sorted(candles, key=lambda x: x.timestamp):
        vol = c.volume if c.volume is not None else ""
        lines.append(
            f"{c.timestamp.isoformat()}|{c.open}|{c.high}|{c.low}|{c.close}|{vol}"
        )
    content = "\n".join(lines)
    return hashlib.sha256(content.encode()).hexdigest()
