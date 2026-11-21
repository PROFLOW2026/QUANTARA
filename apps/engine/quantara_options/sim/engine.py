"""QUANTARA Options simulation engine: open / mark / close / roll / expire / assign.

Each public call loads a working copy of the account + its open strategies, mutates it in
memory with the pure ledger primitives, verifies accounting conservation, and persists the
whole result atomically through the repository. A rejected operation persists nothing.

Positions persist across days and weeks: there is no daily auto-flatten. The only automatic
closes are the configurable expiry-day pin-risk guard, stop/max-loss breaches, expiration,
and (early) assignment.
"""

from __future__ import annotations

import copy
import logging
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any, Iterable, Mapping, Optional, Protocol, Sequence, runtime_checkable

from quantara_options.api_schemas import OptionsExpirationItem, OptionsLegView, OptionsStrategyGroupView
from quantara_options.contracts import (
    ALL_9_STRATEGY_LABELS_HE,
    LegPlan,
    LegRole,
    OptionAccountSummary,
    OptionQuoteSnapshot,
    OptionRight,
    OptionSide,
    StrategyCandidate,
    StrategyStatus,
    StrategyType,
    WheelState,
)
from quantara_options.math_pricing import compute_payoff_curve
from quantara_options.sim.accounting import (
    CREDIT_STRATEGIES,
    AccountTotals,
    LegStatus,
    SimAccountState,
    SimJournalEvent,
    SimLeg,
    SimStrategy,
    book_option_close,
    book_option_open,
    buy_stock,
    charge_fee,
    check_conservation,
    compute_account_totals,
    current_drawdown_pct,
    encumber_shares,
    new_id,
    reserve_cash,
    resolve_strategy,
    sell_stock_from_lot,
    to_account_summary,
    unencumber_shares,
    update_drawdown,
)
from quantara_options.sim.fees_and_slippage import (
    DEFAULT_REG_FEES,
    DEFAULT_SLIPPAGE,
    ZERO,
    FillAction,
    FillUnavailableError,
    LegFill,
    QuoteView,
    RegulatoryFeeSchedule,
    SlippageConfig,
    apply_combo_improvement,
    compute_order_fees,
    money,
    price_option_fill,
    price_stock_fill,
    to_decimal,
)
from quantara_options.sim.lifecycle import (
    DEFAULT_IV,
    DEFAULT_RATE,
    PIN_GUARD_STRATEGIES,
    ExitPolicy,
    LifecycleContext,
    any_leg_itm,
    apply_mark,
    assign_short_call_physical,
    assign_short_put_physical,
    calendar_dte,
    credit_premium_to_lots,
    detect_gap,
    detect_pin_risk,
    detect_vol_shock,
    ensure_aware,
    evaluate_early_assignment,
    evaluate_exit_breach,
    expiration_due,
    market_date,
    mark_leg,
    minutes_to_expiry_close,
    model_price,
    net_settle_leg,
    resolve_at_expiration,
    set_wheel_state,
    wheel_state_after_resolution,
)
from quantara_options.sim.margin_and_collateral import (
    StructureError,
    check_open_rules,
    compute_collateral,
    structure_legs_from_plans,
)

logger = logging.getLogger(__name__)


@runtime_checkable
class OptionsSimRepository(Protocol):
    """Persistence port used by the engine (implemented by `quantara_options.db.repository`)."""

    def load_account_state(self, account_slug: str) -> Optional[SimAccountState]: ...

    def list_open_strategies(self, account_slug: str) -> list[SimStrategy]: ...

    def get_strategy(self, strategy_id: str) -> Optional[SimStrategy]: ...

    def save_simulation_state(
        self,
        account: SimAccountState,
        strategies: Sequence[SimStrategy],
        events: Sequence[SimJournalEvent],
    ) -> None:
        """Atomically upsert account (incl. stock lots & wheel states), strategies + legs, journal."""
        ...


class InMemoryOptionsRepository:
    """Reference `OptionsSimRepository` for tests, fixtures and offline runs."""

    def __init__(self) -> None:
        self.accounts: dict[str, SimAccountState] = {}
        self.strategies: dict[str, SimStrategy] = {}
        self.journal: list[SimJournalEvent] = []

    def add_account(self, account: SimAccountState) -> None:
        self.accounts[account.slug] = copy.deepcopy(account)

    def load_account_state(self, account_slug: str) -> Optional[SimAccountState]:
        acct = self.accounts.get(account_slug)
        return copy.deepcopy(acct) if acct is not None else None

    def list_open_strategies(self, account_slug: str) -> list[SimStrategy]:
        return [
            copy.deepcopy(s)
            for s in self.strategies.values()
            if s.account_slug == account_slug and s.status == StrategyStatus.OPEN
        ]

    def list_strategies(self, account_slug: str) -> list[SimStrategy]:
        return [copy.deepcopy(s) for s in self.strategies.values() if s.account_slug == account_slug]

    def get_strategy(self, strategy_id: str) -> Optional[SimStrategy]:
        s = self.strategies.get(strategy_id)
        return copy.deepcopy(s) if s is not None else None

    def save_simulation_state(
        self,
        account: SimAccountState,
        strategies: Sequence[SimStrategy],
        events: Sequence[SimJournalEvent],
    ) -> None:
        self.accounts[account.slug] = copy.deepcopy(account)
        for s in strategies:
            self.strategies[s.strategy_id] = copy.deepcopy(s)
        self.journal.extend(copy.deepcopy(list(events)))


@dataclass
class SimOperationResult:
    ok: bool
    action: str
    account_slug: str
    strategy: Optional[SimStrategy] = None
    new_strategy: Optional[SimStrategy] = None
    affected_strategies: list[SimStrategy] = field(default_factory=list)
    events: list[SimJournalEvent] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    breaches: list[dict[str, Any]] = field(default_factory=list)
    totals: Optional[AccountTotals] = None
    summary: Optional[OptionAccountSummary] = None


@dataclass
class _Session:
    account: SimAccountState
    strategies: dict[str, SimStrategy]
    now: datetime
    events: list[SimJournalEvent] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    touched: set[str] = field(default_factory=set)

    def open_strategies(self) -> list[SimStrategy]:
        return [s for s in self.strategies.values() if s.is_open]


QuotesMap = Mapping[str, OptionQuoteSnapshot]
SpotMap = Mapping[str, Any]


class OptionsSimulationEngine:
    def __init__(
        self,
        repo: OptionsSimRepository,
        *,
        slippage: SlippageConfig = DEFAULT_SLIPPAGE,
        reg_fees: RegulatoryFeeSchedule = DEFAULT_REG_FEES,
        exit_policy: ExitPolicy = ExitPolicy(),
        mark_policy: str = "mid",  # "mid" | "liquidation" (long@bid, short@ask)
        auto_close_on_breach: bool = True,
        default_iv: float = DEFAULT_IV,
        rate: float = DEFAULT_RATE,
        gap_threshold_pct: Decimal = Decimal("0.03"),
        vol_shock_rel_threshold: float = 0.5,
        strict_conservation: bool = False,
    ) -> None:
        self.repo = repo
        self.slippage = slippage
        self.reg_fees = reg_fees
        self.exit_policy = exit_policy
        self.mark_policy = mark_policy
        self.auto_close_on_breach = auto_close_on_breach
        self.default_iv = default_iv
        self.rate = rate
        self.gap_threshold_pct = gap_threshold_pct
        self.vol_shock_rel_threshold = vol_shock_rel_threshold
        self.strict_conservation = strict_conservation

    # ------------------------------------------------------------------ session

    def _load(self, account_slug: str, now: Optional[datetime]) -> _Session:
        account = self.repo.load_account_state(account_slug)
        if account is None:
            raise KeyError(f"options account not found: {account_slug}")
        opens = self.repo.list_open_strategies(account_slug)
        return _Session(account=account, strategies={s.strategy_id: s for s in opens}, now=ensure_aware(now))

    def _commit(self, s: _Session) -> list[str]:
        problems = check_conservation(s.account, s.open_strategies())
        if problems:
            msg = "; ".join(problems)
            if self.strict_conservation:
                raise RuntimeError(f"options accounting conservation violated: {msg}")
            logger.error("options accounting conservation violated for %s: %s", s.account.slug, msg)
            s.warnings.extend(f"conservation:{p}" for p in problems)
        s.account.updated_at = s.now
        touched = [s.strategies[i] for i in s.touched if i in s.strategies]
        self.repo.save_simulation_state(s.account, touched, s.events)
        return problems

    def _result(self, s: _Session, action: str, ok: bool = True, **kw: Any) -> SimOperationResult:
        if ok:
            account, opens = s.account, s.open_strategies()
        else:
            # Rejected operations persist nothing: report the stored (untouched) books.
            account = self.repo.load_account_state(s.account.slug) or s.account
            opens = self.repo.list_open_strategies(s.account.slug)
        return SimOperationResult(
            ok=ok,
            action=action,
            account_slug=account.slug,
            events=list(s.events) if ok else [],
            warnings=list(dict.fromkeys(s.warnings + kw.pop("warnings", []))),
            totals=compute_account_totals(account, opens),
            summary=to_account_summary(account, opens),
            **kw,
        )

    def _ctx(self, s: _Session) -> LifecycleContext:
        return LifecycleContext(account=s.account, profile=s.account.rule_profile, now=s.now, events=s.events, schedule=self.reg_fees)

    @staticmethod
    def _spot(s: _Session, symbol: str, spots: Optional[SpotMap], quotes: Optional[QuotesMap] = None) -> Optional[Decimal]:
        if spots and spots.get(symbol) is not None:
            px = to_decimal(spots[symbol])
            if px is not None and px > ZERO:
                return px
        for q in (quotes or {}).values():
            if q.contract.underlying_symbol == symbol and q.underlying_price:
                return to_decimal(q.underlying_price)
        return s.account.last_spot_by_symbol.get(symbol)

    def _emit(self, s: _Session, strategy: Optional[SimStrategy], event_type: str, description: str, **kw: Any) -> None:
        self._ctx(s).emit(strategy, event_type, description, **kw)

    # ------------------------------------------------------------------ open

    def open_strategy(
        self,
        account_slug: str,
        candidate: StrategyCandidate,
        ai_rationale: Optional[str] = None,
        now: Optional[datetime] = None,
        *,
        quotes_by_occ: Optional[QuotesMap] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> SimOperationResult:
        s = self._load(account_slug, now)
        strategy, reasons, warnings = self._open_in_session(s, candidate, ai_rationale, quotes_by_occ, metadata)
        if strategy is None:
            return self._result(s, "open", ok=False, reasons=reasons, warnings=warnings)
        self._commit(s)
        return self._result(s, "open", strategy=strategy, warnings=warnings)

    def _open_in_session(
        self,
        s: _Session,
        cand: StrategyCandidate,
        ai_rationale: Optional[str],
        quotes: Optional[QuotesMap],
        metadata: Optional[dict[str, Any]],
    ) -> tuple[Optional[SimStrategy], list[str], list[str]]:
        acct = s.account
        profile = acct.rule_profile
        reasons: list[str] = []
        warnings: list[str] = list(cand.warnings)
        qty = int(cand.contracts_qty)
        symbol = cand.underlying_symbol
        if not acct.is_active:
            return None, ["account inactive"], warnings
        if qty < 1 or not cand.legs:
            return None, ["candidate needs contracts_qty >= 1 and at least one leg"], warnings
        option_plans = [l for l in cand.legs if l.leg_role != LegRole.UNDERLYING_STOCK]
        stock_plans = [l for l in cand.legs if l.leg_role == LegRole.UNDERLYING_STOCK]
        for leg in option_plans:
            if not leg.occ_symbol or leg.right is None or leg.strike is None or leg.expiration_date is None:
                reasons.append(f"option leg {leg.leg_role.value} missing occ/right/strike/expiration")
            elif leg.underlying_symbol != symbol:
                reasons.append(f"leg underlying {leg.underlying_symbol} != {symbol}")
            elif int(leg.multiplier or 0) <= 0:
                reasons.append(f"leg {leg.occ_symbol} has invalid multiplier")
            elif expiration_due(leg.expiration_date, s.now):
                reasons.append(f"leg {leg.occ_symbol} already expired")
        if reasons:
            return None, reasons, warnings

        spot = self._spot(s, symbol, {symbol: cand.underlying_price}, quotes)
        if spot is None or spot <= ZERO:
            return None, ["no underlying price"], warnings

        # 1) Honest fills: buys at Ask, sells at Bid (+ slippage when wide/illiquid/stale).
        fills: list[LegFill] = []
        for plan in option_plans:
            snap = (quotes or {}).get(plan.occ_symbol or "")
            qv = QuoteView.from_snapshot(snap) if snap is not None else QuoteView.from_leg_plan(plan)
            action = FillAction.BUY if plan.side == OptionSide.LONG else FillAction.SELL
            try:
                fill = price_option_fill(
                    action, qv,
                    quantity=qty * max(1, int(plan.ratio or 1)),
                    multiplier=int(plan.multiplier),
                    profile=profile, config=self.slippage, occ_symbol=plan.occ_symbol,
                )
            except FillUnavailableError as exc:
                return None, [f"unfillable: {exc}"], warnings
            if action == FillAction.BUY and fill.price <= ZERO:
                return None, [f"invalid ask for {plan.occ_symbol}"], warnings
            if action == FillAction.SELL and fill.price <= ZERO:
                return None, [f"no bid to sell {plan.occ_symbol}"], warnings
            fills.append(fill)
        combo = apply_combo_improvement(fills, self.slippage)
        net_option = combo.option_net_cash_flow_usd

        st = cand.strategy_type
        if st in CREDIT_STRATEGIES and net_option <= ZERO:
            return None, [f"{st.value} yields no net credit at executable prices ({net_option})"], warnings
        if st not in CREDIT_STRATEGIES and net_option >= ZERO:
            return None, [f"{st.value} should be a debit at executable prices ({net_option})"], warnings

        # 2) Collateral / structure.
        try:
            req = compute_collateral(
                st,
                structure_legs_from_plans(option_plans, qty),
                net_option_cash_flow=net_option,
                stock_reference_price=spot,
            )
        except StructureError as exc:
            return None, [f"invalid structure: {exc}"], warnings

        stock_fill: Optional[LegFill] = None
        have_shares = acct.unencumbered_shares(symbol)
        if req.shares_required:
            shortfall = max(0, req.shares_required - have_shares)
            if shortfall > 0 and stock_plans:
                stock_fill = price_stock_fill(FillAction.BUY, spot=spot, shares=shortfall, config=self.slippage)
        stock_cost = stock_fill.gross_usd if stock_fill else ZERO

        all_fills = fills + ([stock_fill] if stock_fill else [])
        fees = compute_order_fees(all_fills, profile, self.reg_fees)
        cash_needed = money(req.cash_reserved - net_option + fees.total + stock_cost)

        totals = compute_account_totals(acct, s.open_strategies())
        day_pnl_pct = ZERO
        if acct.day_start_equity and acct.day_start_equity > ZERO and acct.day_start_date == market_date(s.now):
            day_pnl_pct = money((totals.equity - acct.day_start_equity) / acct.day_start_equity * 100)
        rules = check_open_rules(
            profile,
            strategy_type=st,
            requirement=req,
            cash_needed_usd=cash_needed,
            free_cash=totals.free_cash,
            equity=totals.equity,
            cash_reserved_after=money(acct.cash_reserved + req.cash_reserved),
            open_strategies_count=totals.open_strategies_count,
            unencumbered_shares=have_shares,
            shares_to_buy=stock_fill.quantity if stock_fill else 0,
            day_trade_dates=acct.day_trade_dates,
            today=market_date(s.now),
            current_drawdown_pct=current_drawdown_pct(acct, totals.equity),
            day_pnl_pct=day_pnl_pct,
        )
        warnings.extend(rules.warnings)
        if not rules.passed:
            return None, rules.reasons, warnings

        for f in fills:
            warnings.extend(f"{f.occ_symbol}:{w}" for w in f.warnings)
        if cand.is_indicative:
            warnings.append("indicative_feed_quotes")
        cand_coll = to_decimal(cand.collateral_required_usd) or ZERO
        if abs(cand_coll - req.cash_reserved) > Decimal("1") and not req.shares_required:
            warnings.append(f"candidate_collateral_{cand_coll}_differs_engine_{req.cash_reserved}")

        # 3) Apply to the ledger.
        sid = new_id("osg")
        legs: list[SimLeg] = []
        for plan, fill in zip(option_plans, fills):
            snap = (quotes or {}).get(plan.occ_symbol or "")
            leg = SimLeg(
                leg_id=new_id("olg"),
                underlying_symbol=symbol,
                leg_role=plan.leg_role,
                side=plan.side,
                quantity=fill.quantity,
                multiplier=fill.multiplier,
                entry_price=fill.price,
                occ_symbol=plan.occ_symbol,
                right=plan.right,
                strike=to_decimal(plan.strike),
                expiration_date=plan.expiration_date,
                exercise_style=plan.exercise_style,
                implied_volatility=plan.implied_volatility,
                entry_implied_volatility=(snap.implied_volatility if snap and snap.implied_volatility else plan.implied_volatility),
                greeks=plan.greeks,
            )
            if snap is not None:
                apply_mark(leg, mark_leg(leg, snap, spot, s.now, policy=self.mark_policy, default_iv=self.default_iv, rate=self.rate))
            else:
                leg.bid, leg.ask = to_decimal(plan.bid), to_decimal(plan.ask)
                if self.mark_policy == "liquidation":
                    leg.current_mark = (leg.bid if leg.side == OptionSide.LONG else leg.ask) or fill.mid or fill.price
                else:
                    leg.current_mark = fill.mid if fill.mid is not None else fill.price
            legs.append(leg)

        strategy = SimStrategy(
            strategy_id=sid,
            account_slug=acct.slug,
            strategy_type=st,
            underlying_symbol=symbol,
            contracts_qty=qty,
            expiration_date=cand.expiration_date,
            is_credit=net_option > ZERO,
            legs=legs,
            mode=acct.mode,
            net_entry_cash_flow=net_option,
            margin_requirement=req.margin_requirement,
            max_profit_usd=req.max_profit_usd,
            max_loss_usd=req.max_loss_usd,
            opened_at=s.now,
            ai_rationale=ai_rationale or cand.rationale_summary or None,
            candidate_id=cand.candidate_id,
            metadata={
                **(metadata or {}),
                "break_evens": [str(b) for b in req.break_evens],
                "requirement_notes": req.notes,
                "feed_tier": cand.feed_tier.value,
                "entry_spot": str(spot),
            },
        )
        for leg in legs:
            book_option_open(acct, leg)

        if stock_fill is not None:
            lot = buy_stock(
                acct, symbol=symbol, shares=stock_fill.quantity, price=stock_fill.price,
                source="buy_write", strategy_id=sid, now=s.now,
            )
            lot.last_price = spot
            strategy.legs.append(
                SimLeg(
                    leg_id=new_id("olg"), underlying_symbol=symbol, leg_role=LegRole.UNDERLYING_STOCK,
                    side=OptionSide.LONG, quantity=stock_fill.quantity, multiplier=1,
                    entry_price=stock_fill.price, current_mark=spot, lot_id=lot.lot_id,
                )
            )
            strategy.metadata["buy_write_lot_ids"] = [lot.lot_id]

        option_fee = compute_order_fees(fills, profile, self.reg_fees).total
        charge_fee(acct, strategy, option_fee)
        charge_fee(acct, strategy, fees.total - option_fee, stock=True)
        reserve_cash(acct, strategy, req.cash_reserved)
        if req.shares_required:
            locked = encumber_shares(acct, strategy, symbol, req.shares_required)
            if locked < req.shares_required:
                return None, [f"could only encumber {locked}/{req.shares_required} shares"], warnings
        if strategy.is_credit:
            acct.premium_collected_gross = money(acct.premium_collected_gross + net_option)

        if st in (StrategyType.CASH_SECURED_PUT, StrategyType.WHEEL) and any(l.leg_role == LegRole.SHORT_PUT for l in legs):
            set_wheel_state(acct, strategy, WheelState.SHORT_PUT)
        elif st in (StrategyType.COVERED_CALL, StrategyType.WHEEL):
            set_wheel_state(acct, strategy, WheelState.SHORT_CALL)
        acct.last_spot_by_symbol[symbol] = spot

        s.strategies[sid] = strategy
        s.touched.add(sid)
        self._emit(
            s, strategy, "OPEN",
            f"Opened {ALL_9_STRATEGY_LABELS_HE.get(st.value, st.value)} x{qty} {symbol} "
            f"({'credit' if strategy.is_credit else 'debit'} {abs(net_option)})",
            cash_delta=net_option - fees.total - stock_cost,
            realized_pnl_delta=-fees.total,
            collateral_delta=req.cash_reserved,
            fee_usd=fees.total,
            metadata={
                "candidate_id": cand.candidate_id,
                "fills": [
                    {"occ": f.occ_symbol, "action": f.action.value, "qty": f.quantity, "multiplier": f.multiplier,
                     "price": str(f.price), "natural": str(f.natural_price), "mid": str(f.mid),
                     "slippage_per_share": str(money(f.slippage_per_share)), "estimated": f.estimated}
                    for f in all_fills
                ],
                "fees": fees.as_dict(),
                "combo_improvement_usd": str(combo.combo_improvement_usd),
                "shares_encumbered": req.shares_required,
                "max_loss_usd": str(req.max_loss_usd),
            },
        )
        return strategy, [], warnings

    # ------------------------------------------------------------------ close

    def _leg_quote(
        self, s: _Session, leg: SimLeg, quotes: Optional[QuotesMap], spot: Optional[Decimal], allow_estimated: bool
    ) -> tuple[Optional[QuoteView], Optional[Decimal], bool]:
        snap = (quotes or {}).get(leg.occ_symbol or "")
        if snap is not None:
            qv = QuoteView.from_snapshot(snap)
            if qv.two_sided():
                return qv, None, False
        else:
            qv = None
        if not allow_estimated:
            return qv, None, False
        fallback = model_price(leg, spot=spot, now=s.now, iv=leg.implied_volatility or leg.entry_implied_volatility or self.default_iv, rate=self.rate) if spot else None
        return (qv or QuoteView(source="estimated")), fallback, True

    def _close_legs(
        self,
        s: _Session,
        strategy: SimStrategy,
        legs: Iterable[SimLeg],
        quotes: Optional[QuotesMap],
        spot: Optional[Decimal],
        *,
        allow_estimated: bool,
    ) -> tuple[bool, list[str], Decimal, Decimal, list[LegFill]]:
        """Price all closing fills first (all-or-nothing), then book them. Returns (ok, reasons, cash, fees, fills)."""
        legs = [l for l in legs if l.is_open and l.is_option]
        priced: list[tuple[SimLeg, LegFill]] = []
        for leg in legs:
            qv, fallback, est = self._leg_quote(s, leg, quotes, spot, allow_estimated)
            if qv is None and not est:
                return False, [f"no executable quote for {leg.occ_symbol}"], ZERO, ZERO, []
            action = FillAction.SELL if leg.side == OptionSide.LONG else FillAction.BUY
            try:
                fill = price_option_fill(
                    action, qv or QuoteView(), quantity=leg.quantity, multiplier=leg.multiplier,
                    profile=s.account.rule_profile, config=self.slippage, fallback_price=fallback,
                    allow_estimated=est, occ_symbol=leg.occ_symbol,
                )
            except FillUnavailableError as exc:
                return False, [f"unfillable: {exc}"], ZERO, ZERO, []
            priced.append((leg, fill))
        combo = apply_combo_improvement([f for _, f in priced], self.slippage)
        cash_total = ZERO
        for leg, fill in priced:
            cash, _ = book_option_close(s.account, strategy, leg, fill.price, status=LegStatus.CLOSED, cash_settled=True)
            cash_total += cash
        fees = compute_order_fees(combo.legs, s.account.rule_profile, self.reg_fees).total
        charge_fee(s.account, strategy, fees)
        return True, [], money(cash_total), fees, combo.legs

    def _close_in_session(
        self,
        s: _Session,
        strategy: SimStrategy,
        quotes: Optional[QuotesMap],
        spots: Optional[SpotMap],
        *,
        reason: str,
        status: StrategyStatus = StrategyStatus.CLOSED,
        allow_estimated: bool = False,
        close_stock: Optional[bool] = None,
    ) -> tuple[bool, list[str]]:
        acct = s.account
        spot = self._spot(s, strategy.underlying_symbol, spots, quotes)
        realized_before = strategy.realized_pnl
        cash_before = acct.cash_balance
        ok, reasons, _, fees, fills = self._close_legs(
            s, strategy, strategy.open_option_legs, quotes, spot, allow_estimated=allow_estimated
        )
        if not ok:
            return False, reasons

        bw_lots = list(strategy.metadata.get("buy_write_lot_ids") or [])
        sell_stock = bool(bw_lots) if close_stock is None else close_stock
        stock_leg = next((l for l in strategy.legs if l.leg_role == LegRole.UNDERLYING_STOCK and l.is_open), None)
        if sell_stock and bw_lots:
            if spot is None:
                return False, ["no spot to sell buy-write shares"]
            unencumber_shares(acct, strategy)
            for lot_id in bw_lots:
                lot = acct.find_lot(lot_id)
                if lot is None or lot.shares <= 0:
                    continue
                sf = price_stock_fill(FillAction.SELL, spot=spot, shares=lot.unencumbered_shares, config=self.slippage)
                sell_stock_from_lot(acct, lot, shares=sf.quantity, price=sf.price, strategy=strategy)
                charge_fee(acct, strategy, compute_order_fees([sf], acct.rule_profile, self.reg_fees).total, stock=True)
                if stock_leg is not None:
                    stock_leg.status, stock_leg.exit_price, stock_leg.current_mark = LegStatus.CLOSED, sf.price, sf.price
        else:
            credit_premium_to_lots(acct, strategy)
            if stock_leg is not None:
                stock_leg.status = LegStatus.CLOSED  # shares remain in the account's stock lots

        resolve_strategy(acct, strategy, status=status, reason=reason, now=s.now)
        wheel_state_after_resolution(acct, strategy)
        if market_date(strategy.opened_at) == market_date(s.now):
            acct.day_trade_dates.append(market_date(s.now))
        s.touched.add(strategy.strategy_id)
        self._emit(
            s, strategy, "CLOSE",
            f"Closed {strategy.strategy_type.value} {strategy.underlying_symbol} ({reason})",
            cash_delta=acct.cash_balance - cash_before,
            realized_pnl_delta=strategy.realized_pnl - realized_before,
            collateral_delta=ZERO,
            fee_usd=fees,
            metadata={
                "reason": reason,
                "fills": [{"occ": f.occ_symbol, "action": f.action.value, "price": str(f.price), "estimated": f.estimated} for f in fills],
                "strategy_realized_pnl": str(strategy.realized_pnl),
            },
        )
        return True, []

    def close_strategy(
        self,
        strategy_id: str,
        quotes_by_occ: Optional[QuotesMap],
        spot_by_symbol: Optional[SpotMap],
        reason: str = "manual_close",
        now: Optional[datetime] = None,
        *,
        allow_estimated_fills: bool = False,
        close_stock: Optional[bool] = None,
    ) -> SimOperationResult:
        found = self.repo.get_strategy(strategy_id)
        if found is None:
            return SimOperationResult(ok=False, action="close", account_slug="", reasons=["strategy not found"])
        s = self._load(found.account_slug, now)
        strategy = s.strategies.get(strategy_id)
        if strategy is None or not strategy.is_open:
            return self._result(s, "close", ok=False, reasons=[f"strategy {strategy_id} is not open"])
        ok, reasons = self._close_in_session(
            s, strategy, quotes_by_occ, spot_by_symbol, reason=reason,
            allow_estimated=allow_estimated_fills, close_stock=close_stock,
        )
        if not ok:
            return self._result(s, "close", ok=False, reasons=reasons)
        self._commit(s)
        return self._result(s, "close", strategy=strategy)

    # ------------------------------------------------------------------ roll

    def roll_strategy(
        self,
        strategy_id: str,
        new_candidate: StrategyCandidate,
        quotes_by_occ: Optional[QuotesMap],
        spot_by_symbol: Optional[SpotMap],
        reason: str = "roll",
        now: Optional[datetime] = None,
    ) -> SimOperationResult:
        """Close the old structure and open the new one atomically (both or neither)."""
        found = self.repo.get_strategy(strategy_id)
        if found is None:
            return SimOperationResult(ok=False, action="roll", account_slug="", reasons=["strategy not found"])
        s = self._load(found.account_slug, now)
        old = s.strategies.get(strategy_id)
        if old is None or not old.is_open:
            return self._result(s, "roll", ok=False, reasons=[f"strategy {strategy_id} is not open"])
        if new_candidate.underlying_symbol != old.underlying_symbol:
            return self._result(s, "roll", ok=False, reasons=["roll must stay on the same underlying"])

        cash_before = s.account.cash_balance
        ok, reasons = self._close_in_session(
            s, old, quotes_by_occ, spot_by_symbol, reason=reason, status=StrategyStatus.ROLLED, close_stock=False
        )
        if not ok:
            return self._result(s, "roll", ok=False, reasons=[f"roll close leg failed: {r}" for r in reasons])
        new, open_reasons, warnings = self._open_in_session(
            s, new_candidate, old.ai_rationale, quotes_by_occ,
            {"rolled_from": old.strategy_id, "roll_reason": reason},
        )
        if new is None:
            return self._result(s, "roll", ok=False, reasons=[f"roll open leg failed: {r}" for r in open_reasons], warnings=warnings)
        old.metadata["rolled_to"] = new.strategy_id
        if old.strategy_type == StrategyType.WHEEL or old.underlying_symbol in s.account.wheel_states:
            new.wheel_state = s.account.wheel_states.get(new.underlying_symbol)
        self._emit(
            s, new, "ROLL",
            f"Rolled {old.strategy_id} -> {new.strategy_id}",
            cash_delta=s.account.cash_balance - cash_before,
            realized_pnl_delta=ZERO,
            metadata={
                "rolled_from": old.strategy_id,
                "old_realized_pnl": str(old.realized_pnl),
                "new_net_entry_cash_flow": str(new.net_entry_cash_flow),
                "net_roll_cash_flow": str(money(s.account.cash_balance - cash_before)),
            },
        )
        self._commit(s)
        return self._result(s, "roll", strategy=old, new_strategy=new, warnings=warnings)

    # ------------------------------------------------------------------ mark

    def _mark_session(self, s: _Session, quotes: Optional[QuotesMap], spots: Optional[SpotMap]) -> list[dict[str, Any]]:
        acct = s.account
        symbols = {st.underlying_symbol for st in s.open_strategies()} | {l.symbol for l in acct.stock_lots}
        resolved: dict[str, Optional[Decimal]] = {}
        for sym in symbols | set((spots or {}).keys()):
            px = self._spot(s, sym, spots, quotes)
            resolved[sym] = px
            fresh = to_decimal((spots or {}).get(sym)) if spots and spots.get(sym) is not None else None
            if fresh is not None and fresh > ZERO:
                gap = detect_gap(acct.last_spot_by_symbol.get(sym), fresh, self.gap_threshold_pct)
                if gap is not None:
                    self._emit(s, None, "WARNING", f"Gap {gap}% on {sym}", symbol=sym, metadata={"gap_pct": str(gap)})
                    s.warnings.append(f"gap:{sym}:{gap}%")
                acct.last_spot_by_symbol[sym] = fresh
        for lot in acct.stock_lots:
            if resolved.get(lot.symbol) is not None:
                lot.last_price = resolved[lot.symbol]

        breaches: list[dict[str, Any]] = []
        for st in s.open_strategies():
            spot = resolved.get(st.underlying_symbol)
            for leg in st.legs:
                if not leg.is_open:
                    continue
                if not leg.is_option:
                    if spot is not None:
                        leg.current_mark = spot
                    continue
                m = mark_leg(
                    leg, (quotes or {}).get(leg.occ_symbol or ""), spot, s.now,
                    policy=self.mark_policy, default_iv=self.default_iv, rate=self.rate,
                )
                apply_mark(leg, m)
                if m.estimated:
                    s.warnings.append(f"estimated_mark:{leg.occ_symbol}")
                if detect_vol_shock(leg.entry_implied_volatility, leg.implied_volatility, self.vol_shock_rel_threshold):
                    s.warnings.append(f"vol_shock:{leg.occ_symbol}")
            st.pin_risk_warning = detect_pin_risk(st, spot, s.now)
            st.early_assignment_warning = any(
                evaluate_early_assignment(l, spot=spot, now=s.now) is not None
                for l in st.open_option_legs if l.side == OptionSide.SHORT
            )
            breach, info = evaluate_exit_breach(st, self.exit_policy)
            st.exit_breach = breach
            if breach:
                breaches.append({"strategy_id": st.strategy_id, "breach": breach, "estimated_mark": st.mark_is_estimated(), **info})
            s.touched.add(st.strategy_id)

        totals = compute_account_totals(acct, s.open_strategies())
        update_drawdown(acct, totals.equity)
        today = market_date(s.now)
        if acct.day_start_date != today:
            acct.day_start_date = today
            acct.day_start_equity = totals.equity
        return breaches

    def mark_to_market(
        self,
        account_slug: str,
        quotes_by_occ: Optional[QuotesMap],
        spot_by_symbol: Optional[SpotMap],
        now: Optional[datetime] = None,
        *,
        auto_close_breaches: Optional[bool] = None,
    ) -> SimOperationResult:
        s = self._load(account_slug, now)
        breaches = self._mark_session(s, quotes_by_occ, spot_by_symbol)
        closed: list[SimStrategy] = []
        if self.auto_close_on_breach if auto_close_breaches is None else auto_close_breaches:
            for b in breaches:
                st = s.strategies.get(b["strategy_id"])
                if st is None or not st.is_open:
                    continue
                ok, reasons = self._close_in_session(s, st, quotes_by_occ, spot_by_symbol, reason=b["breach"])
                if ok:
                    closed.append(st)
                    b["closed"] = True
                else:
                    b["closed"] = False
                    s.warnings.append(f"breach_pending:{st.strategy_id}:{';'.join(reasons)}")
        self._commit(s)
        return self._result(s, "mark_to_market", affected_strategies=closed, breaches=breaches)

    # ------------------------------------------------------------------ expiration & assignment

    def process_expirations_and_assignments(
        self,
        account_slug: str,
        quotes_by_occ: Optional[QuotesMap],
        spot_by_symbol: Optional[SpotMap],
        now: Optional[datetime] = None,
        ex_div_symbols: Optional[Mapping[str, Any] | Iterable[str]] = None,
    ) -> SimOperationResult:
        """Pin-risk guard, expiration sweep, and early assignment. `ex_div_symbols`: symbol -> dividend/share (or bare symbols)."""
        s = self._load(account_slug, now)
        self._mark_session(s, quotes_by_occ, spot_by_symbol)
        if ex_div_symbols is None:
            ex_div: dict[str, Optional[Decimal]] = {}
        elif isinstance(ex_div_symbols, Mapping):
            ex_div = {k: to_decimal(v) for k, v in ex_div_symbols.items()}
        else:
            ex_div = {k: None for k in ex_div_symbols}

        profile = s.account.rule_profile
        affected: list[SimStrategy] = []
        for st in list(s.open_strategies()):
            spot = self._spot(s, st.underlying_symbol, spot_by_symbol, quotes_by_occ)
            if spot is None:
                s.warnings.append(f"no_spot_skip_lifecycle:{st.strategy_id}")
                continue

            if expiration_due(st.expiration_date, s.now):
                self._expire(s, st, spot)
                affected.append(st)
                continue

            guard = int(profile.auto_close_itm_before_expiry_minutes or 0)
            mins = minutes_to_expiry_close(st.expiration_date, s.now)
            if (
                guard > 0
                and st.strategy_type in PIN_GUARD_STRATEGIES
                and market_date(s.now) == st.expiration_date
                and 0 < mins <= guard
                and (any_leg_itm(st, spot) or detect_pin_risk(st, spot, s.now))
            ):
                ok, reasons = self._close_in_session(
                    s, st, quotes_by_occ, spot_by_symbol, reason="auto_close_itm_before_expiry", allow_estimated=True
                )
                if ok:
                    affected.append(st)
                else:
                    s.warnings.append(f"pin_guard_failed:{st.strategy_id}:{';'.join(reasons)}")
                continue

            ex_today = st.underlying_symbol in ex_div
            for leg in [l for l in st.open_option_legs if l.side == OptionSide.SHORT]:
                sig = evaluate_early_assignment(
                    leg, spot=spot, now=s.now, ex_div_today=ex_today, dividend=ex_div.get(st.underlying_symbol)
                )
                if sig is not None and sig.execute:
                    self._early_assign(s, st, leg, spot, sig.reason, quotes_by_occ, spot_by_symbol)
                    affected.append(st)
                    break

        self._commit(s)
        return self._result(s, "expirations_and_assignments", affected_strategies=affected)

    def _expire(self, s: _Session, st: SimStrategy, spot: Decimal) -> None:
        ctx = self._ctx(s)
        status = resolve_at_expiration(ctx, st, spot)
        self._finish_lifecycle(s, st, status, "assignment_at_expiration" if status == StrategyStatus.ASSIGNED else "expiration")

    def _early_assign(
        self,
        s: _Session,
        st: SimStrategy,
        leg: SimLeg,
        spot: Decimal,
        why: str,
        quotes: Optional[QuotesMap],
        spots: Optional[SpotMap],
    ) -> None:
        ctx = self._ctx(s)
        if leg.right == OptionRight.PUT and st.strategy_type in (StrategyType.CASH_SECURED_PUT, StrategyType.WHEEL):
            assign_short_put_physical(ctx, st, leg, spot)
        elif leg.right == OptionRight.CALL and st.strategy_type in (StrategyType.COVERED_CALL, StrategyType.WHEEL):
            assign_short_call_physical(ctx, st, leg, spot)
        else:
            net_settle_leg(ctx, st, leg, spot, event_type="ASSIGN")
            remaining = st.open_option_legs
            if remaining:
                ok, reasons, *_ = self._close_legs(s, st, remaining, quotes, spot, allow_estimated=True)
                if not ok:
                    s.warnings.append(f"early_assign_leg_close_failed:{st.strategy_id}:{';'.join(reasons)}")
                    for rem in st.open_option_legs:
                        book_option_close(s.account, st, rem, rem.intrinsic(spot), status=LegStatus.CLOSED, cash_settled=True)
        st.metadata["early_assignment_reason"] = why
        self._finish_lifecycle(s, st, StrategyStatus.ASSIGNED, f"early_assignment:{why}")

    def _finish_lifecycle(self, s: _Session, st: SimStrategy, status: StrategyStatus, reason: str) -> None:
        acct = s.account
        stock_leg = next((l for l in st.legs if l.leg_role == LegRole.UNDERLYING_STOCK and l.is_open), None)
        call_assigned = any(l.status == LegStatus.ASSIGNED and l.right == OptionRight.CALL for l in st.option_legs)
        if not call_assigned:
            credit_premium_to_lots(acct, st)
        if stock_leg is not None:
            stock_leg.status = LegStatus.CLOSED
            if call_assigned:
                strike = next((l.strike for l in st.option_legs if l.right == OptionRight.CALL), None)
                stock_leg.exit_price = strike
        resolve_strategy(acct, st, status=status, reason=reason, now=s.now)
        wheel_state_after_resolution(acct, st)
        s.touched.add(st.strategy_id)

    # ------------------------------------------------------------------ views

    def get_account_summary(self, account_slug: str) -> OptionAccountSummary:
        acct = self.repo.load_account_state(account_slug)
        if acct is None:
            raise KeyError(account_slug)
        return to_account_summary(acct, self.repo.list_open_strategies(account_slug))

    @staticmethod
    def strategy_to_view(
        strategy: SimStrategy,
        account: Optional[SimAccountState] = None,
        now: Optional[datetime] = None,
        *,
        with_payoff: bool = True,
    ) -> OptionsStrategyGroupView:
        now = ensure_aware(now)
        stock_unrealized = ZERO
        if account is not None and strategy.is_open:
            for lot_id, n in strategy.encumbered_lots.items():
                lot = account.find_lot(lot_id)
                if lot is not None and lot.shares > 0 and lot.last_price is not None:
                    stock_unrealized += (lot.last_price - lot.cost_basis_per_share) * n
        legs_view = [
            OptionsLegView(
                id=l.leg_id,
                occ_symbol=l.occ_symbol,
                underlying_symbol=l.underlying_symbol,
                leg_role=l.leg_role.value,
                side=l.side.value,
                right=l.right.value if l.right else None,
                strike=float(l.strike) if l.strike is not None else None,
                expiration_date=l.expiration_date,
                multiplier=l.multiplier,
                quantity=l.quantity,
                entry_price=float(l.entry_price),
                current_mark=float(l.current_mark),
                bid=float(l.bid) if l.bid is not None else None,
                ask=float(l.ask) if l.ask is not None else None,
                unrealized_pnl=float(l.unrealized_pnl()),
                realized_pnl=float(l.realized_pnl),
                delta=l.greeks.delta,
                gamma=l.greeks.gamma,
                theta=l.greeks.theta,
                vega=l.greeks.vega,
                implied_volatility=l.implied_volatility,
                status=l.status,
            )
            for l in strategy.legs
        ]
        net_delta = net_theta = net_vega = 0.0
        for l in strategy.open_option_legs:
            units = l.sign * l.units
            net_delta += (l.greeks.delta or 0.0) * units
            net_theta += (l.greeks.theta or 0.0) * units
            net_vega += (l.greeks.vega or 0.0) * units
        net_delta += sum(n for n in strategy.encumbered_lots.values())

        payoff = []
        if with_payoff and strategy.is_open:
            spot = account.last_spot_by_symbol.get(strategy.underlying_symbol) if account else None
            spot = spot or to_decimal(strategy.metadata.get("entry_spot"))
            if spot:
                plans = []
                for l in strategy.legs:
                    if not l.is_open:
                        continue
                    if l.is_option:
                        plans.append(LegPlan(
                            occ_symbol=l.occ_symbol, underlying_symbol=l.underlying_symbol, leg_role=l.leg_role,
                            side=l.side, right=l.right, strike=l.strike, expiration_date=l.expiration_date,
                            multiplier=l.multiplier, ratio=max(1, l.quantity // max(1, strategy.contracts_qty)),
                            implied_volatility=l.implied_volatility,
                        ))
                    else:
                        plans.append(LegPlan(
                            underlying_symbol=l.underlying_symbol, leg_role=l.leg_role, side=l.side,
                            multiplier=max(1, l.quantity // max(1, strategy.contracts_qty)), mid=l.entry_price,
                        ))
                payoff = compute_payoff_curve(
                    legs=plans,
                    spot=float(spot),
                    entry_option_cash_flow_usd=float(strategy.net_entry_cash_flow),
                    contracts_qty=strategy.contracts_qty,
                    dte_remaining=max(1, calendar_dte(strategy.expiration_date, now)),
                )

        return OptionsStrategyGroupView(
            id=strategy.strategy_id,
            account_slug=strategy.account_slug,
            strategy_type=strategy.strategy_type.value,
            strategy_label_he=ALL_9_STRATEGY_LABELS_HE.get(strategy.strategy_type.value, strategy.strategy_type.value),
            underlying_symbol=strategy.underlying_symbol,
            status=strategy.status.value,
            mode=strategy.mode,
            contracts_qty=strategy.contracts_qty,
            expiration_date=strategy.expiration_date,
            dte=calendar_dte(strategy.expiration_date, now),
            is_credit=strategy.is_credit,
            net_entry_cash_flow=float(strategy.net_entry_cash_flow),
            open_liability_usd=float(strategy.short_liability()),
            long_value_usd=float(strategy.long_value()),
            collateral_locked_usd=float(strategy.collateral_locked),
            max_profit_usd=float(strategy.max_profit_usd) if strategy.max_profit_usd is not None else None,
            max_loss_usd=float(strategy.max_loss_usd),
            unrealized_pnl=float(money(strategy.option_unrealized() + stock_unrealized)),
            realized_pnl=float(strategy.realized_pnl),
            fees_paid=float(strategy.fees_paid),
            net_delta=round(net_delta, 2),
            net_theta=round(net_theta, 2),
            net_vega=round(net_vega, 2),
            pin_risk_warning=strategy.pin_risk_warning,
            early_assignment_warning=strategy.early_assignment_warning,
            wheel_state=strategy.wheel_state,
            opened_at=strategy.opened_at,
            closed_at=strategy.closed_at,
            close_reason=strategy.close_reason,
            ai_rationale=strategy.ai_rationale,
            legs=legs_view,
            payoff_curve=payoff,
        )

    @staticmethod
    def expiration_item(strategy: SimStrategy, spot: Optional[Decimal], now: Optional[datetime] = None) -> OptionsExpirationItem:
        now = ensure_aware(now)
        dte = calendar_dte(strategy.expiration_date, now)
        pin = detect_pin_risk(strategy, spot, now)
        itm = any_leg_itm(strategy, spot) if spot is not None else False
        status = "ATM_PIN_RISK" if pin else ("ITM" if itm else "OTM")
        level = "urgent" if dte <= 0 or (pin and dte <= 1) else ("watch" if dte <= 2 or itm or pin else "normal")
        return OptionsExpirationItem(
            strategy_group_id=strategy.strategy_id,
            account_slug=strategy.account_slug,
            underlying_symbol=strategy.underlying_symbol,
            strategy_type=strategy.strategy_type.value,
            expiration_date=strategy.expiration_date,
            dte=dte,
            strikes=sorted({float(l.strike) for l in strategy.option_legs if l.strike is not None}),
            underlying_price=float(spot) if spot is not None else None,
            itm_status=status,
            collateral_locked_usd=float(strategy.collateral_locked),
            unrealized_pnl=float(strategy.option_unrealized()),
            warning_level=level,
        )
