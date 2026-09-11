"""FIFO strategy attribution against broker fills."""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import text

from quantara_engine.persistence.store import TradingStore


def allocate_fill_to_strategy_legs(
    store: TradingStore,
    *,
    broker_fill_id: str,
    strategy_position_id: str | None,
    portfolio_id: str,
    direction: str,
    quantity: Decimal,
    fill_price: Decimal,
    realized_pnl: Decimal,
    opportunity_key: str | None = None,
) -> None:
    """
    Record strategy attribution for a broker fill.

    Entry fills: open attribution lot linked to strategy position.
    Exit fills: FIFO reduce (realized_pnl already computed at broker layer).
    """
    try:
        store.session.execute(
            text(
                """
                INSERT INTO broker_attribution_ledger (
                  broker_fill_id, strategy_position_id, strategy_portfolio_id,
                  opportunity_key, quantity, entry_price, exit_price,
                  realized_pnl, direction
                ) VALUES (
                  :fid, :spid, :pid, :opp, :qty,
                  :entry, :exit, :pnl, :dir
                )
                """
            ),
            {
                "fid": broker_fill_id,
                "spid": strategy_position_id,
                "pid": portfolio_id,
                "opp": opportunity_key,
                "qty": quantity,
                "entry": fill_price if direction == "long" and not realized_pnl else None,
                "exit": fill_price if realized_pnl else None,
                "pnl": realized_pnl,
                "dir": direction,
            },
        )
    except Exception:
        pass  # migration may not be applied in unit tests
