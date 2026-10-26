"""Dynamic Live Sim universe + forward prioritization."""

from quantara_engine.live_sim.dynamic_universe import PINNED_SYMBOLS
from quantara_workers.jobs.run_strategy import _prioritize_live_sim_forwards


class _Inst:
    def __init__(self, slug: str, tf: str):
        self.strategy_slug = slug
        self.timeframe = tf


class _Entry:
    def __init__(self, slug: str, tf: str):
        self.instance = _Inst(slug, tf)


def test_pinned_symbols_include_gold_and_fx():
    assert "XAUUSD" in PINNED_SYMBOLS
    assert "GBPJPY" in PINNED_SYMBOLS


def test_prioritize_live_sim_forwards_dedupes_timeframes_and_orders_a_last():
    forwards = [
        ({"instance": _Entry("gold-trend-pullback", "5m").instance}, object()),
        ({"instance": _Entry("gold-trend-pullback", "15m").instance}, object()),
        ({"instance": _Entry("opening-range-breakout", "5m").instance}, object()),
        ({"instance": _Entry("mean-reversion", "15m").instance}, object()),
    ]
    out = _prioritize_live_sim_forwards(forwards, symbol="NVDA")
    slugs = [e["instance"].strategy_slug for e, _ in out]
    assert slugs.index("opening-range-breakout") < slugs.index("gold-trend-pullback")
    assert slugs.count("gold-trend-pullback") == 1

