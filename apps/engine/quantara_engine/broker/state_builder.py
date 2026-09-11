"""Build shared broker account snapshot from open competition positions."""

from __future__ import annotations

from decimal import Decimal

from quantara_engine.broker.account import build_account_snapshot
from quantara_engine.broker.profile import QUANTARA_STANDARD_PAPER
from quantara_engine.broker.types import BrokerAccountSnapshot, BrokerProfile
from quantara_engine.competition.constants import ACTIVE_COMPETITION_EXPERIMENT_ID
from quantara_engine.competition.orb_constants import ORB_COMPETITION_EXPERIMENT_ID
from quantara_engine.domain.types import Direction
from quantara_engine.persistence.store import TradingStore
from quantara_engine.portfolio.currency import resolve_dashboard_fx_rates, quote_currencies_for_instruments


def aggregate_net_positions(store: TradingStore) -> dict[str, tuple[Decimal, Decimal, Decimal]]:
    """
    Aggregate all open competition legs into broker net positions.

    Returns symbol -> (signed_net_qty, weighted_avg_entry, mark_price)
    """
    exp_ids = (str(ACTIVE_COMPETITION_EXPERIMENT_ID), str(ORB_COMPETITION_EXPERIMENT_ID))
    entries = store.list_all_competition_entries()[2]
    sym_by_iid = {i.id: i.symbol for i in store.list_instruments()}

    # symbol -> list of (signed_qty, entry, mark)
    buckets: dict[str, list[tuple[Decimal, Decimal, Decimal]]] = {}

    for entry in entries:
        portfolio_id = entry["portfolio"].id
        state = store.load_portfolio_state(portfolio_id)
        for pos in state.open_positions():
            sym = sym_by_iid.get(pos.instrument_id, "")
            if not sym:
                continue
            sign = Decimal("1") if pos.direction == Direction.LONG else Decimal("-1")
            qty = pos.quantity * sign
            mark = pos.current_price or pos.entry_price
            buckets.setdefault(sym.upper(), []).append((qty, pos.entry_price, mark))

    result: dict[str, tuple[Decimal, Decimal, Decimal]] = {}
    for sym, legs in buckets.items():
        net_qty = sum(l[0] for l in legs)
        if net_qty == 0:
            continue
        # Weighted average entry on net direction
        total_cost = Decimal("0")
        total_abs = Decimal("0")
        mark = legs[-1][2]
        for qty, entry, m in legs:
            mark = m
            total_cost += abs(qty) * entry
            total_abs += abs(qty)
        avg = (total_cost / total_abs) if total_abs else Decimal("0")
        result[sym] = (net_qty, avg, mark)
    return result


def build_competition_broker_account(
    store: TradingStore,
    *,
    profile: BrokerProfile | None = None,
) -> BrokerAccountSnapshot:
    """Canonical paper broker account for the 160-portfolio competition."""
    profile = profile or QUANTARA_STANDARD_PAPER
    positions = aggregate_net_positions(store)

    instruments = [store.get_instrument_by_symbol(s) for s in positions]
    instruments = [i for i in instruments if i]
    fx = resolve_dashboard_fx_rates(store, quote_currencies_for_instruments(instruments))
    fx_map = {k: v for k, v in fx.quote_per_usd.items()}

    # Account-level balance from sum of portfolio balances vs starting cash
    entries = store.list_all_competition_entries()[2]
    total_balance = sum(e["portfolio"].balance for e in entries)
    total_unrealized = sum(e["portfolio"].unrealized_pnl for e in entries)
    total_realized = total_balance - profile.starting_cash

    pos_tuples = {
        sym: (qty, avg, mark) for sym, (qty, avg, mark) in positions.items()
    }

    return build_account_snapshot(
        cash=total_balance,
        balance=total_balance,
        realized_pnl=total_realized,
        positions=pos_tuples,
        fx_rates=fx_map,
        profile=profile,
        unrealized_override=total_unrealized,
    )
