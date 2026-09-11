"""Asset-allocated competition equity — denominator for global asset risk."""

from __future__ import annotations

from decimal import Decimal

from quantara_engine.competition.constants import (
    ACTIVE_COMPETITION_PORTFOLIOS,
    COMPETITION_INITIAL_CAPITAL,
)
from quantara_engine.competition.orb_constants import (
    ORB_COMPETITION_INITIAL_CAPITAL,
    ORB_COMPETITION_PORTFOLIOS,
)


def portfolio_ids_for_symbol(db_symbol: str) -> list[str]:
    sym = db_symbol.upper()
    ids = [p.portfolio_id for p in ACTIVE_COMPETITION_PORTFOLIOS if p.symbol.upper() == sym]
    ids.extend(p.portfolio_id for p in ORB_COMPETITION_PORTFOLIOS if p.symbol.upper() == sym)
    return ids


def nominal_asset_allocated_equity(db_symbol: str) -> Decimal:
    """Fallback when DB equity unavailable."""
    sym = db_symbol.upper()
    n_a = sum(1 for p in ACTIVE_COMPETITION_PORTFOLIOS if p.symbol.upper() == sym)
    n_b = sum(1 for p in ORB_COMPETITION_PORTFOLIOS if p.symbol.upper() == sym)
    return Decimal(str(n_a)) * COMPETITION_INITIAL_CAPITAL + Decimal(str(n_b)) * ORB_COMPETITION_INITIAL_CAPITAL


def asset_portfolio_counts() -> dict[str, dict]:
    from quantara_engine.market_data.active_universe import ACTIVE_DB_SYMBOLS

    out: dict[str, dict] = {}
    for sym in ACTIVE_DB_SYMBOLS:
        ids = portfolio_ids_for_symbol(sym)
        out[sym] = {
            "portfolio_count": len(ids),
            "nominal_allocated_equity": float(nominal_asset_allocated_equity(sym)),
        }
    return out
