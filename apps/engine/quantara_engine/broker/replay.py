"""Read-only replay of historical entry requests through broker model."""

from __future__ import annotations

from collections import Counter
from datetime import datetime
from decimal import Decimal

from quantara_engine.broker.account import build_account_snapshot
from quantara_engine.broker.netting import apply_fill_to_net_position
from quantara_engine.broker.pre_trade import evaluate_broker_order
from quantara_engine.broker.profile import QUANTARA_STANDARD_PAPER
from quantara_engine.broker.types import BrokerOrderRequest, PositionMode
from quantara_engine.persistence.store import TradingStore

# Static FX for replay when DB FX lookup is unavailable.
_REPLAY_FX = {"USD": Decimal("1"), "JPY": Decimal("150")}


def _positions_dict(
    positions: dict[str, tuple[Decimal, Decimal, Decimal]],
) -> dict[str, tuple[Decimal, Decimal, Decimal]]:
    """symbol -> (signed_net_qty, avg_price, mark_price)"""
    return {k: v for k, v in positions.items() if v[0] != 0}


def _build_replay_account(
    positions: dict[str, tuple[Decimal, Decimal, Decimal]],
    fx_map: dict[str, Decimal],
) -> "BrokerAccountSnapshot":
    profile = QUANTARA_STANDARD_PAPER
    return build_account_snapshot(
        cash=profile.starting_cash,
        balance=profile.starting_cash,
        realized_pnl=Decimal("0"),
        positions=_positions_dict(positions),
        fx_rates=fx_map,
        profile=profile,
    )


def replay_entries_for_day(
    store: TradingStore,
    day_start: datetime,
    day_end: datetime,
) -> dict:
    """
    Replay entry intents opened in [day_start, day_end) through broker pre-trade.

    Does NOT modify DB. Uses sequential account state starting from empty
    positions and $320k starting cash (QUANTARA_STANDARD_PAPER).
    """
    from sqlalchemy import text

    rows = store.session.execute(
        text(
            """
            SELECT p.id, p.portfolio_id, p.quantity, p.direction, p.entry_price,
                   p.opened_at, i.symbol, i.asset_class::text
            FROM positions p
            JOIN instruments i ON i.id = p.instrument_id
            WHERE p.backtest_run_id IS NULL
              AND p.opened_at >= :d0 AND p.opened_at < :d1
            ORDER BY p.opened_at, p.portfolio_id, p.id
            """
        ),
        {"d0": day_start, "d1": day_end},
    ).mappings().all()

    accepted = 0
    rejected = 0
    reasons: Counter = Counter()
    details: list[dict] = []

    fx_map = dict(_REPLAY_FX)
    try:
        from quantara_engine.portfolio.currency import (
            quote_currencies_for_instruments,
            resolve_dashboard_fx_rates,
        )

        symbols = {str(r["symbol"]) for r in rows}
        instruments = [store.get_instrument_by_symbol(s) for s in symbols]
        instruments = [i for i in instruments if i]
        if instruments:
            fx = resolve_dashboard_fx_rates(store, quote_currencies_for_instruments(instruments))
            fx_map = {k: v for k, v in fx.quote_per_usd.items()}
    except Exception:
        pass

    positions: dict[str, tuple[Decimal, Decimal, Decimal]] = {}
    profile = QUANTARA_STANDARD_PAPER
    mode = profile.position_mode

    for row in rows:
        symbol = str(row["symbol"]).upper()
        direction = "long" if str(row["direction"]).lower() == "long" else "short"
        qty = Decimal(str(row["quantity"]))
        mark = Decimal(str(row["entry_price"]))
        asset_class = str(row["asset_class"])

        account = _build_replay_account(positions, fx_map)
        request = BrokerOrderRequest(
            symbol=symbol,
            asset_class=asset_class,
            direction=direction,
            quantity=qty,
            mark_price=mark,
            signal_timestamp=row["opened_at"],
        )
        decision = evaluate_broker_order(account, profile, request, fx_map)

        if decision.accepted:
            accepted += 1
            cur_qty, cur_avg, _ = positions.get(symbol, (Decimal("0"), Decimal("0"), mark))
            new_qty, new_avg = apply_fill_to_net_position(
                cur_qty,
                cur_avg,
                decision.accepted_quantity,
                mark,
                direction,
                mode=mode,
            )
            if new_qty == 0:
                positions.pop(symbol, None)
            else:
                positions[symbol] = (new_qty, new_avg, mark)
        else:
            rejected += 1
            reason = decision.rejection_reason.value if decision.rejection_reason else "unknown"
            reasons[reason] += 1

        details.append(
            {
                "symbol": symbol,
                "opened_at": str(row["opened_at"]),
                "qty": float(qty),
                "accepted": decision.accepted,
                "reason": decision.rejection_reason.value if decision.rejection_reason else None,
                "detail": decision.rejection_detail,
            }
        )

    return {
        "total": len(rows),
        "accepted": accepted,
        "rejected": rejected,
        "by_reason": dict(reasons),
        "sample_rejections": [d for d in details if not d["accepted"]][:20],
        "mode": "sequential",
    }
