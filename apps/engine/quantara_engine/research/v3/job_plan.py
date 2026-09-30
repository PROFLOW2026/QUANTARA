"""Deterministic V3 backtest job list."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import text

from quantara_engine.persistence.store import TradingStore
from quantara_engine.research.v3.candidates import frozen_v3_candidates
from quantara_engine.research.v3.constants import V3_RESEARCH_START
from quantara_engine.research.v3.data_quality_gate import build_data_quality_matrix
from quantara_engine.research.v3.discovery import _skip_backtest, _test_pairs
from quantara_engine.research.v3.job_registry import job_key_for
from quantara_engine.strategies.registry import get_latest


@dataclass(frozen=True)
class V3Job:
    index: int
    key: str
    candidate_id: str
    family: str
    strategy_slug: str
    asset: str
    timeframe: str


def plan_v3_jobs(store: TradingStore) -> list[V3Job]:
    quality = build_data_quality_matrix(store)
    pairs = _test_pairs(quality)
    candidates = frozen_v3_candidates()
    raw: list[tuple] = []
    for candidate in candidates:
        cls = get_latest(candidate.strategy_slug)
        for symbol, timeframe in pairs:
            if _skip_backtest(candidate, symbol, timeframe):
                continue
            if timeframe not in cls.supported_timeframes():
                continue
            instrument = store.get_instrument_by_symbol(symbol)
            if not instrument:
                continue
            n = store.session.execute(
                text(
                    """
                    SELECT COUNT(*)::int FROM candles
                    WHERE instrument_id = CAST(:iid AS uuid)
                      AND timeframe = :tf
                      AND timestamp >= :start
                    """
                ),
                {"iid": str(instrument.id), "tf": timeframe, "start": V3_RESEARCH_START},
            ).scalar()
            if not n or int(n) < 250:
                continue
            key = job_key_for(candidate.candidate_id, symbol, timeframe)
            raw.append((key, candidate, symbol, timeframe))
    raw.sort(key=lambda x: x[0])
    jobs: list[V3Job] = []
    for i, (key, candidate, symbol, timeframe) in enumerate(raw):
        jobs.append(
            V3Job(
                index=i,
                key=key,
                candidate_id=candidate.candidate_id,
                family=candidate.family,
                strategy_slug=candidate.strategy_slug,
                asset=symbol,
                timeframe=timeframe,
            )
        )
    return jobs
