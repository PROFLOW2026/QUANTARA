"""Robot A tradable universe — all owner-approved active assets."""

from __future__ import annotations

from quantara_engine.market_data.active_universe import list_active_db_symbols


def list_robot_a_tradable_db_symbols() -> tuple[str, ...]:
    return list_active_db_symbols()


def is_robot_a_tradable(db_symbol: str) -> bool:
    return db_symbol.upper() in list_active_db_symbols()
