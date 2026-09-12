"""Measured 8-asset / 3-TF steady-state egress on disposable PostgreSQL."""

from __future__ import annotations

import pytest
from sqlalchemy.orm import sessionmaker

from quantara_engine.db import session as db_session
from quantara_engine.market_data.candle_working_set import reset_worker_candle_cache
from quantara_engine.persistence.store import TradingStore
from tests.egress_test_support import (
    CANDLE_LOOKBACK,
    COMPETITION_TIMEFRAMES,
    _next_candle_start,
    make_candle_series,
    simulate_steady_state_strategy_reads,
)


@pytest.fixture
def egress_store(broker_test_database):
    session = db_session.SessionLocal()
    store = TradingStore(session)
    yield store
    session.rollback()
    session.close()


def _seed_incremental_candles(store: TradingStore) -> int:
    """Append one fresh candle per instrument×TF if history exists."""
    from sqlalchemy import text

    instruments = [
        row[0]
        for row in store.session.execute(text("SELECT id::text FROM instruments")).all()
    ]
    appended = 0
    for instrument_id in instruments[:8]:
        for timeframe in COMPETITION_TIMEFRAMES:
            count = store.count_candles(instrument_id, timeframe)
            if count < CANDLE_LOOKBACK:
                series = make_candle_series(
                    CANDLE_LOOKBACK + 5,
                    instrument_id=instrument_id,
                    timeframe=timeframe,
                )
                for candle in series:
                    store.upsert_candle(candle)
                appended += len(series)
                continue
            latest = store.latest_candle_timestamp(instrument_id, timeframe)
            if latest is None:
                continue
            new_candle = make_candle_series(
                1,
                instrument_id=instrument_id,
                timeframe=timeframe,
                start=_next_candle_start(latest, timeframe),
            )[0]
            store.upsert_candle(new_candle)
            appended += 1
    store.session.flush()
    return appended


@pytest.mark.integration
def test_measured_8_asset_steady_state_cycle(egress_store: TradingStore):
    reset_worker_candle_cache()
    _seed_incremental_candles(egress_store)

    _, _, entries = egress_store.list_all_competition_entries()
    assert len(entries) >= 40, "expected seeded competition portfolios"

    instrument_ids = sorted({entry["instance"].instrument_id for entry in entries})
    assert len(instrument_ids) >= 8, f"expected 8 assets, got {len(instrument_ids)}"

    metrics = simulate_steady_state_strategy_reads(egress_store)
    summary = metrics.summary()
    warm = getattr(metrics, "_warm_summary", {})

    assert summary["trade_rows"] == 0
    assert summary["snapshot_rows"] == 0
    assert summary["candle_rows"] > 0
    assert summary["candle_rows"] <= len(instrument_ids) * len(COMPETITION_TIMEFRAMES)
    assert summary["queries"] > 0

    cycles_per_day = 288
    est_day_bytes = summary["payload_bytes"] * cycles_per_day
    est_30d_bytes = est_day_bytes * 30

    assert est_day_bytes < 100 * 1024 * 1024, (
        f"steady-state day payload estimate {est_day_bytes / (1024*1024):.2f} MB exceeds 100 MB target"
    )

    print("\n--- EGRESS STEADY STATE MEASUREMENT ---")
    print(f"warmup_payload_kb={warm.get('payload_kb', 0)}")
    print(f"warmup_candle_rows={warm.get('candle_rows', 0)}")
    print(f"cycle_queries={summary['queries']}")
    print(f"cycle_candle_rows={summary['candle_rows']}")
    print(f"cycle_trade_rows={summary['trade_rows']}")
    print(f"cycle_snapshot_rows={summary['snapshot_rows']}")
    print(f"cycle_position_rows={summary['position_rows']}")
    print(f"cycle_portfolio_rows={summary['portfolio_rows']}")
    print(f"cycle_payload_kb={summary['payload_kb']}")
    print(f"estimated_day_payload_mb={est_day_bytes / (1024*1024):.2f}")
    print(f"estimated_30d_payload_mb={est_30d_bytes / (1024*1024):.2f}")
