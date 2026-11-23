"""SQLAlchemy `text()` repository for the QUANTARA Options Lab (`opt_*` tables).

Transaction model
-----------------
The repository never opens its own session. Every method runs inside the caller's
transaction; pass ``autocommit=True`` (or call ``session.commit()``) to persist.

Accounting model (single source of truth, enforced here)
--------------------------------------------------------
* A credit received at open:
    cash_balance             += net credit - fees           (cash is real immediately)
    premium_collected_gross  += net credit                  (credit strategies only)
    open_liability_usd       = cost-to-close of short legs  (so equity is unchanged)
  premium_realized_net / realized_pnl are NOT touched at open.
* premium_realized_net / realized_pnl change ONLY inside ``close_strategy_record``
  (status closed / expired / assigned / rolled). Realized P&L is net of ALL fees
  (entry + exit). ``premium_realized_net`` accumulates realized P&L of credit
  strategies only; ``realized_pnl`` accumulates every strategy.
* Strategy ``unrealized_pnl`` = sum(leg mark P&L) - fees paid so far, so that
      equity = starting_capital + realized_pnl + unrealized_pnl
  holds exactly (stock gains are booked through ``realized_pnl_delta``).
* Account equity = cash_balance + long_options_market_value
                   - short_options_liability + stock_holdings_value.
* Stock ``avg_cost_basis`` must be the cash paid per share (e.g. the put strike on
  assignment) because option premium has already been realized by the strategy.
* ``underlying_stock`` legs are not persisted as option legs; stock inventory lives
  in ``opt_stock_lots``.
* win_rate_pct = winners / closed strategies * 100 (realized_pnl > 0).
  profit_factor = gross profit / gross loss over closed strategies; ``None`` when
  there are no closed strategies or no losing strategy (undefined, never infinite).
"""

from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Iterable, Mapping, Optional, Sequence

from sqlalchemy import text
from sqlalchemy.orm import Session

from quantara_options.api_schemas import (
    OptionsEngineStatusView,
    OptionsExpirationItem,
    OptionsJournalEventView,
    OptionsLegView,
    OptionsStrategyComparisonRow,
    OptionsStrategyGroupView,
)
from quantara_options.contracts import (
    ALL_9_STRATEGY_LABELS_HE,
    AccountRuleProfile,
    AIAction,
    AIDecisionRecord,
    DataFeedTier,
    DecisionMode,
    ExerciseStyle,
    Greeks,
    LegPlan,
    LegRole,
    OptionAccountSummary,
    OptionContractSpec,
    OptionQuoteSnapshot,
    OptionRight,
    OptionSide,
    PayoffPoint,
    StrategyCandidate,
    StrategyType,
    WheelState,
)
from quantara_options.db.seed_lab import LAB_ACCOUNT_SEEDS, LAB_ACCOUNT_SLUGS

CLOSED_STATUSES: tuple[str, ...] = ("closed", "expired", "assigned", "rolled")
_TERMINAL_STATUS_EVENT = {
    "closed": "CLOSE",
    "expired": "EXPIRE",
    "assigned": "ASSIGN",
    "rolled": "ROLL",
}
_ZERO = Decimal("0")
_Q2 = Decimal("0.01")
_Q4 = Decimal("0.0001")

_ENGINE_STATE_COLUMNS = {
    "enabled",
    "running_cycle",
    "mode",
    "feed_tier",
    "is_indicative",
    "is_delayed_trades",
    "market_open",
    "data_freshness_seconds",
    "last_cycle_at",
    "last_error",
    "groq_available",
    "groq_model",
    "groq_calls_today",
    "groq_calls_date",
    "universe",
}
_ENGINE_JSON_COLUMNS = {"universe"}

_LEG_MARK_FIELDS = (
    "current_mark",
    "bid",
    "ask",
    "delta",
    "gamma",
    "theta",
    "vega",
    "implied_volatility",
)


class OptionsAccountNotFound(KeyError):
    """Raised when an account slug does not exist."""


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #
def _d(value: Any, default: Decimal = _ZERO) -> Decimal:
    if value is None:
        return default
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _q2(value: Any) -> Decimal:
    return _d(value).quantize(_Q2, rounding=ROUND_HALF_UP)


def _q4(value: Any) -> Decimal:
    return _d(value).quantize(_Q4, rounding=ROUND_HALF_UP)


def _f(value: Any, default: float = 0.0) -> float:
    if value is None:
        return default
    return float(value)


def _fo(value: Any) -> Optional[float]:
    return None if value is None else float(value)


def _s(value: Any) -> Optional[str]:
    return None if value is None else str(value)


def _enum_value(value: Any) -> Any:
    return getattr(value, "value", value)


def _json_default(obj: Any) -> Any:
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if hasattr(obj, "model_dump"):
        return obj.model_dump(mode="json")
    if hasattr(obj, "value"):
        return obj.value
    raise TypeError(f"Not JSON serializable: {type(obj)!r}")


def _json(obj: Any) -> str:
    return json.dumps(obj, default=_json_default)


def _json_load(value: Any, default: Any) -> Any:
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def _today() -> date:
    try:
        from zoneinfo import ZoneInfo

        return datetime.now(ZoneInfo("America/New_York")).date()
    except Exception:  # pragma: no cover - tzdata missing
        return datetime.now(timezone.utc).date()


def _is_stock_leg(role: Any) -> bool:
    return str(_enum_value(role)) == LegRole.UNDERLYING_STOCK.value


def _sign(side: Any) -> int:
    return 1 if str(_enum_value(side)) == OptionSide.LONG.value else -1


def _leg_cash_flow(side: Any, price: Any, multiplier: int, quantity: int) -> Decimal:
    """Cash flow of OPENING a leg (+credit received / -debit paid) or, inverted, closing."""
    gross = _d(price) * multiplier * quantity
    return gross if _sign(side) < 0 else -gross


def _leg_pnl(side: Any, entry: Any, mark: Any, multiplier: int, quantity: int) -> Decimal:
    diff = _d(mark) - _d(entry)
    return diff * multiplier * quantity * _sign(side)


def _row(mapping: Any) -> dict[str, Any]:
    return dict(mapping)


# --------------------------------------------------------------------------- #
# Repository
# --------------------------------------------------------------------------- #
class OptionsRepository:
    """CRUD + accounting for the Options Lab. See module docstring for semantics."""

    def __init__(self, session: Session, *, autocommit: bool = False) -> None:
        self.session = session
        self.autocommit = autocommit

    # ------------------------------------------------------------------ infra
    def _exec(self, sql: str, params: Optional[Mapping[str, Any]] = None):
        return self.session.execute(text(sql), dict(params or {}))

    def _maybe_commit(self) -> None:
        if self.autocommit:
            self.session.commit()

    # --------------------------------------------------------------- accounts
    def ensure_lab_seeded(self) -> int:
        """Idempotently create lab accounts + engine_state row. Returns #accounts created.

        Existing accounts (and their balances) are never modified.
        """
        created = 0
        for seed in LAB_ACCOUNT_SEEDS:
            cap = _q2(seed.starting_capital)
            res = self._exec(
                """
                INSERT INTO opt_accounts (
                  slug, name, mode, strategy_scope, experiment_group,
                  starting_capital, cash_balance, equity, peak_equity,
                  rule_profile, wheel_states
                ) VALUES (
                  :slug, :name, :mode, :scope, :grp,
                  :cap, :cap, :cap, :cap,
                  CAST(:profile AS jsonb), '{}'::jsonb
                )
                ON CONFLICT (slug) DO NOTHING
                RETURNING id
                """,
                {
                    "slug": seed.slug,
                    "name": seed.name,
                    "mode": seed.mode.value,
                    "scope": seed.strategy_scope,
                    "grp": seed.experiment_group,
                    "cap": cap,
                    "profile": _json(seed.rule_profile_json()),
                },
            )
            if res.first() is not None:
                created += 1
        self._exec("INSERT INTO opt_engine_state (id) VALUES (1) ON CONFLICT (id) DO NOTHING")
        self._maybe_commit()
        return created

    def _account_row(self, slug: str, *, lock: bool = False) -> dict[str, Any]:
        row = (
            self._exec(
                "SELECT * FROM opt_accounts WHERE slug = :slug" + (" FOR UPDATE" if lock else ""),
                {"slug": slug},
            )
            .mappings()
            .first()
        )
        if row is None:
            raise OptionsAccountNotFound(slug)
        return _row(row)

    def get_account(self, slug: str) -> Optional[dict[str, Any]]:
        """Raw account row (Decimal money, parsed JSON) or None."""
        try:
            acc = self._account_row(slug)
        except OptionsAccountNotFound:
            return None
        acc["rule_profile"] = _json_load(acc.get("rule_profile"), {})
        acc["wheel_states"] = _json_load(acc.get("wheel_states"), {})
        return acc

    def list_accounts(self, *, active_only: bool = False) -> list[OptionAccountSummary]:
        rows = (
            self._exec(
                "SELECT * FROM opt_accounts"
                + (" WHERE is_active" if active_only else "")
            )
            .mappings()
            .all()
        )
        stats = self._strategy_stats()
        order = {slug: i for i, slug in enumerate(LAB_ACCOUNT_SLUGS)}
        accounts = [self._build_summary(_row(r), stats.get(r["slug"])) for r in rows]
        accounts.sort(key=lambda a: (order.get(a.slug, len(order)), a.slug))
        return accounts

    def get_account_summary(self, slug: str) -> Optional[OptionAccountSummary]:
        try:
            acc = self._account_row(slug)
        except OptionsAccountNotFound:
            return None
        return self._build_summary(acc, self._strategy_stats(slug).get(slug))

    def _strategy_stats(self, slug: Optional[str] = None) -> dict[str, dict[str, Any]]:
        rows = (
            self._exec(
                """
                SELECT
                  account_slug,
                  COUNT(*) FILTER (WHERE status = 'open') AS open_count,
                  COUNT(*) FILTER (WHERE status = ANY(:closed)) AS closed_count,
                  COUNT(*) FILTER (WHERE status = ANY(:closed) AND realized_pnl > 0) AS wins,
                  COALESCE(SUM(realized_pnl) FILTER (
                    WHERE status = ANY(:closed) AND realized_pnl > 0), 0) AS gross_profit,
                  COALESCE(SUM(-realized_pnl) FILTER (
                    WHERE status = ANY(:closed) AND realized_pnl < 0), 0) AS gross_loss,
                  COALESCE(SUM(open_liability_usd) FILTER (WHERE status = 'open'), 0)
                    AS open_liability,
                  COALESCE(SUM(collateral_locked_usd) FILTER (WHERE status = 'open'), 0)
                    AS collateral_locked
                FROM opt_strategies
                WHERE (CAST(:slug AS text) IS NULL OR account_slug = CAST(:slug AS text))
                GROUP BY account_slug
                """,
                {"closed": list(CLOSED_STATUSES), "slug": slug},
            )
            .mappings()
            .all()
        )
        return {r["account_slug"]: _row(r) for r in rows}

    @staticmethod
    def _win_rate_and_pf(stats: Optional[Mapping[str, Any]]) -> tuple[Optional[float], Optional[float]]:
        if not stats:
            return None, None
        closed = int(stats["closed_count"])
        win_rate = round(int(stats["wins"]) / closed * 100.0, 2) if closed > 0 else None
        gross_loss = _d(stats["gross_loss"])
        profit_factor = (
            round(float(_d(stats["gross_profit"]) / gross_loss), 4)
            if closed > 0 and gross_loss > 0
            else None
        )
        return win_rate, profit_factor

    def _build_summary(
        self, acc: Mapping[str, Any], stats: Optional[Mapping[str, Any]]
    ) -> OptionAccountSummary:
        start = _d(acc["starting_capital"])
        equity = _d(acc["equity"])
        free_cash = max(
            _ZERO, _d(acc["cash_balance"]) - _d(acc["cash_reserved"]) - _d(acc["margin_used"])
        )
        win_rate, profit_factor = self._win_rate_and_pf(stats)
        profile_raw = _json_load(acc.get("rule_profile"), {}) or {}
        try:
            profile = AccountRuleProfile(**profile_raw)
        except Exception:
            profile = AccountRuleProfile()
        wheel_states = _json_load(acc.get("wheel_states"), {}) or {}
        return OptionAccountSummary(
            id=str(acc["id"]),
            slug=acc["slug"],
            name=acc["name"],
            mode=DecisionMode(acc["mode"]),
            strategy_scope=acc.get("strategy_scope"),
            starting_capital=_f(start),
            cash_balance=_f(acc["cash_balance"]),
            cash_reserved=_f(acc["cash_reserved"]),
            free_cash=_f(free_cash),
            margin_used=_f(acc["margin_used"]),
            long_options_market_value=_f(acc["long_options_market_value"]),
            short_options_liability=_f(acc["short_options_liability"]),
            stock_holdings_value=_f(acc["stock_holdings_value"]),
            equity=_f(equity),
            premium_collected_gross=_f(acc["premium_collected_gross"]),
            premium_realized_net=_f(acc["premium_realized_net"]),
            unrealized_pnl=_f(acc["unrealized_pnl"]),
            realized_pnl=_f(acc["realized_pnl"]),
            fees_paid=_f(acc["fees_paid"]),
            max_drawdown_usd=_f(acc["max_drawdown_usd"]),
            max_drawdown_pct=_f(acc["max_drawdown_pct"]),
            return_pct=round(float((equity - start) / start * 100), 4) if start > 0 else 0.0,
            open_strategies_count=int(stats["open_count"]) if stats else 0,
            closed_strategies_count=int(stats["closed_count"]) if stats else 0,
            win_rate_pct=win_rate,
            profit_factor=profit_factor,
            rule_profile=profile,
            is_active=bool(acc["is_active"]),
            experiment_group=acc.get("experiment_group"),
            wheel_states={str(k): str(v) for k, v in wheel_states.items()},
            created_at=acc.get("created_at"),
            updated_at=acc.get("updated_at"),
        )

    def update_account_balances(
        self,
        slug: str,
        *,
        cash_delta: Any = 0,
        premium_collected_gross_delta: Any = 0,
        premium_realized_net_delta: Any = 0,
        realized_pnl_delta: Any = 0,
        fees_delta: Any = 0,
        stock_marks: Optional[Mapping[str, Any]] = None,
        stock_holdings_value: Optional[Any] = None,
        rule_profile: Optional[Mapping[str, Any]] = None,
        is_active: Optional[bool] = None,
    ) -> OptionAccountSummary:
        """Apply cash/P&L deltas, then re-derive every aggregate from open strategies and lots.

        ``stock_marks`` maps symbol -> price for stock valuation (default: cost basis).
        Use ``realized_pnl_delta`` for stock P&L (e.g. shares called away).
        Premium/realized fields are only meant to change from ``close_strategy_record``
        and ``create_strategy_with_legs_and_fill``; direct use is for adjustments.
        """
        self._refresh_account(
            slug,
            cash_delta=cash_delta,
            premium_collected_gross_delta=premium_collected_gross_delta,
            premium_realized_net_delta=premium_realized_net_delta,
            realized_pnl_delta=realized_pnl_delta,
            fees_delta=fees_delta,
            stock_marks=stock_marks,
            stock_holdings_value=stock_holdings_value,
        )
        if rule_profile is not None or is_active is not None:
            self._exec(
                """
                UPDATE opt_accounts SET
                  rule_profile = COALESCE(CAST(:profile AS jsonb), rule_profile),
                  is_active = COALESCE(:active, is_active),
                  updated_at = NOW()
                WHERE slug = :slug
                """,
                {
                    "slug": slug,
                    "profile": _json(dict(rule_profile)) if rule_profile is not None else None,
                    "active": is_active,
                },
            )
        self._maybe_commit()
        summary = self.get_account_summary(slug)
        assert summary is not None
        return summary

    def _refresh_account(
        self,
        slug: str,
        *,
        cash_delta: Any = 0,
        premium_collected_gross_delta: Any = 0,
        premium_realized_net_delta: Any = 0,
        realized_pnl_delta: Any = 0,
        fees_delta: Any = 0,
        stock_marks: Optional[Mapping[str, Any]] = None,
        stock_holdings_value: Optional[Any] = None,
    ) -> dict[str, Any]:
        acc = self._account_row(slug, lock=True)
        agg = (
            self._exec(
                """
                SELECT
                  COALESCE(SUM(long_value_usd), 0) AS long_value,
                  COALESCE(SUM(open_liability_usd), 0) AS liability,
                  COALESCE(SUM(unrealized_pnl), 0) AS unrealized,
                  COALESCE(SUM(collateral_locked_usd) FILTER (
                    WHERE strategy_type = 'cash_secured_put'
                       OR (strategy_type = 'wheel' AND COALESCE(wheel_state, '') = 'short_put')
                  ), 0) AS reserved,
                  COALESCE(SUM(collateral_locked_usd), 0) AS total_collateral
                FROM opt_strategies
                WHERE account_slug = :slug AND status = 'open'
                """,
                {"slug": slug},
            )
            .mappings()
            .one()
        )
        lots = (
            self._exec(
                """
                SELECT underlying_symbol, shares, avg_cost_basis
                FROM opt_stock_lots WHERE account_slug = :slug AND shares > 0
                """,
                {"slug": slug},
            )
            .mappings()
            .all()
        )
        marks = {str(k).upper(): _d(v) for k, v in (stock_marks or {}).items()}
        stock_cost = _ZERO
        stock_value = _ZERO
        for lot in lots:
            shares = int(lot["shares"])
            cost = _d(lot["avg_cost_basis"])
            stock_cost += cost * shares
            stock_value += marks.get(lot["underlying_symbol"], cost) * shares
        if stock_holdings_value is not None:
            stock_value = _d(stock_holdings_value)

        cash = _q2(_d(acc["cash_balance"]) + _d(cash_delta))
        long_value = _q2(agg["long_value"])
        liability = _q2(agg["liability"])
        stock_value = _q2(stock_value)
        equity = _q2(cash + long_value - liability + stock_value)
        reserved = _q2(agg["reserved"])
        margin_used = _q2(_d(agg["total_collateral"]) - _d(agg["reserved"]))
        unrealized = _q2(_d(agg["unrealized"]) + (stock_value - _q2(stock_cost)))

        peak = max(_d(acc["peak_equity"]), equity)
        dd_usd = peak - equity
        dd_pct = (dd_usd / peak * 100) if peak > 0 else _ZERO
        max_dd_usd = max(_d(acc["max_drawdown_usd"]), dd_usd)
        max_dd_pct = max(_d(acc["max_drawdown_pct"]), dd_pct)

        self._exec(
            """
            UPDATE opt_accounts SET
              cash_balance = :cash,
              cash_reserved = :reserved,
              margin_used = :margin,
              long_options_market_value = :long_value,
              short_options_liability = :liability,
              stock_holdings_value = :stock_value,
              equity = :equity,
              peak_equity = :peak,
              max_drawdown_usd = :max_dd_usd,
              max_drawdown_pct = :max_dd_pct,
              unrealized_pnl = :unrealized,
              premium_collected_gross = premium_collected_gross + :pcg,
              premium_realized_net = premium_realized_net + :prn,
              realized_pnl = realized_pnl + :rpd,
              fees_paid = fees_paid + :fees,
              updated_at = NOW()
            WHERE slug = :slug
            """,
            {
                "slug": slug,
                "cash": cash,
                "reserved": reserved,
                "margin": margin_used,
                "long_value": long_value,
                "liability": liability,
                "stock_value": stock_value,
                "equity": equity,
                "peak": _q2(peak),
                "max_dd_usd": _q2(max_dd_usd),
                "max_dd_pct": _q4(max_dd_pct),
                "unrealized": unrealized,
                "pcg": _q2(premium_collected_gross_delta),
                "prn": _q2(premium_realized_net_delta),
                "rpd": _q2(realized_pnl_delta),
                "fees": _q2(fees_delta),
            },
        )
        return self._account_row(slug)

    # ----------------------------------------------------- contracts / chains
    _CONTRACT_UPSERT_SQL = """
        INSERT INTO opt_contracts (
          occ_symbol, underlying_symbol, option_right, strike, expiration_date,
          multiplier, exercise_style, root_symbol, tradable, open_interest, close_price,
          updated_at
        ) VALUES (
          :occ, :underlying, :right, :strike, :expiration,
          :multiplier, :style, :root, :tradable, :oi, :close_price, NOW()
        )
        ON CONFLICT (occ_symbol) DO UPDATE SET
          tradable = EXCLUDED.tradable,
          open_interest = EXCLUDED.open_interest,
          close_price = COALESCE(EXCLUDED.close_price, opt_contracts.close_price),
          updated_at = NOW()
        RETURNING id
    """

    @staticmethod
    def _contract_params(spec: OptionContractSpec) -> dict[str, Any]:
        return {
            "occ": spec.occ_symbol,
            "underlying": spec.underlying_symbol,
            "right": spec.right.value,
            "strike": spec.strike,
            "expiration": spec.expiration_date,
            "multiplier": spec.multiplier,
            "style": spec.exercise_style.value,
            "root": spec.root_symbol or spec.underlying_symbol,
            "tradable": spec.tradable,
            "oi": spec.open_interest,
            "close_price": spec.close_price,
        }

    def upsert_contract(self, spec: OptionContractSpec) -> str:
        """Insert/refresh a contract master row. Returns contract id."""
        row = self._exec(self._CONTRACT_UPSERT_SQL, self._contract_params(spec)).first()
        self._maybe_commit()
        return str(row[0])

    def save_chain_snapshots(self, snapshots: Iterable[OptionQuoteSnapshot]) -> int:
        """Persist quote snapshots (and refresh their contract master rows). Returns count."""
        snaps = list(snapshots)
        if not snaps:
            return 0
        seen: dict[str, OptionContractSpec] = {}
        for s in snaps:
            seen[s.contract.occ_symbol] = s.contract
        for spec in seen.values():
            self._exec(self._CONTRACT_UPSERT_SQL, self._contract_params(spec))
        params = []
        for s in snaps:
            mid = s.mid
            if mid is None and s.bid is not None and s.ask is not None:
                mid = (s.bid + s.ask) / 2
            params.append(
                {
                    "underlying": s.contract.underlying_symbol,
                    "occ": s.contract.occ_symbol,
                    "uprice": s.underlying_price,
                    "bid": s.bid,
                    "ask": s.ask,
                    "mid": mid,
                    "last": s.last_trade,
                    "volume": s.volume,
                    "oi": s.open_interest,
                    "iv": s.implied_volatility,
                    "delta": s.greeks.delta,
                    "gamma": s.greeks.gamma,
                    "theta": s.greeks.theta,
                    "vega": s.greeks.vega,
                    "rho": s.greeks.rho,
                    "local": s.greeks.computed_locally,
                    "tier": _enum_value(s.feed_tier),
                    "indicative": s.is_indicative,
                    "delayed": s.is_delayed_trades,
                    "stale": s.is_stale,
                    "spread": s.spread_pct,
                    "qts": s.quote_timestamp,
                    "sts": s.snapshot_timestamp,
                    "warnings": _json(list(s.data_warnings)),
                }
            )
        self.session.execute(
            text(
                """
                INSERT INTO opt_chain_snapshots (
                  underlying_symbol, occ_symbol, underlying_price, bid, ask, mid, last_trade,
                  volume, open_interest, implied_volatility, delta, gamma, theta, vega, rho,
                  greeks_computed_locally, feed_tier, is_indicative, is_delayed_trades,
                  is_stale, spread_pct, quote_timestamp, snapshot_timestamp, data_warnings
                ) VALUES (
                  :underlying, :occ, :uprice, :bid, :ask, :mid, :last,
                  :volume, :oi, :iv, :delta, :gamma, :theta, :vega, :rho,
                  :local, :tier, :indicative, :delayed,
                  :stale, :spread, :qts, :sts, CAST(:warnings AS jsonb)
                )
                """
            ),
            params,
        )
        self._maybe_commit()
        return len(snaps)

    def get_latest_chain_snapshots(
        self, symbol: str, *, window_minutes: int = 15
    ) -> list[OptionQuoteSnapshot]:
        """Latest snapshot per contract within ``window_minutes`` of the newest snapshot."""
        sym = symbol.upper()
        rows = (
            self._exec(
                """
                WITH newest AS (
                  SELECT MAX(snapshot_timestamp) AS ts
                  FROM opt_chain_snapshots WHERE underlying_symbol = :sym
                )
                SELECT DISTINCT ON (s.occ_symbol)
                  s.*, c.option_right, c.strike, c.expiration_date, c.multiplier,
                  c.exercise_style, c.root_symbol, c.tradable, c.close_price
                FROM opt_chain_snapshots s
                JOIN opt_contracts c ON c.occ_symbol = s.occ_symbol
                CROSS JOIN newest n
                WHERE s.underlying_symbol = :sym
                  AND n.ts IS NOT NULL
                  AND s.snapshot_timestamp >= n.ts - make_interval(mins => :window)
                ORDER BY s.occ_symbol, s.snapshot_timestamp DESC
                """,
                {"sym": sym, "window": window_minutes},
            )
            .mappings()
            .all()
        )
        out: list[OptionQuoteSnapshot] = []
        for r in rows:
            spec = OptionContractSpec(
                occ_symbol=r["occ_symbol"],
                underlying_symbol=r["underlying_symbol"],
                right=OptionRight(r["option_right"]),
                strike=_d(r["strike"]),
                expiration_date=r["expiration_date"],
                multiplier=int(r["multiplier"]),
                exercise_style=ExerciseStyle(r["exercise_style"]),
                tradable=bool(r["tradable"]),
                root_symbol=r["root_symbol"],
                open_interest=int(r["open_interest"] or 0),
                close_price=r["close_price"],
            )
            out.append(
                OptionQuoteSnapshot(
                    contract=spec,
                    underlying_price=_d(r["underlying_price"]),
                    bid=r["bid"],
                    ask=r["ask"],
                    mid=r["mid"],
                    last_trade=r["last_trade"],
                    volume=int(r["volume"] or 0),
                    open_interest=int(r["open_interest"] or 0),
                    implied_volatility=r["implied_volatility"],
                    greeks=Greeks(
                        delta=r["delta"],
                        gamma=r["gamma"],
                        theta=r["theta"],
                        vega=r["vega"],
                        rho=r["rho"],
                        computed_locally=bool(r["greeks_computed_locally"]),
                    ),
                    quote_timestamp=r["quote_timestamp"],
                    snapshot_timestamp=r["snapshot_timestamp"],
                    feed_tier=DataFeedTier(r["feed_tier"]),
                    is_indicative=bool(r["is_indicative"]),
                    is_delayed_trades=bool(r["is_delayed_trades"]),
                    is_stale=bool(r["is_stale"]),
                    spread_pct=r["spread_pct"],
                    data_warnings=list(_json_load(r["data_warnings"], [])),
                )
            )
        out.sort(key=lambda q: (q.contract.expiration_date, q.contract.strike, q.contract.right.value))
        return out

    def prune_chain_snapshots(self, older_than_hours: int = 24) -> int:
        res = self._exec(
            "DELETE FROM opt_chain_snapshots WHERE snapshot_timestamp < NOW() - make_interval(hours => :h)",
            {"h": older_than_hours},
        )
        self._maybe_commit()
        return res.rowcount or 0

    # ------------------------------------------------------------- strategies
    def _contract_id_for_leg(self, leg: LegPlan) -> Optional[str]:
        if not leg.occ_symbol or leg.right is None or leg.strike is None or leg.expiration_date is None:
            if leg.occ_symbol:
                row = self._exec(
                    "SELECT id FROM opt_contracts WHERE occ_symbol = :o", {"o": leg.occ_symbol}
                ).first()
                return str(row[0]) if row else None
            return None
        spec = OptionContractSpec(
            occ_symbol=leg.occ_symbol,
            underlying_symbol=leg.underlying_symbol,
            right=leg.right,
            strike=leg.strike,
            expiration_date=leg.expiration_date,
            multiplier=leg.multiplier,
            exercise_style=leg.exercise_style,
        )
        row = self._exec(self._CONTRACT_UPSERT_SQL, self._contract_params(spec)).first()
        return str(row[0])

    @staticmethod
    def _match_fill(
        fills: Optional[Sequence[Mapping[str, Any]]], index: int, occ: Optional[str]
    ) -> Mapping[str, Any]:
        if not fills:
            return {}
        if occ:
            for f in fills:
                if f.get("occ_symbol") == occ:
                    return f
        if index < len(fills) and fills[index].get("occ_symbol") in (None, occ):
            return fills[index]
        return {}

    def create_strategy_with_legs_and_fill(
        self,
        account_slug: str,
        candidate: StrategyCandidate,
        fills: Optional[Sequence[Mapping[str, Any]]] = None,
        *,
        mode: Optional[DecisionMode] = None,
        idempotency_key: Optional[str] = None,
        ai_rationale: Optional[str] = None,
        strategy_label_he: Optional[str] = None,
        payoff_curve: Optional[Sequence[PayoffPoint | Mapping[str, Any]]] = None,
        wheel_state: Optional[str] = None,
        metadata: Optional[Mapping[str, Any]] = None,
        collateral_locked_usd: Optional[Any] = None,
        filled_at: Optional[datetime] = None,
    ) -> OptionsStrategyGroupView:
        """Persist order + strategy + legs + fills + ledger OPEN event and update the account.

        ``fills`` entries (matched by ``occ_symbol`` or by index into ``candidate.legs``)
        may carry: fill_price, bid_at_fill, ask_at_fill, slippage_usd, fee_usd,
        current_mark, feed_tier, is_indicative. Missing fill_price falls back to
        mid, else ask (long) / bid (short). Idempotent on ``idempotency_key``.
        """
        acc = self._account_row(account_slug, lock=True)
        key = idempotency_key or f"open:{account_slug}:{candidate.candidate_id}:{uuid.uuid4().hex}"
        action_mode = (mode or DecisionMode(acc["mode"])).value
        now = filled_at or datetime.now(timezone.utc)

        order_id = str(uuid.uuid4())
        inserted = self._exec(
            """
            INSERT INTO opt_orders (id, account_id, action_kind, legs_payload, order_status,
                                    idempotency_key, created_at, filled_at)
            VALUES (CAST(:id AS uuid), :account_id, 'OPEN', CAST(:payload AS jsonb), 'filled',
                    :key, :now, :now)
            ON CONFLICT (idempotency_key) DO NOTHING
            RETURNING id
            """,
            {
                "id": order_id,
                "account_id": acc["id"],
                "payload": _json([l.model_dump(mode="json") for l in candidate.legs]),
                "key": key,
                "now": now,
            },
        ).first()
        if inserted is None:  # replay of an already-processed order
            existing = self._exec(
                "SELECT strategy_id FROM opt_orders WHERE idempotency_key = :k", {"k": key}
            ).first()
            if existing and existing[0]:
                view = self.get_strategy_group(str(existing[0]))
                if view is not None:
                    return view
            raise ValueError(f"Order {key!r} exists without a strategy")

        strategy_id = str(uuid.uuid4())
        label = strategy_label_he or ALL_9_STRATEGY_LABELS_HE.get(
            _enum_value(candidate.strategy_type), str(_enum_value(candidate.strategy_type))
        )
        collateral = _q2(
            collateral_locked_usd
            if collateral_locked_usd is not None
            else candidate.collateral_required_usd
        )

        leg_rows: list[dict[str, Any]] = []
        fill_rows: list[dict[str, Any]] = []
        net_cash = _ZERO
        total_fees = _ZERO
        long_value = _ZERO
        liability = _ZERO
        net_delta = net_theta = net_vega = 0.0
        for idx, leg in enumerate(candidate.legs):
            if _is_stock_leg(leg.leg_role):
                continue
            fill = self._match_fill(fills, idx, leg.occ_symbol)
            qty = max(1, int(leg.ratio) * int(candidate.contracts_qty))
            side = _enum_value(leg.side)
            price = fill.get("fill_price")
            if price is None:
                price = leg.mid
            if price is None:
                price = leg.ask if side == "long" else leg.bid
            if price is None:
                raise ValueError(f"No fill price available for leg {leg.occ_symbol}")
            price = _q4(price)
            fee = _q4(fill.get("fee_usd", 0))
            mark = _q4(fill.get("current_mark", price))
            cash_flow = _q2(_leg_cash_flow(side, price, leg.multiplier, qty))
            net_cash += cash_flow
            total_fees += fee
            value = mark * leg.multiplier * qty
            if side == "long":
                long_value += value
            else:
                liability += value
            sgn = _sign(side)
            scale = leg.multiplier * qty * sgn
            net_delta += (leg.greeks.delta or 0.0) * scale
            net_theta += (leg.greeks.theta or 0.0) * scale
            net_vega += (leg.greeks.vega or 0.0) * scale
            leg_id = str(uuid.uuid4())
            contract_id = self._contract_id_for_leg(leg)
            leg_rows.append(
                {
                    "id": leg_id,
                    "strategy_id": strategy_id,
                    "contract_id": contract_id,
                    "occ": leg.occ_symbol,
                    "underlying": leg.underlying_symbol,
                    "role": _enum_value(leg.leg_role),
                    "side": side,
                    "right": _enum_value(leg.right) if leg.right else None,
                    "strike": leg.strike,
                    "expiration": leg.expiration_date,
                    "multiplier": leg.multiplier,
                    "quantity": qty,
                    "entry": price,
                    "mark": mark,
                    "bid": leg.bid if fill.get("bid_at_fill") is None else fill.get("bid_at_fill"),
                    "ask": leg.ask if fill.get("ask_at_fill") is None else fill.get("ask_at_fill"),
                    "unreal": _q2(_leg_pnl(side, price, mark, leg.multiplier, qty)),
                    "delta": leg.greeks.delta,
                    "gamma": leg.greeks.gamma,
                    "theta": leg.greeks.theta,
                    "vega": leg.greeks.vega,
                    "iv": leg.implied_volatility,
                }
            )
            fill_rows.append(
                {
                    "id": str(uuid.uuid4()),
                    "order_id": order_id,
                    "strategy_id": strategy_id,
                    "leg_id": leg_id,
                    "occ": leg.occ_symbol,
                    "side": side,
                    "quantity": qty,
                    "multiplier": leg.multiplier,
                    "price": price,
                    "bid": fill.get("bid_at_fill", leg.bid),
                    "ask": fill.get("ask_at_fill", leg.ask),
                    "slippage": _q4(fill.get("slippage_usd", 0)),
                    "fee": fee,
                    "cash_flow": cash_flow,
                    "tier": str(_enum_value(fill.get("feed_tier", candidate.feed_tier))),
                    "indicative": bool(fill.get("is_indicative", candidate.is_indicative)),
                    "now": now,
                }
            )
        if not leg_rows:
            raise ValueError("Strategy has no option legs")

        net_cash = _q2(net_cash)
        total_fees = _q2(total_fees)
        long_value = _q2(long_value)
        liability = _q2(liability)
        unrealized = _q2(sum((r["unreal"] for r in leg_rows), _ZERO) - total_fees)
        premium_gross = net_cash if (candidate.is_credit and net_cash > 0) else _ZERO

        meta: dict[str, Any] = {
            "candidate_id": candidate.candidate_id,
            "dte_at_open": candidate.dte,
            "underlying_price_at_open": float(candidate.underlying_price),
            "break_even_prices": [float(p) for p in candidate.break_even_prices],
            "probability_of_profit": candidate.probability_of_profit,
            "iv_rank": candidate.iv_rank,
            "rules_score": candidate.rules_score,
            "liquidity_score": candidate.liquidity_score,
            "feed_tier": _enum_value(candidate.feed_tier),
            "is_indicative": candidate.is_indicative,
            "warnings": list(candidate.warnings),
            "rationale_summary": candidate.rationale_summary,
            "skills_evidence": candidate.skills_evidence,
            "idempotency_key": key,
        }
        meta.update(dict(metadata or {}))
        curve = [
            p.model_dump(mode="json") if isinstance(p, PayoffPoint) else dict(p)
            for p in (payoff_curve or [])
        ]

        self._exec(
            """
            INSERT INTO opt_strategies (
              id, account_id, account_slug, strategy_type, strategy_label_he, underlying_symbol,
              status, mode, contracts_qty, expiration_date, is_credit, net_entry_cash_flow,
              open_liability_usd, long_value_usd, collateral_locked_usd, max_profit_usd,
              max_loss_usd, unrealized_pnl, realized_pnl, fees_paid, net_delta, net_theta,
              net_vega, wheel_state, ai_rationale, payoff_curve, metadata, opened_at, updated_at
            ) VALUES (
              CAST(:id AS uuid), :account_id, :slug, :stype, :label, :underlying,
              'open', :mode, :qty, :expiration, :is_credit, :net_cash,
              :liability, :long_value, :collateral, :max_profit,
              :max_loss, :unrealized, 0, :fees, :nd, :nt,
              :nv, :wheel_state, :rationale, CAST(:curve AS jsonb), CAST(:meta AS jsonb), :now, :now
            )
            """,
            {
                "id": strategy_id,
                "account_id": acc["id"],
                "slug": account_slug,
                "stype": _enum_value(candidate.strategy_type),
                "label": label,
                "underlying": candidate.underlying_symbol,
                "mode": action_mode,
                "qty": candidate.contracts_qty,
                "expiration": candidate.expiration_date,
                "is_credit": candidate.is_credit,
                "net_cash": net_cash,
                "liability": liability,
                "long_value": long_value,
                "collateral": collateral,
                "max_profit": _q2(candidate.max_profit_usd) if candidate.max_profit_usd is not None else None,
                "max_loss": _q2(candidate.max_loss_usd),
                "unrealized": unrealized,
                "fees": total_fees,
                "nd": net_delta,
                "nt": net_theta,
                "nv": net_vega,
                "wheel_state": wheel_state,
                "rationale": ai_rationale,
                "curve": _json(curve),
                "meta": _json(meta),
                "now": now,
            },
        )
        self.session.execute(
            text(
                """
                INSERT INTO opt_legs (
                  id, strategy_id, contract_id, occ_symbol, underlying_symbol, leg_role, side,
                  option_right, strike, expiration_date, multiplier, quantity, entry_price,
                  current_mark, bid, ask, unrealized_pnl, delta, gamma, theta, vega,
                  implied_volatility, status, updated_at
                ) VALUES (
                  CAST(:id AS uuid), CAST(:strategy_id AS uuid), CAST(:contract_id AS uuid), :occ,
                  :underlying, :role, :side, :right, :strike, :expiration, :multiplier, :quantity,
                  :entry, :mark, :bid, :ask, :unreal, :delta, :gamma, :theta, :vega, :iv,
                  'open', NOW()
                )
                """
            ),
            leg_rows,
        )
        self._exec(
            "UPDATE opt_orders SET strategy_id = CAST(:sid AS uuid) WHERE id = CAST(:oid AS uuid)",
            {"sid": strategy_id, "oid": order_id},
        )
        self.session.execute(
            text(
                """
                INSERT INTO opt_fills (
                  id, order_id, strategy_id, leg_id, occ_symbol, side, quantity, multiplier,
                  fill_price, bid_at_fill, ask_at_fill, slippage_usd, fee_usd, cash_flow_usd,
                  feed_tier, is_indicative, filled_at
                ) VALUES (
                  CAST(:id AS uuid), CAST(:order_id AS uuid), CAST(:strategy_id AS uuid),
                  CAST(:leg_id AS uuid), :occ, :side, :quantity, :multiplier,
                  :price, :bid, :ask, :slippage, :fee, :cash_flow,
                  :tier, :indicative, :now
                )
                """
            ),
            fill_rows,
        )
        self._insert_ledger(
            acc,
            event_type="OPEN",
            strategy_id=strategy_id,
            underlying=candidate.underlying_symbol,
            strategy_type=str(_enum_value(candidate.strategy_type)),
            cash_delta=net_cash - total_fees,
            realized_delta=_ZERO,
            collateral_delta=collateral,
            fee=total_fees,
            description=(
                f"Opened {label} {candidate.underlying_symbol} x{candidate.contracts_qty} "
                f"({'credit' if net_cash >= 0 else 'debit'} ${abs(net_cash):,.2f})"
            ),
            metadata={"order_id": order_id, "candidate_id": candidate.candidate_id,
                      "premium_collected_gross": float(premium_gross)},
            created_at=now,
        )
        self._refresh_account(
            account_slug,
            cash_delta=net_cash - total_fees,
            premium_collected_gross_delta=premium_gross,
            fees_delta=total_fees,
        )
        self._maybe_commit()
        view = self.get_strategy_group(strategy_id)
        assert view is not None
        return view

    def _insert_ledger(
        self,
        acc: Mapping[str, Any],
        *,
        event_type: str,
        strategy_id: Optional[str],
        underlying: str,
        strategy_type: str,
        cash_delta: Any,
        realized_delta: Any,
        collateral_delta: Any,
        fee: Any,
        description: str,
        metadata: Optional[Mapping[str, Any]] = None,
        created_at: Optional[datetime] = None,
    ) -> str:
        row = self._exec(
            """
            INSERT INTO opt_ledger_events (
              account_id, account_slug, strategy_id, event_type, underlying_symbol, strategy_type,
              cash_delta, realized_pnl_delta, collateral_delta, fee_usd, description, metadata,
              created_at
            ) VALUES (
              :account_id, :slug, CAST(:sid AS uuid), :etype, :underlying, :stype,
              :cash, :realized, :collateral, :fee, :desc, CAST(:meta AS jsonb),
              COALESCE(:created_at, NOW())
            ) RETURNING id
            """,
            {
                "account_id": acc["id"],
                "slug": acc["slug"],
                "sid": strategy_id,
                "etype": event_type,
                "underlying": underlying or "",
                "stype": strategy_type or "",
                "cash": _q2(cash_delta),
                "realized": _q2(realized_delta),
                "collateral": _q2(collateral_delta),
                "fee": _q2(fee),
                "desc": description,
                "meta": _json(dict(metadata or {})),
                "created_at": created_at,
            },
        ).first()
        return str(row[0])

    # --- reading strategies -------------------------------------------------
    def _legs_for(self, strategy_ids: Sequence[str]) -> dict[str, list[dict[str, Any]]]:
        if not strategy_ids:
            return {}
        rows = (
            self._exec(
                """
                SELECT * FROM opt_legs
                WHERE strategy_id = ANY(CAST(:ids AS uuid[]))
                ORDER BY strategy_id, side DESC, option_right, strike, id
                """,
                {"ids": list(strategy_ids)},
            )
            .mappings()
            .all()
        )
        out: dict[str, list[dict[str, Any]]] = {}
        for r in rows:
            out.setdefault(str(r["strategy_id"]), []).append(_row(r))
        return out

    @staticmethod
    def _leg_view(r: Mapping[str, Any]) -> OptionsLegView:
        return OptionsLegView(
            id=str(r["id"]),
            occ_symbol=r["occ_symbol"],
            underlying_symbol=r["underlying_symbol"],
            leg_role=r["leg_role"],
            side=r["side"],
            right=r["option_right"],
            strike=_fo(r["strike"]),
            expiration_date=r["expiration_date"],
            multiplier=int(r["multiplier"]),
            quantity=int(r["quantity"]),
            entry_price=_f(r["entry_price"]),
            current_mark=_f(r["current_mark"]),
            bid=_fo(r["bid"]),
            ask=_fo(r["ask"]),
            unrealized_pnl=_f(r["unrealized_pnl"]),
            realized_pnl=_f(r["realized_pnl"]),
            delta=r["delta"],
            gamma=r["gamma"],
            theta=r["theta"],
            vega=r["vega"],
            implied_volatility=r["implied_volatility"],
            status=r["status"],
        )

    def _strategy_views(self, rows: Sequence[Mapping[str, Any]]) -> list[OptionsStrategyGroupView]:
        legs = self._legs_for([str(r["id"]) for r in rows])
        today = _today()
        views: list[OptionsStrategyGroupView] = []
        for r in rows:
            curve: list[PayoffPoint] = []
            for p in _json_load(r["payoff_curve"], []) or []:
                try:
                    curve.append(PayoffPoint(**p))
                except Exception:
                    continue
            exp = r["expiration_date"]
            views.append(
                OptionsStrategyGroupView(
                    id=str(r["id"]),
                    account_slug=r["account_slug"],
                    strategy_type=r["strategy_type"],
                    strategy_label_he=r["strategy_label_he"],
                    underlying_symbol=r["underlying_symbol"],
                    status=r["status"],
                    mode=DecisionMode(r["mode"]),
                    contracts_qty=int(r["contracts_qty"]),
                    expiration_date=exp,
                    dte=(exp - today).days if exp else None,
                    is_credit=bool(r["is_credit"]),
                    net_entry_cash_flow=_f(r["net_entry_cash_flow"]),
                    open_liability_usd=_f(r["open_liability_usd"]),
                    long_value_usd=_f(r["long_value_usd"]),
                    collateral_locked_usd=_f(r["collateral_locked_usd"]),
                    max_profit_usd=_fo(r["max_profit_usd"]),
                    max_loss_usd=_f(r["max_loss_usd"]),
                    unrealized_pnl=_f(r["unrealized_pnl"]),
                    realized_pnl=_f(r["realized_pnl"]),
                    fees_paid=_f(r["fees_paid"]),
                    net_delta=_f(r["net_delta"]),
                    net_theta=_f(r["net_theta"]),
                    net_vega=_f(r["net_vega"]),
                    pin_risk_warning=bool(r["pin_risk_warning"]),
                    early_assignment_warning=bool(r["early_assignment_warning"]),
                    wheel_state=r["wheel_state"],
                    opened_at=r["opened_at"],
                    closed_at=r["closed_at"],
                    close_reason=r["close_reason"],
                    ai_rationale=r["ai_rationale"],
                    legs=[self._leg_view(l) for l in legs.get(str(r["id"]), [])],
                    payoff_curve=curve,
                )
            )
        return views

    def list_strategy_groups(
        self,
        account_slug: str,
        status: Optional[str | Sequence[str]] = None,
        *,
        limit: int = 500,
    ) -> list[OptionsStrategyGroupView]:
        """List strategies. ``status``: None=all, 'open', 'closed' (=ANY terminal status:
        closed/expired/assigned/rolled), another exact status, or a sequence of statuses."""
        if status is None:
            statuses: Optional[list[str]] = None
        elif isinstance(status, str):
            statuses = list(CLOSED_STATUSES) if status == "closed" else [status]
        else:
            statuses = [s for st in status for s in (CLOSED_STATUSES if st == "closed" else (st,))]
        rows = (
            self._exec(
                """
                SELECT * FROM opt_strategies
                WHERE account_slug = :slug
                  AND (CAST(:statuses AS text[]) IS NULL OR status = ANY(CAST(:statuses AS text[])))
                ORDER BY (status = 'open') DESC, COALESCE(closed_at, opened_at) DESC
                LIMIT :limit
                """,
                {"slug": account_slug, "statuses": statuses, "limit": limit},
            )
            .mappings()
            .all()
        )
        return self._strategy_views([_row(r) for r in rows])

    def get_strategy_group(self, strategy_id: str) -> Optional[OptionsStrategyGroupView]:
        row = (
            self._exec(
                "SELECT * FROM opt_strategies WHERE id = CAST(:id AS uuid)", {"id": str(strategy_id)}
            )
            .mappings()
            .first()
        )
        if row is None:
            return None
        return self._strategy_views([_row(row)])[0]

    def _strategy_row(self, strategy_id: str, *, lock: bool = False) -> dict[str, Any]:
        row = (
            self._exec(
                "SELECT * FROM opt_strategies WHERE id = CAST(:id AS uuid)"
                + (" FOR UPDATE" if lock else ""),
                {"id": str(strategy_id)},
            )
            .mappings()
            .first()
        )
        if row is None:
            raise KeyError(f"strategy {strategy_id} not found")
        return _row(row)

    def _recalc_strategy(self, strategy_id: str) -> None:
        """Recompute leg P&L + strategy aggregates from OPEN option legs' marks."""
        strat = self._strategy_row(strategy_id)
        legs = self._legs_for([strategy_id]).get(strategy_id, [])
        long_value = liability = _ZERO
        leg_pnl_total = _ZERO
        nd = nt = nv = 0.0
        for leg in legs:
            if leg["status"] != "open" or _is_stock_leg(leg["leg_role"]):
                continue
            mult, qty, side = int(leg["multiplier"]), int(leg["quantity"]), leg["side"]
            mark = _d(leg["current_mark"])
            pnl = _q2(_leg_pnl(side, leg["entry_price"], mark, mult, qty))
            leg_pnl_total += pnl
            if pnl != _d(leg["unrealized_pnl"]):
                self._exec(
                    "UPDATE opt_legs SET unrealized_pnl = :p, updated_at = NOW() WHERE id = CAST(:id AS uuid)",
                    {"p": pnl, "id": str(leg["id"])},
                )
            value = mark * mult * qty
            if side == "long":
                long_value += value
            else:
                liability += value
            scale = mult * qty * _sign(side)
            nd += (leg["delta"] or 0.0) * scale
            nt += (leg["theta"] or 0.0) * scale
            nv += (leg["vega"] or 0.0) * scale
        self._exec(
            """
            UPDATE opt_strategies SET
              long_value_usd = :lv, open_liability_usd = :liab, unrealized_pnl = :unreal,
              net_delta = :nd, net_theta = :nt, net_vega = :nv, updated_at = NOW()
            WHERE id = CAST(:id AS uuid)
            """,
            {
                "id": strategy_id,
                "lv": _q2(long_value),
                "liab": _q2(liability),
                "unreal": _q2(leg_pnl_total - _d(strat["fees_paid"])),
                "nd": nd,
                "nt": nt,
                "nv": nv,
            },
        )

    def update_strategy_and_legs(
        self,
        strategy_id: str,
        *,
        leg_updates: Optional[Sequence[Mapping[str, Any]]] = None,
        pin_risk_warning: Optional[bool] = None,
        early_assignment_warning: Optional[bool] = None,
        wheel_state: Optional[str] = None,
        collateral_locked_usd: Optional[Any] = None,
        ai_rationale: Optional[str] = None,
        payoff_curve: Optional[Sequence[PayoffPoint | Mapping[str, Any]]] = None,
        metadata_patch: Optional[Mapping[str, Any]] = None,
        stock_marks: Optional[Mapping[str, Any]] = None,
    ) -> OptionsStrategyGroupView:
        """Mark-to-market an OPEN strategy and refresh account aggregates.

        ``leg_updates``: dicts keyed by ``leg_id`` or ``occ_symbol`` with any of
        current_mark, bid, ask, delta, gamma, theta, vega, implied_volatility.
        ``current_mark`` defaults to bid/ask mid when only a quote is supplied.
        Premium/realized fields are never touched here.
        """
        strat = self._strategy_row(strategy_id, lock=True)
        for upd in leg_updates or []:
            params: dict[str, Any] = {f: upd.get(f) for f in _LEG_MARK_FIELDS}
            if params["current_mark"] is None and upd.get("bid") is not None and upd.get("ask") is not None:
                params["current_mark"] = (_d(upd["bid"]) + _d(upd["ask"])) / 2
            if params["current_mark"] is not None:
                params["current_mark"] = _q4(params["current_mark"])
            params.update(sid=strategy_id, leg_id=_s(upd.get("leg_id")), occ=upd.get("occ_symbol"))
            if params["leg_id"] is None and params["occ"] is None:
                raise ValueError("leg update needs leg_id or occ_symbol")
            self._exec(
                """
                UPDATE opt_legs SET
                  current_mark = COALESCE(:current_mark, current_mark),
                  bid = COALESCE(:bid, bid), ask = COALESCE(:ask, ask),
                  delta = COALESCE(:delta, delta), gamma = COALESCE(:gamma, gamma),
                  theta = COALESCE(:theta, theta), vega = COALESCE(:vega, vega),
                  implied_volatility = COALESCE(:implied_volatility, implied_volatility),
                  updated_at = NOW()
                WHERE strategy_id = CAST(:sid AS uuid) AND status = 'open'
                  AND ((CAST(:leg_id AS text) IS NOT NULL AND id = CAST(:leg_id AS uuid))
                    OR (CAST(:leg_id AS text) IS NULL AND occ_symbol = :occ))
                """,
                params,
            )
        self._exec(
            """
            UPDATE opt_strategies SET
              pin_risk_warning = COALESCE(:pin, pin_risk_warning),
              early_assignment_warning = COALESCE(:early, early_assignment_warning),
              wheel_state = COALESCE(:wheel, wheel_state),
              collateral_locked_usd = COALESCE(:collateral, collateral_locked_usd),
              ai_rationale = COALESCE(:rationale, ai_rationale),
              payoff_curve = COALESCE(CAST(:curve AS jsonb), payoff_curve),
              metadata = metadata || CAST(:meta AS jsonb),
              updated_at = NOW()
            WHERE id = CAST(:id AS uuid)
            """,
            {
                "id": strategy_id,
                "pin": pin_risk_warning,
                "early": early_assignment_warning,
                "wheel": wheel_state,
                "collateral": _q2(collateral_locked_usd) if collateral_locked_usd is not None else None,
                "rationale": ai_rationale,
                "curve": _json(
                    [p.model_dump(mode="json") if isinstance(p, PayoffPoint) else dict(p) for p in payoff_curve]
                )
                if payoff_curve is not None
                else None,
                "meta": _json(dict(metadata_patch or {})),
            },
        )
        if strat["status"] == "open":
            self._recalc_strategy(strategy_id)
            self._refresh_account(strat["account_slug"], stock_marks=stock_marks)
        self._maybe_commit()
        view = self.get_strategy_group(strategy_id)
        assert view is not None
        return view

    def close_strategy_record(
        self,
        strategy_id: str,
        *,
        status: str = "closed",
        close_reason: Optional[str] = None,
        close_fills: Optional[Sequence[Mapping[str, Any]]] = None,
        idempotency_key: Optional[str] = None,
        closed_at: Optional[datetime] = None,
        stock_marks: Optional[Mapping[str, Any]] = None,
    ) -> OptionsStrategyGroupView:
        """Finalize an OPEN strategy (closed/expired/assigned/rolled) and realize P&L.

        ``close_fills`` entries (by ``leg_id`` or ``occ_symbol``) may carry fill_price,
        bid_at_fill, ask_at_fill, slippage_usd, fee_usd, feed_tier, is_indicative.
        Legs without a fill default to price 0 for expired/assigned (option extinguished;
        pass intrinsic as fill_price for exercised long legs, and book stock via
        ``upsert_stock_lot`` + ``update_account_balances``) and to the current mark for
        closed/rolled. Idempotent: closing an already-finalized strategy is a no-op.
        """
        if status not in _TERMINAL_STATUS_EVENT:
            raise ValueError(f"status must be one of {sorted(_TERMINAL_STATUS_EVENT)}")
        strat = self._strategy_row(strategy_id, lock=True)
        if strat["status"] != "open":
            view = self.get_strategy_group(strategy_id)
            assert view is not None
            return view
        acc = self._account_row(strat["account_slug"], lock=True)
        now = closed_at or datetime.now(timezone.utc)
        key = idempotency_key or f"close:{strategy_id}"
        order_id = str(uuid.uuid4())
        inserted = self._exec(
            """
            INSERT INTO opt_orders (id, account_id, strategy_id, action_kind, legs_payload,
                                    order_status, idempotency_key, created_at, filled_at)
            VALUES (CAST(:id AS uuid), :account_id, CAST(:sid AS uuid), :kind,
                    CAST(:payload AS jsonb), 'filled', :key, :now, :now)
            ON CONFLICT (idempotency_key) DO NOTHING RETURNING id
            """,
            {
                "id": order_id,
                "account_id": acc["id"],
                "sid": strategy_id,
                "kind": _TERMINAL_STATUS_EVENT[status],
                "payload": _json(list(close_fills or [])),
                "key": key,
                "now": now,
            },
        ).first()
        if inserted is None:
            view = self.get_strategy_group(strategy_id)
            assert view is not None
            return view

        legs = [
            l for l in self._legs_for([strategy_id]).get(strategy_id, [])
            if l["status"] == "open" and not _is_stock_leg(l["leg_role"])
        ]
        by_id = {str(f["leg_id"]): f for f in (close_fills or []) if f.get("leg_id")}
        by_occ = {f["occ_symbol"]: f for f in (close_fills or []) if f.get("occ_symbol")}
        close_cash = close_fees = leg_realized_total = _ZERO
        for leg in legs:
            fill = by_id.get(str(leg["id"])) or by_occ.get(leg["occ_symbol"]) or {}
            price = fill.get("fill_price")
            if price is None:
                price = _ZERO if status in ("expired", "assigned") else leg["current_mark"]
            price = _q4(price)
            fee = _q4(fill.get("fee_usd", 0))
            mult, qty, side = int(leg["multiplier"]), int(leg["quantity"]), leg["side"]
            # closing flips the opening direction: long sells (+), short buys back (-)
            cash_flow = _q2(-_leg_cash_flow(side, price, mult, qty))
            realized = _q2(_leg_pnl(side, leg["entry_price"], price, mult, qty))
            close_cash += cash_flow
            close_fees += fee
            leg_realized_total += realized
            closing_side = "short" if side == "long" else "long"
            self._exec(
                """
                UPDATE opt_legs SET status = :status, current_mark = :price, unrealized_pnl = 0,
                  realized_pnl = :realized, updated_at = NOW()
                WHERE id = CAST(:id AS uuid)
                """,
                {"status": status, "price": price, "realized": realized, "id": str(leg["id"])},
            )
            self._exec(
                """
                INSERT INTO opt_fills (
                  order_id, strategy_id, leg_id, occ_symbol, side, quantity, multiplier,
                  fill_price, bid_at_fill, ask_at_fill, slippage_usd, fee_usd, cash_flow_usd,
                  feed_tier, is_indicative, filled_at
                ) VALUES (
                  CAST(:oid AS uuid), CAST(:sid AS uuid), CAST(:lid AS uuid), :occ, :side, :qty,
                  :mult, :price, :bid, :ask, :slip, :fee, :cash, :tier, :ind, :now
                )
                """,
                {
                    "oid": order_id,
                    "sid": strategy_id,
                    "lid": str(leg["id"]),
                    "occ": leg["occ_symbol"],
                    "side": closing_side,
                    "qty": qty,
                    "mult": mult,
                    "price": price,
                    "bid": fill.get("bid_at_fill"),
                    "ask": fill.get("ask_at_fill"),
                    "slip": _q4(fill.get("slippage_usd", 0)),
                    "fee": fee,
                    "cash": cash_flow,
                    "tier": str(_enum_value(fill.get("feed_tier", DataFeedTier.ALPACA_INDICATIVE))),
                    "ind": bool(fill.get("is_indicative", True)),
                    "now": now,
                },
            )

        close_cash = _q2(close_cash)
        close_fees = _q2(close_fees)
        total_fees = _q2(_d(strat["fees_paid"]) + close_fees)
        realized_total = _q2(leg_realized_total - total_fees)
        collateral = _d(strat["collateral_locked_usd"])
        self._exec(
            """
            UPDATE opt_strategies SET
              status = :status, close_reason = COALESCE(:reason, close_reason),
              open_liability_usd = 0, long_value_usd = 0, collateral_locked_usd = 0,
              unrealized_pnl = 0, realized_pnl = :realized, fees_paid = :fees,
              net_delta = 0, net_theta = 0, net_vega = 0,
              pin_risk_warning = FALSE, early_assignment_warning = FALSE,
              closed_at = :now, updated_at = NOW()
            WHERE id = CAST(:id AS uuid)
            """,
            {
                "id": strategy_id,
                "status": status,
                "reason": close_reason,
                "realized": realized_total,
                "fees": total_fees,
                "now": now,
            },
        )
        self._insert_ledger(
            acc,
            event_type=_TERMINAL_STATUS_EVENT[status],
            strategy_id=strategy_id,
            underlying=strat["underlying_symbol"],
            strategy_type=strat["strategy_type"],
            cash_delta=close_cash - close_fees,
            realized_delta=realized_total,
            collateral_delta=-collateral,
            fee=close_fees,
            description=(
                f"{_TERMINAL_STATUS_EVENT[status].title()} {strat['strategy_label_he']} "
                f"{strat['underlying_symbol']}: realized ${realized_total:,.2f}"
                + (f" ({close_reason})" if close_reason else "")
            ),
            metadata={"order_id": order_id, "close_cash_flow": float(close_cash)},
            created_at=now,
        )
        self._refresh_account(
            strat["account_slug"],
            cash_delta=close_cash - close_fees,
            realized_pnl_delta=realized_total,
            premium_realized_net_delta=realized_total if strat["is_credit"] else _ZERO,
            fees_delta=close_fees,
            stock_marks=stock_marks,
        )
        self._maybe_commit()
        view = self.get_strategy_group(strategy_id)
        assert view is not None
        return view

    # ------------------------------------------------------------ stock lots
    def get_stock_lot(self, account_slug: str, symbol: str) -> Optional[dict[str, Any]]:
        row = (
            self._exec(
                "SELECT * FROM opt_stock_lots WHERE account_slug = :s AND underlying_symbol = :sym",
                {"s": account_slug, "sym": symbol.upper()},
            )
            .mappings()
            .first()
        )
        return self._lot_dict(row) if row else None

    def get_stock_lots(self, account_slug: str, symbol: Optional[str] = None) -> list[dict[str, Any]]:
        rows = (
            self._exec(
                """
                SELECT * FROM opt_stock_lots
                WHERE account_slug = :s
                  AND (CAST(:sym AS text) IS NULL OR underlying_symbol = CAST(:sym AS text))
                ORDER BY underlying_symbol
                """,
                {"s": account_slug, "sym": symbol.upper() if symbol else None},
            )
            .mappings()
            .all()
        )
        return [self._lot_dict(r) for r in rows]

    @staticmethod
    def _lot_dict(r: Mapping[str, Any]) -> dict[str, Any]:
        d = _row(r)
        d["id"] = str(d["id"])
        d["account_id"] = str(d["account_id"])
        d["source_strategy_id"] = _s(d.get("source_strategy_id"))
        d["free_shares"] = int(d["shares"]) - int(d["encumbered_shares"])
        return d

    def upsert_stock_lot(
        self,
        account_slug: str,
        symbol: str,
        *,
        shares: Optional[int] = None,
        avg_cost_basis: Optional[Any] = None,
        add_shares: Optional[int] = None,
        add_price: Optional[Any] = None,
        encumbered_shares: Optional[int] = None,
        source_strategy_id: Optional[str] = None,
        mark_price: Optional[Any] = None,
    ) -> dict[str, Any]:
        """Create/update a stock lot, then refresh account stock valuation.

        Either set absolutes (``shares`` / ``avg_cost_basis``) or apply a trade with
        ``add_shares`` (negative = sell) and ``add_price`` (weighted-average cost on
        buys; basis unchanged on sells). Does NOT move cash - use
        ``update_account_balances`` for the cash leg of the trade.
        """
        sym = symbol.upper()
        acc = self._account_row(account_slug, lock=True)
        cur = self.get_stock_lot(account_slug, sym)
        cur_shares = int(cur["shares"]) if cur else 0
        cur_basis = _d(cur["avg_cost_basis"]) if cur else _ZERO
        new_shares, new_basis = cur_shares, cur_basis
        if shares is not None:
            new_shares = int(shares)
            if avg_cost_basis is not None:
                new_basis = _d(avg_cost_basis)
        if add_shares is not None:
            if add_shares > 0:
                if add_price is None:
                    raise ValueError("add_price required when buying shares")
                total = new_shares + add_shares
                new_basis = (new_basis * new_shares + _d(add_price) * add_shares) / total
                new_shares = total
            else:
                new_shares = max(0, new_shares + add_shares)
        if shares is None and add_shares is None and avg_cost_basis is not None:
            new_basis = _d(avg_cost_basis)
        if new_shares == 0:
            new_basis = _ZERO
        encumbered = (
            int(encumbered_shares) if encumbered_shares is not None
            else min(int(cur["encumbered_shares"]) if cur else 0, new_shares)
        )
        encumbered = max(0, min(encumbered, new_shares))
        self._exec(
            """
            INSERT INTO opt_stock_lots (account_id, account_slug, underlying_symbol, shares,
                avg_cost_basis, encumbered_shares, source_strategy_id, updated_at)
            VALUES (:aid, :slug, :sym, :shares, :basis, :enc, CAST(:src AS uuid), NOW())
            ON CONFLICT (account_slug, underlying_symbol) DO UPDATE SET
              shares = EXCLUDED.shares,
              avg_cost_basis = EXCLUDED.avg_cost_basis,
              encumbered_shares = EXCLUDED.encumbered_shares,
              source_strategy_id = COALESCE(EXCLUDED.source_strategy_id, opt_stock_lots.source_strategy_id),
              updated_at = NOW()
            """,
            {
                "aid": acc["id"],
                "slug": account_slug,
                "sym": sym,
                "shares": new_shares,
                "basis": _q4(new_basis),
                "enc": encumbered,
                "src": _s(source_strategy_id),
            },
        )
        self._refresh_account(account_slug, stock_marks={sym: mark_price} if mark_price is not None else None)
        self._maybe_commit()
        lot = self.get_stock_lot(account_slug, sym)
        assert lot is not None
        return lot

    # ----------------------------------------------------------- wheel state
    def get_wheel_state(self, account_slug: str, symbol: str) -> str:
        row = self._exec(
            "SELECT wheel_states ->> :sym FROM opt_accounts WHERE slug = :slug",
            {"slug": account_slug, "sym": symbol.upper()},
        ).first()
        return (row[0] if row and row[0] else WheelState.IDLE.value)

    def set_wheel_state(self, account_slug: str, symbol: str, state: str | WheelState) -> str:
        value = WheelState(_enum_value(state)).value
        res = self._exec(
            """
            UPDATE opt_accounts
            SET wheel_states = COALESCE(wheel_states, '{}'::jsonb) || jsonb_build_object(CAST(:sym AS text), CAST(:state AS text)),
                updated_at = NOW()
            WHERE slug = :slug
            """,
            {"slug": account_slug, "sym": symbol.upper(), "state": value},
        )
        if not res.rowcount:
            raise OptionsAccountNotFound(account_slug)
        self._maybe_commit()
        return value

    # --------------------------------------------------------------- journal
    @staticmethod
    def _journal_view(r: Mapping[str, Any]) -> OptionsJournalEventView:
        return OptionsJournalEventView(
            id=str(r["id"]),
            account_slug=r["account_slug"],
            strategy_group_id=_s(r["strategy_id"]),
            event_type=r["event_type"],
            underlying_symbol=r["underlying_symbol"],
            strategy_type=r["strategy_type"],
            cash_delta=_f(r["cash_delta"]),
            realized_pnl_delta=_f(r["realized_pnl_delta"]),
            collateral_delta=_f(r["collateral_delta"]),
            fee_usd=_f(r["fee_usd"]),
            description=r["description"],
            created_at=r["created_at"],
            metadata=_json_load(r["metadata"], {}) or {},
        )

    def record_journal_event(
        self,
        account_slug: str,
        event_type: str,
        *,
        strategy_id: Optional[str] = None,
        underlying_symbol: str = "",
        strategy_type: str = "",
        cash_delta: Any = 0,
        realized_pnl_delta: Any = 0,
        collateral_delta: Any = 0,
        fee_usd: Any = 0,
        description: str = "",
        metadata: Optional[Mapping[str, Any]] = None,
    ) -> OptionsJournalEventView:
        """Append an audit event (OPEN/CLOSE/ROLL/EXPIRE/ASSIGN/EXERCISE/STOCK_DELIVERY/FEE).

        Log-only: it does not change balances. OPEN/CLOSE/EXPIRE/ASSIGN/ROLL events for
        strategies are already written by the create/close methods.
        """
        acc = self._account_row(account_slug)
        event_id = self._insert_ledger(
            acc,
            event_type=event_type,
            strategy_id=strategy_id,
            underlying=underlying_symbol,
            strategy_type=strategy_type,
            cash_delta=cash_delta,
            realized_delta=realized_pnl_delta,
            collateral_delta=collateral_delta,
            fee=fee_usd,
            description=description,
            metadata=metadata,
        )
        row = self._exec(
            "SELECT * FROM opt_ledger_events WHERE id = CAST(:id AS uuid)", {"id": event_id}
        ).mappings().one()
        self._maybe_commit()
        return self._journal_view(row)

    def list_journal_events(self, account_slug: str, limit: int = 100) -> list[OptionsJournalEventView]:
        rows = (
            self._exec(
                """
                SELECT * FROM opt_ledger_events WHERE account_slug = :slug
                ORDER BY created_at DESC, id LIMIT :limit
                """,
                {"slug": account_slug, "limit": limit},
            )
            .mappings()
            .all()
        )
        return [self._journal_view(r) for r in rows]

    # ----------------------------------------------------------- AI decisions
    def record_ai_decision(self, decision: AIDecisionRecord) -> bool:
        """Insert a decision (idempotent on decision_id). Returns True if newly stored."""
        row = self._exec(
            """
            INSERT INTO opt_ai_decisions (
              decision_id, account_slug, mode, action, strategy_type, candidate_id,
              position_group_id, underlying_symbol, contracts_qty, confidence, decided_by,
              model_name, rationale_he, rationale_en, risk_guard_passed, risk_guard_reasons,
              market_context, created_at
            ) VALUES (
              :did, :slug, :mode, :action, :stype, :cid, :pgid, :sym, :qty, :conf, :by,
              :model, :he, :en, :passed, CAST(:reasons AS jsonb), CAST(:ctx AS jsonb), :ts
            )
            ON CONFLICT (decision_id) DO NOTHING RETURNING id
            """,
            {
                "did": decision.decision_id,
                "slug": decision.account_slug,
                "mode": _enum_value(decision.mode),
                "action": _enum_value(decision.action),
                "stype": _enum_value(decision.strategy_type) if decision.strategy_type else None,
                "cid": decision.candidate_id,
                "pgid": decision.position_group_id,
                "sym": decision.underlying_symbol,
                "qty": decision.contracts_qty,
                "conf": decision.confidence,
                "by": decision.decided_by,
                "model": decision.model_name,
                "he": decision.rationale_he,
                "en": decision.rationale_en,
                "passed": decision.risk_guard_passed,
                "reasons": _json(list(decision.risk_guard_reasons)),
                "ctx": _json(decision.market_context),
                "ts": decision.created_at,
            },
        ).first()
        self._maybe_commit()
        return row is not None

    def list_ai_decisions(
        self, account_slug: Optional[str] = None, limit: int = 50
    ) -> list[AIDecisionRecord]:
        rows = (
            self._exec(
                """
                SELECT * FROM opt_ai_decisions
                WHERE (CAST(:slug AS text) IS NULL OR account_slug = CAST(:slug AS text))
                ORDER BY created_at DESC, id LIMIT :limit
                """,
                {"slug": account_slug, "limit": limit},
            )
            .mappings()
            .all()
        )
        out: list[AIDecisionRecord] = []
        for r in rows:
            try:
                stype = StrategyType(r["strategy_type"]) if r["strategy_type"] else None
            except ValueError:
                stype = None
            out.append(
                AIDecisionRecord(
                    decision_id=r["decision_id"],
                    account_slug=r["account_slug"],
                    mode=DecisionMode(r["mode"]),
                    action=AIAction(r["action"]),
                    strategy_type=stype,
                    candidate_id=r["candidate_id"],
                    position_group_id=r["position_group_id"],
                    underlying_symbol=r["underlying_symbol"],
                    contracts_qty=int(r["contracts_qty"]),
                    confidence=_f(r["confidence"]),
                    decided_by=r["decided_by"],
                    model_name=r["model_name"],
                    rationale_he=r["rationale_he"],
                    rationale_en=r["rationale_en"],
                    risk_guard_passed=bool(r["risk_guard_passed"]),
                    risk_guard_reasons=list(_json_load(r["risk_guard_reasons"], [])),
                    market_context=_json_load(r["market_context"], {}) or {},
                    created_at=r["created_at"],
                )
            )
        return out

    # ----------------------------------------------------------- opportunities
    def save_opportunities(
        self,
        candidates: Iterable[StrategyCandidate],
        *,
        prune_older_than_minutes: Optional[int] = 240,
    ) -> int:
        """Upsert scanned candidates (keyed by candidate_id). Returns number saved."""
        params = [
            {
                "cid": c.candidate_id,
                "stype": _enum_value(c.strategy_type),
                "sym": c.underlying_symbol,
                "exp": c.expiration_date,
                "payload": _json(c.model_dump(mode="json")),
                "score": c.rules_score,
                "tier": str(_enum_value(c.feed_tier)),
                "ind": c.is_indicative,
            }
            for c in candidates
        ]
        if params:
            self.session.execute(
                text(
                    """
                    INSERT INTO opt_opportunities (candidate_id, strategy_type, underlying_symbol,
                        expiration_date, payload, rules_score, feed_tier, is_indicative, scanned_at)
                    VALUES (:cid, :stype, :sym, :exp, CAST(:payload AS jsonb), :score, :tier, :ind, NOW())
                    ON CONFLICT (candidate_id) DO UPDATE SET
                      strategy_type = EXCLUDED.strategy_type,
                      underlying_symbol = EXCLUDED.underlying_symbol,
                      expiration_date = EXCLUDED.expiration_date,
                      payload = EXCLUDED.payload,
                      rules_score = EXCLUDED.rules_score,
                      feed_tier = EXCLUDED.feed_tier,
                      is_indicative = EXCLUDED.is_indicative,
                      scanned_at = NOW()
                    """
                ),
                params,
            )
        if prune_older_than_minutes is not None:
            self._exec(
                "DELETE FROM opt_opportunities WHERE scanned_at < NOW() - make_interval(mins => :m)",
                {"m": prune_older_than_minutes},
            )
        self._maybe_commit()
        return len(params)

    def list_opportunities(self, limit: int = 30) -> list[StrategyCandidate]:
        rows = (
            self._exec(
                """
                SELECT payload FROM opt_opportunities
                WHERE expiration_date >= :today
                ORDER BY scanned_at DESC, rules_score DESC, candidate_id
                LIMIT :limit
                """,
                {"today": _today(), "limit": limit},
            )
            .mappings()
            .all()
        )
        out: list[StrategyCandidate] = []
        for r in rows:
            try:
                out.append(StrategyCandidate(**_json_load(r["payload"], {})))
            except Exception:
                continue
        return out

    # ------------------------------------------------------------ engine state
    def get_engine_state(self) -> OptionsEngineStatusView:
        self._exec("INSERT INTO opt_engine_state (id) VALUES (1) ON CONFLICT (id) DO NOTHING")
        r = self._exec(
            """
            SELECT s.*,
              (s.groq_calls_date = CURRENT_DATE) AS groq_today,
              EXISTS (SELECT 1 FROM opt_orders) AS has_orders
            FROM opt_engine_state s WHERE s.id = 1
            """
        ).mappings().one()
        activity = (
            self._exec(
                """
                SELECT
                  (SELECT opened_at FROM opt_strategies ORDER BY opened_at DESC NULLS LAST LIMIT 1)
                    AS last_sim_trade_at,
                  (SELECT account_slug FROM opt_strategies ORDER BY opened_at DESC NULLS LAST LIMIT 1)
                    AS last_sim_trade_account,
                  (SELECT strategy_type FROM opt_strategies ORDER BY opened_at DESC NULLS LAST LIMIT 1)
                    AS last_sim_trade_strategy,
                  (SELECT COUNT(*) FROM opt_strategies
                     WHERE opened_at > NOW() - INTERVAL '1 hour') AS sim_opens_last_hour,
                  (SELECT COUNT(*) FROM opt_ai_decisions
                     WHERE created_at > NOW() - INTERVAL '1 hour'
                       AND action = 'OPEN' AND risk_guard_passed = false) AS risk_blocks_last_hour,
                  (SELECT COUNT(*) FROM opt_opportunities
                     WHERE scanned_at > NOW() - INTERVAL '1 hour') AS opportunities_last_hour
                """
            )
            .mappings()
            .one()
        )
        self._maybe_commit()
        last_cycle_at = r["last_cycle_at"]
        last_error = r["last_error"]
        last_cycle_ok: bool | None
        if last_cycle_at is None:
            last_cycle_ok = None
        else:
            last_cycle_ok = last_error is None

        opens_1h = int(activity["sim_opens_last_hour"] or 0)
        risk_blocks_1h = int(activity["risk_blocks_last_hour"] or 0)
        opps_1h = int(activity["opportunities_last_hour"] or 0)
        idle_hint: str | None = None
        if bool(r["enabled"]) and not bool(r["running_cycle"]) and opens_1h == 0:
            if not bool(r["market_open"]):
                idle_hint = "השוק סגור — אין פתיחות סימולציה חדשות מחוץ לשעות המסחר"
            elif opps_1h == 0:
                idle_hint = "המחזור רץ, אך לא נמצאו מועמדים חדשים בסריקה האחרונה"
            elif risk_blocks_1h > 0:
                idle_hint = f"יש מועמדים, אך Risk Guard חסם {risk_blocks_1h} הצעות בשעה האחרונה"
            else:
                idle_hint = "המנוע פעיל; אין פתיחות סימולציה בשעה האחרונה"

        return OptionsEngineStatusView(
            enabled=bool(r["enabled"]),
            running_cycle=bool(r["running_cycle"]),
            mode=DecisionMode(r["mode"]),
            feed_tier=DataFeedTier(r["feed_tier"]),
            is_indicative=bool(r["is_indicative"]),
            is_delayed_trades=bool(r["is_delayed_trades"]),
            market_open=bool(r["market_open"]),
            data_freshness_seconds=r["data_freshness_seconds"],
            last_cycle_at=last_cycle_at,
            last_error=last_error,
            groq_available=bool(r["groq_available"]),
            groq_model=r["groq_model"],
            groq_calls_today=int(r["groq_calls_today"]) if r["groq_today"] else 0,
            readiness_technical=True,
            readiness_market_data=(
                last_cycle_at is not None
                and r["feed_tier"] not in (DataFeedTier.UNAVAILABLE.value, DataFeedTier.FIXTURE.value)
            ),
            readiness_forward_sim=(
                bool(r["has_orders"])
                and last_cycle_at is not None
                and r["feed_tier"] not in (DataFeedTier.UNAVAILABLE.value, DataFeedTier.FIXTURE.value)
            ),
            universe=[str(s) for s in _json_load(r["universe"], [])],
            last_cycle_ok=last_cycle_ok,
            last_sim_trade_at=activity["last_sim_trade_at"],
            last_sim_trade_account=activity["last_sim_trade_account"],
            last_sim_trade_strategy=activity["last_sim_trade_strategy"],
            sim_opens_last_hour=opens_1h,
            idle_hint=idle_hint,
        )

    def set_engine_state(self, patch: Mapping[str, Any]) -> OptionsEngineStatusView:
        """Patch engine_state. Unknown keys raise. Special key ``groq_calls_increment``
        atomically bumps today's Groq counter (auto-resets on a new day)."""
        patch = dict(patch)
        increment = int(patch.pop("groq_calls_increment", 0) or 0)
        unknown = set(patch) - _ENGINE_STATE_COLUMNS
        if unknown:
            raise ValueError(f"Unknown engine_state fields: {sorted(unknown)}")
        self._exec("INSERT INTO opt_engine_state (id) VALUES (1) ON CONFLICT (id) DO NOTHING")
        sets: list[str] = []
        params: dict[str, Any] = {}
        for col, value in patch.items():
            if col in _ENGINE_JSON_COLUMNS:
                sets.append(f"{col} = CAST(:{col} AS jsonb)")
                params[col] = _json(list(value))
            else:
                sets.append(f"{col} = :{col}")
                params[col] = _enum_value(value)
        if increment:
            sets.append(
                "groq_calls_today = (CASE WHEN groq_calls_date = CURRENT_DATE "
                "THEN groq_calls_today ELSE 0 END) + :inc"
            )
            sets.append("groq_calls_date = CURRENT_DATE")
            params["inc"] = increment
        sets.append("updated_at = NOW()")
        self._exec(f"UPDATE opt_engine_state SET {', '.join(sets)} WHERE id = 1", params)
        self._maybe_commit()
        return self.get_engine_state()

    # ---------------------------------------------------------------- compare
    def get_comparison_rows(self) -> list[OptionsStrategyComparisonRow]:
        out: list[OptionsStrategyComparisonRow] = []
        for acc in self.list_accounts(active_only=True):
            stats = self._strategy_stats(acc.slug).get(acc.slug) or {}
            out.append(
                OptionsStrategyComparisonRow(
                    cohort_key=acc.slug,
                    label=acc.name,
                    account_slug=acc.slug,
                    mode=acc.mode,
                    strategy_scope=acc.strategy_scope or "all",
                    starting_capital=acc.starting_capital,
                    equity=acc.equity,
                    return_pct=acc.return_pct,
                    realized_pnl=acc.realized_pnl,
                    unrealized_pnl=acc.unrealized_pnl,
                    premium_collected_gross=acc.premium_collected_gross,
                    premium_realized_net=acc.premium_realized_net,
                    open_liability_usd=_f(stats.get("open_liability")),
                    collateral_locked_usd=_f(stats.get("collateral_locked")),
                    max_drawdown_pct=acc.max_drawdown_pct,
                    win_rate_pct=acc.win_rate_pct,
                    profit_factor=acc.profit_factor,
                    open_count=acc.open_strategies_count,
                    closed_count=acc.closed_strategies_count,
                )
            )
        return out

    # ------------------------------------------------------------ expirations
    def get_expirations(self, account_slug: Optional[str] = None) -> list[OptionsExpirationItem]:
        rows = (
            self._exec(
                """
                SELECT * FROM opt_strategies
                WHERE status = 'open' AND expiration_date IS NOT NULL
                  AND (CAST(:slug AS text) IS NULL OR account_slug = CAST(:slug AS text))
                ORDER BY expiration_date, account_slug, underlying_symbol
                """,
                {"slug": account_slug},
            )
            .mappings()
            .all()
        )
        if not rows:
            return []
        symbols = sorted({r["underlying_symbol"] for r in rows})
        spots = {
            r["underlying_symbol"]: _d(r["underlying_price"])
            for r in self._exec(
                """
                SELECT DISTINCT ON (underlying_symbol) underlying_symbol, underlying_price
                FROM opt_chain_snapshots
                WHERE underlying_symbol = ANY(:syms) AND underlying_price IS NOT NULL
                ORDER BY underlying_symbol, snapshot_timestamp DESC
                """,
                {"syms": symbols},
            ).mappings()
        }
        legs = self._legs_for([str(r["id"]) for r in rows])
        today = _today()
        items: list[OptionsExpirationItem] = []
        for r in rows:
            strat_legs = [
                l for l in legs.get(str(r["id"]), [])
                if l["status"] == "open" and l["strike"] is not None
            ]
            spot = spots.get(r["underlying_symbol"])
            focus = [l for l in strat_legs if l["side"] == "short"] or strat_legs
            itm = False
            near = False
            if spot is not None and spot > 0:
                for l in focus:
                    strike = _d(l["strike"])
                    is_itm = spot > strike if l["option_right"] == "call" else spot < strike
                    itm = itm or is_itm
                    near = near or abs(spot - strike) / spot <= Decimal("0.005")
            itm_status = "ITM" if itm else ("ATM_PIN_RISK" if (near or r["pin_risk_warning"]) else "OTM")
            dte = (r["expiration_date"] - today).days
            if dte <= 1 or itm_status == "ITM":
                level = "urgent"
            elif dte <= 7 or itm_status == "ATM_PIN_RISK" or r["early_assignment_warning"]:
                level = "watch"
            else:
                level = "normal"
            items.append(
                OptionsExpirationItem(
                    strategy_group_id=str(r["id"]),
                    account_slug=r["account_slug"],
                    underlying_symbol=r["underlying_symbol"],
                    strategy_type=r["strategy_type"],
                    expiration_date=r["expiration_date"],
                    dte=dte,
                    strikes=sorted({float(l["strike"]) for l in strat_legs}),
                    underlying_price=float(spot) if spot is not None else None,
                    itm_status=itm_status,
                    collateral_locked_usd=_f(r["collateral_locked_usd"]),
                    unrealized_pnl=_f(r["unrealized_pnl"]),
                    warning_level=level,
                )
            )
        return items
