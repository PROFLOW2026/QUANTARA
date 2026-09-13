"""Trading Week Learning Layer — observational shadow analytics only.

Never writes to Research / Live Sim financial truth tables as trades,
orders, fills, or equity. Shadow PnL lives only in learning_* tables.
"""

from quantara_engine.learning.activation import ensure_trading_week_baseline, get_active_baseline

__all__ = ["ensure_trading_week_baseline", "get_active_baseline"]
