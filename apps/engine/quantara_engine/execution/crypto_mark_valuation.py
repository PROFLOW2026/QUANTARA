"""Canonical 1m BTC/ETH marks for valuation — reuses fast-protection candle fetch."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import text

from quantara_engine.broker.accounts import LIVE_SIM_10K_ACCOUNT_SLUG
from quantara_engine.broker.execution_service import BrokerExecutionService
from quantara_engine.broker.instruments import get_instrument_spec
from quantara_engine.broker.pnl import unrealized_pnl_usd
from quantara_engine.domain.types import Instrument
from quantara_engine.execution.timing import is_bar_complete
from quantara_engine.market_data.polling import FAST_PROTECTION_TIMEFRAME
from quantara_engine.market_data.symbols import normalize_db_symbol
from quantara_engine.persistence.store import TradingStore
from quantara_engine.portfolio.currency import build_currency_context
from quantara_engine.portfolio.pnl import update_position_unrealized

logger = logging.getLogger(__name__)

CRYPTO_CANONICAL_MARKS_KEY = "crypto_canonical_marks"
FAST_CRYPTO_DB_SYMBOLS = frozenset({"BTCUSD", "ETHUSD"})


def is_fast_protection_crypto(symbol: str) -> bool:
    return normalize_db_symbol(symbol) in FAST_CRYPTO_DB_SYMBOLS


def _as_utc(ts: datetime) -> datetime:
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc)


def load_crypto_canonical_marks(store: TradingStore) -> dict[str, dict[str, str]]:
    raw = store.get_settings_dict().get(CRYPTO_CANONICAL_MARKS_KEY) or {}
    return dict(raw) if isinstance(raw, dict) else {}


def get_crypto_canonical_mark(
    store: TradingStore,
    symbol: str,
) -> tuple[Decimal, datetime] | None:
    """Return stored canonical mark (price, candle timestamp) for a crypto symbol."""
    db_sym = normalize_db_symbol(symbol)
    entry = load_crypto_canonical_marks(store).get(db_sym)
    if not entry:
        return None
    price_raw = entry.get("price")
    at_raw = entry.get("at")
    if price_raw is None or not at_raw:
        return None
    return Decimal(str(price_raw)), _as_utc(datetime.fromisoformat(str(at_raw).replace("Z", "+00:00")))


def crypto_mark_owned_by_1m(store: TradingStore, symbol: str) -> bool:
    """True when open BTC/ETH positions use the 1m canonical mark path."""
    if not is_fast_protection_crypto(symbol):
        return False
    if get_crypto_canonical_mark(store, symbol) is None:
        return False
    return _has_open_crypto_exposure(store, normalize_db_symbol(symbol))


def latest_completed_1m_close(
    candles: list,
    now: datetime,
) -> tuple[Decimal, datetime] | None:
    """Latest completed 1m bar close from prefetched/stored candles."""
    if not candles:
        return None
    best = None
    for candle in candles:
        if is_bar_complete(candle.timestamp, FAST_PROTECTION_TIMEFRAME, now):
            best = candle
    if best is None:
        return None
    return best.close, _as_utc(best.timestamp)


def prune_crypto_canonical_marks(store: TradingStore, active_symbols: set[str]) -> None:
    """Drop stored marks for symbols with no open crypto exposure."""
    stored = load_crypto_canonical_marks(store)
    if not stored:
        return
    pruned = {sym: meta for sym, meta in stored.items() if sym in active_symbols}
    if pruned != stored:
        store.update_settings(CRYPTO_CANONICAL_MARKS_KEY, pruned, flush=False)


def _has_open_crypto_exposure(store: TradingStore, db_symbol: str) -> bool:
    instrument = store.get_instrument_by_symbol(db_symbol)
    if not instrument:
        return False
    research = store.session.execute(
        text(
            """
            SELECT 1 FROM positions
            WHERE instrument_id = :iid AND status = 'open'
            LIMIT 1
            """
        ),
        {"iid": instrument.id},
    ).first()
    if research:
        return True
    live = store.session.execute(
        text(
            """
            SELECT 1 FROM live_sim_positions p
            JOIN broker_accounts a ON a.id = p.broker_account_id
            WHERE p.instrument_id = :iid AND p.status = 'open'
              AND a.slug = :slug
            LIMIT 1
            """
        ),
        {"iid": instrument.id, "slug": LIVE_SIM_10K_ACCOUNT_SLUG},
    ).first()
    return live is not None


def _update_research_position_marks(
    store: TradingStore,
    instrument: Instrument,
    mark: Decimal,
) -> int:
    from quantara_engine.persistence.batch_summary import batch_open_positions_by_portfolio

    _, _, entries = store.list_all_competition_entries()
    if not entries:
        return 0
    portfolio_ids = [e["portfolio"].id for e in entries]
    open_by = batch_open_positions_by_portfolio(store, portfolio_ids)
    currency = build_currency_context(store, [instrument])
    mark_updates: list[tuple[str, Decimal, Decimal]] = []
    portfolios = []

    for entry in entries:
        portfolio = entry["portfolio"]
        positions = open_by.get(portfolio.id, [])
        touched = False
        for pos in positions:
            if pos.instrument_id != instrument.id:
                continue
            upnl = update_position_unrealized(
                pos, mark, instrument, currency.fx_rates
            )
            mark_updates.append((pos.id, mark, upnl))
            touched = True
        if touched and portfolio not in portfolios:
            portfolios.append(portfolio)

    if mark_updates:
        store.update_open_position_marks_batch(mark_updates)
        store.sync_portfolios_financial_state_from_ledger(portfolios, flush=False)
    return len(mark_updates)


def _update_live_sim_position_marks(
    store: TradingStore,
    instrument: Instrument,
    mark: Decimal,
) -> int:
    account = store.session.execute(
        text("SELECT id::text FROM broker_accounts WHERE slug = :slug"),
        {"slug": LIVE_SIM_10K_ACCOUNT_SLUG},
    ).mappings().first()
    if not account:
        return 0

    rows = store.session.execute(
        text(
            """
            SELECT id::text, direction::text, quantity, entry_price
            FROM live_sim_positions
            WHERE broker_account_id = :aid AND instrument_id = :iid AND status = 'open'
            """
        ),
        {"aid": account["id"], "iid": instrument.id},
    ).mappings().all()
    if not rows:
        return 0

    spec = get_instrument_spec(normalize_db_symbol(instrument.symbol))
    fx = {"USD": Decimal("1")}
    updated = 0
    for row in rows:
        qty = Decimal(str(row["quantity"]))
        entry = Decimal(str(row["entry_price"]))
        signed = qty if str(row["direction"]).lower() == "long" else -qty
        upnl = unrealized_pnl_usd(signed, entry, mark, spec, fx)
        store.session.execute(
            text(
                """
                UPDATE live_sim_positions
                SET current_price = :mark, unrealized_pnl = :upnl, updated_at = NOW()
                WHERE id = :id
                """
            ),
            {"id": row["id"], "mark": mark, "upnl": upnl},
        )
        updated += 1
    return updated


def apply_crypto_1m_marks(
    store: TradingStore,
    marks_by_symbol: dict[str, tuple[Decimal, datetime]],
    *,
    flush: bool = True,
) -> dict[str, Any]:
    """
    Apply completed 1m closes as canonical marks (Research + Live Sim + broker).

    Newer candle timestamps win; older marks never overwrite fresher 1m marks.
    """
    stored = load_crypto_canonical_marks(store)
    applied: list[str] = []
    research_rows = 0
    live_sim_rows = 0

    for symbol, (price, at) in marks_by_symbol.items():
        db_sym = normalize_db_symbol(symbol)
        if not is_fast_protection_crypto(db_sym):
            continue
        at = _as_utc(at)
        prev = stored.get(db_sym)
        if prev and prev.get("at"):
            prev_at = _as_utc(
                datetime.fromisoformat(str(prev["at"]).replace("Z", "+00:00"))
            )
            if at <= prev_at:
                continue

        instrument = store.get_instrument_by_symbol(db_sym)
        if not instrument:
            continue

        stored[db_sym] = {"price": str(price), "at": at.isoformat()}
        research_rows += _update_research_position_marks(store, instrument, price)
        live_sim_rows += _update_live_sim_position_marks(store, instrument, price)

        BrokerExecutionService(store).mark_to_market({db_sym: price}, at=at)
        BrokerExecutionService(
            store, account_slug=LIVE_SIM_10K_ACCOUNT_SLUG
        ).mark_to_market({db_sym: price}, at=at)
        applied.append(db_sym)
        logger.debug("Crypto 1m mark %s = %s @ %s", db_sym, price, at.isoformat())

    if stored:
        store.update_settings(CRYPTO_CANONICAL_MARKS_KEY, stored, flush=False)
    if flush:
        store.session.flush()

    return {
        "applied_symbols": applied,
        "research_positions_updated": research_rows,
        "live_sim_positions_updated": live_sim_rows,
    }


def canonical_mark_for_instrument(
    store: TradingStore,
    instrument_id: str,
    symbol: str,
) -> Decimal | None:
    """Dashboard/snapshot helper — canonical 1m mark when active."""
    if not crypto_mark_owned_by_1m(store, symbol):
        return None
    canon = get_crypto_canonical_mark(store, symbol)
    return canon[0] if canon else None
