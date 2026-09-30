"""V3 research constants — assets, baselines, contamination."""

from __future__ import annotations

from datetime import datetime, timezone

V3_RESEARCH_START = datetime(2026, 3, 17, 0, 0, 0, tzinfo=timezone.utc)

V3_RESEARCH_ASSETS: tuple[str, ...] = (
    "BTCUSD",
    "ETHUSD",
    "XAUUSD",
    "GBPJPY",
    "NVDA",
    "TSLA",
    "AMD",
    "COIN",
)

BASELINE_STRATEGY_SLUGS: dict[str, str] = {
    "Robot A": "gold-trend-pullback",
    "Robot B": "opening-range-breakout",
    "Robot C": "mean-reversion",
    "Robot D": "volatility-squeeze",
    "Robot E": "momentum-continuation",
}

EQUITY_1M_CONTAMINATION_SETTINGS_KEY = "equity_1m_contaminated_windows"

V3_INITIAL_LIVE_RISK_PCT = 0.25
