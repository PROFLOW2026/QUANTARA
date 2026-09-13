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
FAST_CANONICAL_MARKS_KEY = "fast_canonical_marks"
FAST_CRYPTO_DB_SYMBOLS = frozenset({"BTCUSD", "ETHUSD"})
FAST_EQUITY_DB_SYMBOLS = frozenset({"NVDA", "TSLA", "AMD", "COIN"})
FAST_FX_DB_SYMBOLS = frozenset({"XAUUSD", "GBPJPY"})
FAST_NON_CRYPTO_DB_SYMBOLS = FAST_EQUITY_DB_SYMBOLS | FAST_FX_DB_SYMBOLS
FAST_ALL_1M_SYMBOLS = FAST_CRYPTO_DB_SYMBOLS | FAST_NON_CRYPTO_DB_SYMBOLS


def is_fast_protection_crypto(symbol: str) -> bool:
    return normalize_db_symbol(symbol) in FAST_CRYPTO_DB_SYMBOLS


def is_fast_protection_equity(symbol: str) -> bool:
    return normalize_db_symbol(symbol) in FAST_EQUITY_DB_SYMBOLS


def is_fast_protection_fx(symbol: str) -> bool:
    return normalize_db_symbol(symbol) in FAST_FX_DB_SYMBOLS


def is_fast_1m_protected_symbol(symbol: str) -> bool:
    return normalize_db_symbol(symbol) in FAST_ALL_1M_SYMBOLS


def should_skip_5m_position_management(
    store: TradingStore,
    symbol: str,
    *,
    now: datetime | None = None,
) -> bool:
    """
    Skip 5m PM when the 1m fast path is active for this symbol.

    FX falls back to 5m when the Twelve Data credit guard blocks fast fetches.
    Equities fall back outside US RTH. Crypto always uses 1m when listed.
    """
    from quantara_engine.execution.fx_fast_credit_guard import can_run_fast_fx_fetch
    from quantara_engine.market_data.sessions import is_forex_session, is_us_equity_rth

    db_sym = normalize_db_symbol(symbol)
    if db_sym in FAST_CRYPTO_DB_SYMBOLS:
        return True
    if db_sym in FAST_EQUITY_DB_SYMBOLS:
        now = now or datetime.now(timezone.utc)
        return is_us_equity_rth(now)
    if db_sym in FAST_FX_DB_SYMBOLS:
        now = now or datetime.now(timezone.utc)
        if not is_forex_session(now):
            return False
        return can_run_fast_fx_fetch(store)
    return False


def _as_utc(ts: datetime) -> datetime:
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc)


def load_fast_canonical_marks(store: TradingStore) -> dict[str, dict[str, str]]:
    settings = store.get_settings_dict()
    raw = settings.get(FAST_CANONICAL_MARKS_KEY) or settings.get(CRYPTO_CANONICAL_MARKS_KEY) or {}
    return dict(raw) if isinstance(raw, dict) else {}


def load_crypto_canonical_marks(store: TradingStore) -> dict[str, dict[str, str]]:
    return load_fast_canonical_marks(store)


def get_fast_canonical_mark(
    store: TradingStore,
    symbol: str,
) -> tuple[Decimal, datetime] | None:
    db_sym = normalize_db_symbol(symbol)
    entry = load_fast_canonical_marks(store).get(db_sym)
    if not entry:
        return None
    price_raw = entry.get("price")
    at_raw = entry.get("at")
    if price_raw is None or not at_raw:
        return None
    return Decimal(str(price_raw)), _as_utc(datetime.fromisoformat(str(at_raw).replace("Z", "+00:00")))


def get_crypto_canonical_mark(
    store: TradingStore,
    symbol: str,
) -> tuple[Decimal, datetime] | None:
    """Return stored canonical mark (price, candle timestamp) for a fast-monitored symbol."""
    db_sym = normalize_db_symbol(symbol)
    entry = load_fast_canonical_marks(store).get(db_sym)
    if not entry:
        return None
    price_raw = entry.get("price")
    at_raw = entry.get("at")
    if price_raw is None or not at_raw:
        return None
    return Decimal(str(price_raw)), _as_utc(datetime.fromisoformat(str(at_raw).replace("Z", "+00:00")))


def _has_open_exposure(store: TradingStore, db_symbol: str) -> bool:
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


def crypto_mark_owned_by_1m(store: TradingStore, symbol: str) -> bool:
    """True when open BTC/ETH positions use the 1m canonical mark path."""
    if not is_fast_protection_crypto(symbol):
        return False
    if get_fast_canonical_mark(store, symbol) is None:
        return False
    return _has_open_exposure(store, normalize_db_symbol(symbol))


def fast_mark_owned_by_1m(store: TradingStore, symbol: str) -> bool:
    """True when a canonical 1m mark should block stale 5m refresh."""
    if not is_fast_1m_protected_symbol(symbol):
        return False
    return get_fast_canonical_mark(store, symbol) is not None


def display_price_from_canonical_mark(
    store: TradingStore,
    symbol: str,
) -> tuple[Decimal, datetime] | None:
    """UI/API display price from stored canonical 1m mark when available."""
    if not is_fast_1m_protected_symbol(symbol):
        return None
    return get_fast_canonical_mark(store, symbol)


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


def prune_fast_canonical_marks(store: TradingStore, active_symbols: set[str]) -> None:
    """Drop stored marks for symbols with no open fast-monitored exposure."""
    stored = load_fast_canonical_marks(store)
    if not stored:
        return
    pruned = {sym: meta for sym, meta in stored.items() if sym in active_symbols}
    if pruned != stored:
        store.update_settings(FAST_CANONICAL_MARKS_KEY, pruned, flush=False)
        store.update_settings(CRYPTO_CANONICAL_MARKS_KEY, pruned, flush=False)


def prune_crypto_canonical_marks(store: TradingStore, active_symbols: set[str]) -> None:
    prune_fast_canonical_marks(store, active_symbols)


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


def apply_fast_1m_marks(
    store: TradingStore,
    marks_by_symbol: dict[str, tuple[Decimal, datetime]],
    *,
    flush: bool = True,
) -> dict[str, Any]:
    """
    Apply completed 1m closes as canonical marks (Research + Live Sim + broker).

    Newer candle timestamps win; older marks never overwrite fresher 1m marks.
    """
    stored = load_fast_canonical_marks(store)
    applied: list[str] = []
    research_rows = 0
    live_sim_rows = 0

    for symbol, (price, at) in marks_by_symbol.items():
        db_sym = normalize_db_symbol(symbol)
        if not is_fast_1m_protected_symbol(db_sym):
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
        sym_research = _update_research_position_marks(store, instrument, price)
        sym_live = _update_live_sim_position_marks(store, instrument, price)
        research_rows += sym_research
        live_sim_rows += sym_live

        if sym_research or sym_live:
            BrokerExecutionService(store).mark_to_market({db_sym: price}, at=at)
            BrokerExecutionService(
                store, account_slug=LIVE_SIM_10K_ACCOUNT_SLUG
            ).mark_to_market({db_sym: price}, at=at)
        applied.append(db_sym)
        logger.debug("Fast 1m mark %s = %s @ %s", db_sym, price, at.isoformat())

    if stored:
        store.update_settings(FAST_CANONICAL_MARKS_KEY, stored, flush=False)
        store.update_settings(CRYPTO_CANONICAL_MARKS_KEY, stored, flush=False)
    if flush:
        store.session.flush()

    return {
        "applied_symbols": applied,
        "research_positions_updated": research_rows,
        "live_sim_positions_updated": live_sim_rows,
    }


def apply_crypto_1m_marks(
    store: TradingStore,
    marks_by_symbol: dict[str, tuple[Decimal, datetime]],
    *,
    flush: bool = True,
) -> dict[str, Any]:
    return apply_fast_1m_marks(store, marks_by_symbol, flush=flush)


def canonical_mark_for_instrument(
    store: TradingStore,
    instrument_id: str,
    symbol: str,
) -> Decimal | None:
    """Dashboard/snapshot helper — canonical 1m mark when stored."""
    canon = display_price_from_canonical_mark(store, symbol)
    return canon[0] if canon else None
