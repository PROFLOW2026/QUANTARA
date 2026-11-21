"""Pure accounting state + helpers for the QUANTARA Options simulator.

Identities maintained by every mutation in this package:

    equity    = cash_balance + long_options_market_value - short_options_liability + stock_holdings_value
    free_cash = cash_balance - cash_reserved
    equity    = starting_capital + realized_pnl + unrealized_pnl

Premium accounting: opening a short/credit structure adds the net credit to `cash_balance`
and `premium_collected_gross`, while `realized_pnl` only absorbs fees. The credit reaches
`realized_pnl` / `premium_realized_net` only when the structure is closed, expires, or is
resolved through assignment.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Iterable, Optional

from quantara_options.api_schemas import OptionsJournalEventView
from quantara_options.contracts import (
    AccountRuleProfile,
    DecisionMode,
    ExerciseStyle,
    Greeks,
    LegRole,
    OptionAccountSummary,
    OptionRight,
    OptionSide,
    StrategyStatus,
    StrategyType,
)
from quantara_options.sim.fees_and_slippage import ZERO, money, to_decimal

CREDIT_STRATEGIES = frozenset(
    {
        StrategyType.CASH_SECURED_PUT,
        StrategyType.COVERED_CALL,
        StrategyType.WHEEL,
        StrategyType.BULL_PUT_CREDIT_SPREAD,
        StrategyType.BEAR_CALL_CREDIT_SPREAD,
        StrategyType.IRON_CONDOR,
    }
)

RESOLVED_STATUSES = frozenset(
    {StrategyStatus.CLOSED, StrategyStatus.EXPIRED, StrategyStatus.ASSIGNED, StrategyStatus.ROLLED}
)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class LegStatus:
    OPEN = "open"
    CLOSED = "closed"
    EXPIRED = "expired"
    ASSIGNED = "assigned"
    EXERCISED = "exercised"


@dataclass
class SimLeg:
    leg_id: str
    underlying_symbol: str
    leg_role: LegRole
    side: OptionSide
    quantity: int                      # contracts for option legs, shares for the stock leg
    multiplier: int                    # contract multiplier (1 for stock legs)
    entry_price: Decimal               # executed price per share
    occ_symbol: Optional[str] = None
    right: Optional[OptionRight] = None
    strike: Optional[Decimal] = None
    expiration_date: Optional[date] = None
    exercise_style: ExerciseStyle = ExerciseStyle.AMERICAN
    current_mark: Decimal = ZERO
    bid: Optional[Decimal] = None
    ask: Optional[Decimal] = None
    mark_is_estimated: bool = False
    implied_volatility: Optional[float] = None
    entry_implied_volatility: Optional[float] = None
    greeks: Greeks = field(default_factory=Greeks)
    status: str = LegStatus.OPEN
    exit_price: Optional[Decimal] = None
    realized_pnl: Decimal = ZERO       # gross of fees
    lot_id: Optional[str] = None       # stock legs: linked opt_stock_lot

    @property
    def is_option(self) -> bool:
        return self.leg_role != LegRole.UNDERLYING_STOCK and self.right is not None

    @property
    def is_open(self) -> bool:
        return self.status == LegStatus.OPEN

    @property
    def sign(self) -> int:
        return 1 if self.side == OptionSide.LONG else -1

    @property
    def units(self) -> int:
        """Shares controlled by the leg (quantity * multiplier)."""
        return self.quantity * self.multiplier

    def market_value(self) -> Decimal:
        return money(self.current_mark * self.multiplier * self.quantity)

    def entry_value(self) -> Decimal:
        return money(self.entry_price * self.multiplier * self.quantity)

    def unrealized_pnl(self) -> Decimal:
        if not self.is_open or not self.is_option:
            return ZERO
        return money(self.sign * (self.current_mark - self.entry_price) * self.multiplier * self.quantity)

    def intrinsic(self, spot: Decimal) -> Decimal:
        if self.strike is None or self.right is None:
            return ZERO
        if self.right == OptionRight.CALL:
            return max(ZERO, spot - self.strike)
        return max(ZERO, self.strike - spot)


@dataclass
class SimStrategy:
    strategy_id: str
    account_slug: str
    strategy_type: StrategyType
    underlying_symbol: str
    contracts_qty: int
    expiration_date: date
    is_credit: bool
    legs: list[SimLeg]
    mode: DecisionMode = DecisionMode.RULES
    status: StrategyStatus = StrategyStatus.OPEN
    net_entry_cash_flow: Decimal = ZERO          # option legs only: +credit / -debit (before fees)
    collateral_locked: Decimal = ZERO            # portion of account.cash_reserved held by this strategy
    margin_requirement: Decimal = ZERO
    encumbered_lots: dict[str, int] = field(default_factory=dict)  # lot_id -> shares locked
    max_profit_usd: Optional[Decimal] = None
    max_loss_usd: Decimal = ZERO
    option_realized_gross: Decimal = ZERO
    stock_realized_gross: Decimal = ZERO
    option_fees: Decimal = ZERO
    stock_fees: Decimal = ZERO
    opened_at: datetime = field(default_factory=utcnow)
    closed_at: Optional[datetime] = None
    close_reason: Optional[str] = None
    ai_rationale: Optional[str] = None
    candidate_id: Optional[str] = None
    wheel_state: Optional[str] = None
    pin_risk_warning: bool = False
    early_assignment_warning: bool = False
    exit_breach: Optional[str] = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def is_open(self) -> bool:
        return self.status == StrategyStatus.OPEN

    @property
    def fees_paid(self) -> Decimal:
        return money(self.option_fees + self.stock_fees)

    @property
    def realized_pnl(self) -> Decimal:
        """Net of every fee charged to this strategy (entry fees count immediately)."""
        return money(self.option_realized_gross + self.stock_realized_gross - self.fees_paid)

    @property
    def option_legs(self) -> list[SimLeg]:
        return [l for l in self.legs if l.is_option]

    @property
    def open_option_legs(self) -> list[SimLeg]:
        return [l for l in self.legs if l.is_option and l.is_open]

    def long_value(self) -> Decimal:
        return money(sum((l.market_value() for l in self.open_option_legs if l.side == OptionSide.LONG), ZERO))

    def short_liability(self) -> Decimal:
        return money(sum((l.market_value() for l in self.open_option_legs if l.side == OptionSide.SHORT), ZERO))

    def option_unrealized(self) -> Decimal:
        return money(sum((l.unrealized_pnl() for l in self.open_option_legs), ZERO))

    def mark_is_estimated(self) -> bool:
        return any(l.mark_is_estimated for l in self.open_option_legs)

    def short_strikes(self) -> list[Decimal]:
        return [l.strike for l in self.open_option_legs if l.side == OptionSide.SHORT and l.strike is not None]

    def credit_per_share(self) -> Decimal:
        shares = sum(l.units for l in self.option_legs if l.side == OptionSide.SHORT) or 1
        return self.net_entry_cash_flow / shares if self.net_entry_cash_flow > ZERO else ZERO


@dataclass
class SimStockLot:
    lot_id: str
    account_slug: str
    symbol: str
    shares: int
    cost_basis_per_share: Decimal                 # actual cash paid per share (accounting basis)
    premium_credit_per_share: Decimal = ZERO      # option premium credited against the lot (wheel)
    encumbered_shares: int = 0
    last_price: Optional[Decimal] = None
    acquired_at: datetime = field(default_factory=utcnow)
    source: str = "buy"                           # buy | buy_write | assignment | exercise
    source_strategy_id: Optional[str] = None

    @property
    def adjusted_cost_basis_per_share(self) -> Decimal:
        return self.cost_basis_per_share - self.premium_credit_per_share

    @property
    def unencumbered_shares(self) -> int:
        return max(0, self.shares - self.encumbered_shares)

    def market_value(self) -> Decimal:
        px = self.last_price if self.last_price is not None else self.cost_basis_per_share
        return money(px * self.shares)

    def unrealized_pnl(self) -> Decimal:
        px = self.last_price if self.last_price is not None else self.cost_basis_per_share
        return money((px - self.cost_basis_per_share) * self.shares)


@dataclass
class SimJournalEvent:
    account_slug: str
    event_type: str  # OPEN, CLOSE, ROLL, EXPIRE, ASSIGN, EXERCISE, STOCK_DELIVERY, FEE, WARNING
    underlying_symbol: str
    strategy_type: str
    description: str
    strategy_group_id: Optional[str] = None
    cash_delta: Decimal = ZERO
    realized_pnl_delta: Decimal = ZERO
    collateral_delta: Decimal = ZERO
    fee_usd: Decimal = ZERO
    created_at: datetime = field(default_factory=utcnow)
    metadata: dict[str, Any] = field(default_factory=dict)
    event_id: str = field(default_factory=lambda: new_id("oje"))

    def to_view(self) -> OptionsJournalEventView:
        return OptionsJournalEventView(
            id=self.event_id,
            account_slug=self.account_slug,
            strategy_group_id=self.strategy_group_id,
            event_type=self.event_type,
            underlying_symbol=self.underlying_symbol,
            strategy_type=self.strategy_type,
            cash_delta=float(money(self.cash_delta)),
            realized_pnl_delta=float(money(self.realized_pnl_delta)),
            collateral_delta=float(money(self.collateral_delta)),
            fee_usd=float(money(self.fee_usd)),
            description=self.description,
            created_at=self.created_at,
            metadata=self.metadata,
        )


@dataclass
class SimAccountState:
    slug: str
    starting_capital: Decimal
    cash_balance: Decimal
    account_id: str = ""
    name: str = ""
    mode: DecisionMode = DecisionMode.RULES
    strategy_scope: Optional[str] = None
    cash_reserved: Decimal = ZERO
    fees_paid: Decimal = ZERO
    realized_pnl: Decimal = ZERO
    premium_collected_gross: Decimal = ZERO
    premium_realized_net: Decimal = ZERO
    peak_equity: Decimal = ZERO
    max_drawdown_usd: Decimal = ZERO
    max_drawdown_pct: Decimal = ZERO
    rule_profile: AccountRuleProfile = field(default_factory=AccountRuleProfile)
    wheel_states: dict[str, str] = field(default_factory=dict)
    stock_lots: list[SimStockLot] = field(default_factory=list)
    day_start_date: Optional[date] = None
    day_start_equity: Optional[Decimal] = None
    day_trade_dates: list[date] = field(default_factory=list)
    last_spot_by_symbol: dict[str, Decimal] = field(default_factory=dict)
    closed_strategies_count: int = 0
    winning_strategies: int = 0
    losing_strategies: int = 0
    gross_profit: Decimal = ZERO
    gross_loss: Decimal = ZERO
    is_active: bool = True
    experiment_group: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    @property
    def free_cash(self) -> Decimal:
        return money(self.cash_balance - self.cash_reserved)

    def lots_for(self, symbol: str) -> list[SimStockLot]:
        return [l for l in self.stock_lots if l.symbol == symbol and l.shares > 0]

    def unencumbered_shares(self, symbol: str) -> int:
        return sum(l.unencumbered_shares for l in self.lots_for(symbol))

    def find_lot(self, lot_id: str) -> Optional[SimStockLot]:
        return next((l for l in self.stock_lots if l.lot_id == lot_id), None)


# ---------------------------------------------------------------------------
# Ledger primitives. Each keeps cash, realized PnL and fee totals in lock-step so the
# identity `equity = starting_capital + realized + unrealized` holds by construction.
# ---------------------------------------------------------------------------


def charge_fee(
    account: SimAccountState,
    strategy: Optional[SimStrategy],
    amount: Decimal,
    *,
    stock: bool = False,
) -> Decimal:
    fee = money(amount)
    if fee <= ZERO:
        return ZERO
    account.cash_balance = money(account.cash_balance - fee)
    account.fees_paid = money(account.fees_paid + fee)
    account.realized_pnl = money(account.realized_pnl - fee)
    if strategy is not None:
        if stock:
            strategy.stock_fees = money(strategy.stock_fees + fee)
        else:
            strategy.option_fees = money(strategy.option_fees + fee)
    return fee


def book_option_open(account: SimAccountState, leg: SimLeg) -> Decimal:
    """Cash flow for an opening option fill: sells add proceeds, buys pay the debit."""
    cash = leg.entry_value() if leg.side == OptionSide.SHORT else -leg.entry_value()
    account.cash_balance = money(account.cash_balance + cash)
    return cash


def book_option_close(
    account: SimAccountState,
    strategy: SimStrategy,
    leg: SimLeg,
    exit_price: Decimal,
    *,
    status: str = LegStatus.CLOSED,
    cash_settled: bool = True,
) -> tuple[Decimal, Decimal]:
    """Close a leg at `exit_price` per share. Returns (cash_delta, realized_gross_delta).

    `cash_settled=False` (physical exercise/assignment) closes the option at `exit_price`
    for PnL purposes without a premium cash flow; the stock delivery carries the economics.
    """
    exit_price = max(ZERO, exit_price)
    value = money(exit_price * leg.multiplier * leg.quantity)
    cash = ZERO
    if cash_settled:
        cash = value if leg.side == OptionSide.LONG else -value
        account.cash_balance = money(account.cash_balance + cash)
    realized = money(leg.sign * (exit_price - leg.entry_price) * leg.multiplier * leg.quantity)
    leg.exit_price = exit_price
    leg.current_mark = exit_price
    leg.realized_pnl = money(leg.realized_pnl + realized)
    leg.status = status
    strategy.option_realized_gross = money(strategy.option_realized_gross + realized)
    account.realized_pnl = money(account.realized_pnl + realized)
    return cash, realized


def reserve_cash(account: SimAccountState, strategy: SimStrategy, amount: Decimal) -> None:
    amt = money(amount)
    account.cash_reserved = money(account.cash_reserved + amt)
    strategy.collateral_locked = money(strategy.collateral_locked + amt)


def release_cash(account: SimAccountState, strategy: SimStrategy) -> Decimal:
    released = strategy.collateral_locked
    account.cash_reserved = money(max(ZERO, account.cash_reserved - released))
    strategy.collateral_locked = ZERO
    return released


def encumber_shares(account: SimAccountState, strategy: SimStrategy, symbol: str, shares: int) -> int:
    remaining = shares
    for lot in sorted(account.lots_for(symbol), key=lambda l: l.acquired_at):
        if remaining <= 0:
            break
        take = min(lot.unencumbered_shares, remaining)
        if take <= 0:
            continue
        lot.encumbered_shares += take
        strategy.encumbered_lots[lot.lot_id] = strategy.encumbered_lots.get(lot.lot_id, 0) + take
        remaining -= take
    return shares - remaining


def unencumber_shares(account: SimAccountState, strategy: SimStrategy) -> int:
    freed = 0
    for lot_id, n in list(strategy.encumbered_lots.items()):
        lot = account.find_lot(lot_id)
        if lot is not None:
            take = min(lot.encumbered_shares, n)
            lot.encumbered_shares -= take
            freed += take
    strategy.encumbered_lots = {}
    return freed


def buy_stock(
    account: SimAccountState,
    *,
    symbol: str,
    shares: int,
    price: Decimal,
    source: str,
    strategy_id: Optional[str],
    now: datetime,
    premium_credit_per_share: Decimal = ZERO,
) -> SimStockLot:
    cost = money(price * shares)
    account.cash_balance = money(account.cash_balance - cost)
    lot = SimStockLot(
        lot_id=new_id("lot"),
        account_slug=account.slug,
        symbol=symbol,
        shares=shares,
        cost_basis_per_share=price,
        premium_credit_per_share=premium_credit_per_share,
        last_price=price,
        acquired_at=now,
        source=source,
        source_strategy_id=strategy_id,
    )
    account.stock_lots.append(lot)
    return lot


def sell_stock_from_lot(
    account: SimAccountState,
    lot: SimStockLot,
    *,
    shares: int,
    price: Decimal,
    strategy: Optional[SimStrategy] = None,
    from_encumbered: bool = False,
) -> tuple[Decimal, Decimal]:
    """Sell shares from one lot. Returns (cash_delta, realized_gross_delta)."""
    shares = min(shares, lot.shares)
    proceeds = money(price * shares)
    realized = money((price - lot.cost_basis_per_share) * shares)
    account.cash_balance = money(account.cash_balance + proceeds)
    account.realized_pnl = money(account.realized_pnl + realized)
    lot.shares -= shares
    if from_encumbered:
        lot.encumbered_shares = max(0, lot.encumbered_shares - shares)
    lot.encumbered_shares = min(lot.encumbered_shares, lot.shares)
    if strategy is not None:
        strategy.stock_realized_gross = money(strategy.stock_realized_gross + realized)
    if lot.shares <= 0:
        account.stock_lots = [l for l in account.stock_lots if l.lot_id != lot.lot_id]
    return proceeds, realized


def sell_unencumbered_shares(
    account: SimAccountState,
    *,
    symbol: str,
    shares: int,
    price: Decimal,
    strategy: Optional[SimStrategy] = None,
) -> tuple[Decimal, Decimal, int]:
    remaining = shares
    cash_total = ZERO
    realized_total = ZERO
    for lot in sorted(account.lots_for(symbol), key=lambda l: l.acquired_at):
        if remaining <= 0:
            break
        take = min(lot.unencumbered_shares, remaining)
        if take <= 0:
            continue
        c, r = sell_stock_from_lot(account, lot, shares=take, price=price, strategy=strategy)
        cash_total += c
        realized_total += r
        remaining -= take
    return money(cash_total), money(realized_total), shares - remaining


def resolve_strategy(
    account: SimAccountState,
    strategy: SimStrategy,
    *,
    status: StrategyStatus,
    reason: str,
    now: datetime,
) -> None:
    """Finalize a strategy: release collateral/encumbrance and roll premium + win stats."""
    release_cash(account, strategy)
    unencumber_shares(account, strategy)
    strategy.status = status
    strategy.closed_at = now
    strategy.close_reason = reason
    if strategy.is_credit and strategy.net_entry_cash_flow > ZERO:
        account.premium_realized_net = money(
            account.premium_realized_net + strategy.option_realized_gross - strategy.option_fees
        )
    net = strategy.realized_pnl
    account.closed_strategies_count += 1
    if net > ZERO:
        account.winning_strategies += 1
        account.gross_profit = money(account.gross_profit + net)
    elif net < ZERO:
        account.losing_strategies += 1
        account.gross_loss = money(account.gross_loss + (-net))


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------


@dataclass
class AccountTotals:
    cash_balance: Decimal
    cash_reserved: Decimal
    free_cash: Decimal
    margin_used: Decimal
    long_options_market_value: Decimal
    short_options_liability: Decimal
    stock_holdings_value: Decimal
    equity: Decimal
    unrealized_pnl: Decimal
    realized_pnl: Decimal
    premium_collected_gross: Decimal
    premium_realized_net: Decimal
    fees_paid: Decimal
    max_drawdown_usd: Decimal
    max_drawdown_pct: Decimal
    return_pct: Decimal
    open_strategies_count: int
    encumbered_shares: int
    has_estimated_marks: bool = False


def compute_equity(
    *,
    cash_balance: Decimal,
    long_options_market_value: Decimal,
    short_options_liability: Decimal,
    stock_holdings_value: Decimal,
) -> Decimal:
    return money(cash_balance + long_options_market_value - short_options_liability + stock_holdings_value)


def compute_account_totals(
    account: SimAccountState,
    open_strategies: Iterable[SimStrategy],
) -> AccountTotals:
    opens = [s for s in open_strategies if s.is_open]
    long_mv = money(sum((s.long_value() for s in opens), ZERO))
    short_liab = money(sum((s.short_liability() for s in opens), ZERO))
    stock_val = money(sum((l.market_value() for l in account.stock_lots), ZERO))
    equity = compute_equity(
        cash_balance=account.cash_balance,
        long_options_market_value=long_mv,
        short_options_liability=short_liab,
        stock_holdings_value=stock_val,
    )
    unrealized = money(
        sum((s.option_unrealized() for s in opens), ZERO)
        + sum((l.unrealized_pnl() for l in account.stock_lots), ZERO)
    )
    start = account.starting_capital
    return_pct = money((equity - start) / start * 100) if start > ZERO else ZERO
    return AccountTotals(
        cash_balance=money(account.cash_balance),
        cash_reserved=money(account.cash_reserved),
        free_cash=account.free_cash,
        margin_used=money(sum((s.margin_requirement for s in opens), ZERO)),
        long_options_market_value=long_mv,
        short_options_liability=short_liab,
        stock_holdings_value=stock_val,
        equity=equity,
        unrealized_pnl=unrealized,
        realized_pnl=money(account.realized_pnl),
        premium_collected_gross=money(account.premium_collected_gross),
        premium_realized_net=money(account.premium_realized_net),
        fees_paid=money(account.fees_paid),
        max_drawdown_usd=money(account.max_drawdown_usd),
        max_drawdown_pct=money(account.max_drawdown_pct),
        return_pct=return_pct,
        open_strategies_count=len(opens),
        encumbered_shares=sum(l.encumbered_shares for l in account.stock_lots),
        has_estimated_marks=any(s.mark_is_estimated() for s in opens),
    )


def update_drawdown(account: SimAccountState, equity: Decimal) -> None:
    if account.peak_equity <= ZERO:
        account.peak_equity = max(account.starting_capital, equity)
    if equity > account.peak_equity:
        account.peak_equity = equity
    dd_usd = money(account.peak_equity - equity)
    if dd_usd > account.max_drawdown_usd:
        account.max_drawdown_usd = dd_usd
    if account.peak_equity > ZERO:
        dd_pct = money(dd_usd / account.peak_equity * 100)
        if dd_pct > account.max_drawdown_pct:
            account.max_drawdown_pct = dd_pct


def current_drawdown_pct(account: SimAccountState, equity: Decimal) -> Decimal:
    peak = max(account.peak_equity, account.starting_capital)
    if peak <= ZERO:
        return ZERO
    return money(max(ZERO, peak - equity) / peak * 100)


def check_conservation(
    account: SimAccountState,
    open_strategies: Iterable[SimStrategy],
    *,
    tolerance: Decimal = Decimal("0.05"),
) -> list[str]:
    """Return a list of violated accounting invariants (empty list == books balance)."""
    opens = [s for s in open_strategies if s.is_open]
    totals = compute_account_totals(account, opens)
    problems: list[str] = []

    identity = account.starting_capital + totals.realized_pnl + totals.unrealized_pnl
    if abs(totals.equity - identity) > tolerance:
        problems.append(
            f"equity {totals.equity} != starting_capital + realized + unrealized = {money(identity)}"
        )
    reserved = money(sum((s.collateral_locked for s in opens), ZERO))
    if abs(reserved - account.cash_reserved) > tolerance:
        problems.append(f"cash_reserved {account.cash_reserved} != sum(strategy collateral) {reserved}")
    enc_from_strats: dict[str, int] = {}
    for s in opens:
        for lot_id, n in s.encumbered_lots.items():
            enc_from_strats[lot_id] = enc_from_strats.get(lot_id, 0) + n
    for lot in account.stock_lots:
        if lot.encumbered_shares != enc_from_strats.get(lot.lot_id, 0):
            problems.append(f"lot {lot.lot_id} encumbered {lot.encumbered_shares} != strategy locks")
        if lot.encumbered_shares > lot.shares or lot.shares < 0:
            problems.append(f"lot {lot.lot_id} encumbered/shares out of range")
    if account.cash_reserved < ZERO:
        problems.append("cash_reserved negative")
    return problems


def to_account_summary(
    account: SimAccountState,
    open_strategies: Iterable[SimStrategy],
) -> OptionAccountSummary:
    totals = compute_account_totals(account, open_strategies)
    decided = account.winning_strategies + account.losing_strategies
    win_rate = round(account.winning_strategies / decided * 100, 2) if decided else None
    profit_factor = (
        round(float(account.gross_profit / account.gross_loss), 3) if account.gross_loss > ZERO else None
    )
    return OptionAccountSummary(
        id=account.account_id or account.slug,
        slug=account.slug,
        name=account.name or account.slug,
        mode=account.mode,
        strategy_scope=account.strategy_scope,
        starting_capital=float(account.starting_capital),
        cash_balance=float(totals.cash_balance),
        cash_reserved=float(totals.cash_reserved),
        free_cash=float(totals.free_cash),
        margin_used=float(totals.margin_used),
        long_options_market_value=float(totals.long_options_market_value),
        short_options_liability=float(totals.short_options_liability),
        stock_holdings_value=float(totals.stock_holdings_value),
        equity=float(totals.equity),
        premium_collected_gross=float(totals.premium_collected_gross),
        premium_realized_net=float(totals.premium_realized_net),
        unrealized_pnl=float(totals.unrealized_pnl),
        realized_pnl=float(totals.realized_pnl),
        fees_paid=float(totals.fees_paid),
        max_drawdown_usd=float(totals.max_drawdown_usd),
        max_drawdown_pct=float(totals.max_drawdown_pct),
        return_pct=float(totals.return_pct),
        open_strategies_count=totals.open_strategies_count,
        closed_strategies_count=account.closed_strategies_count,
        win_rate_pct=win_rate,
        profit_factor=profit_factor,
        rule_profile=account.rule_profile,
        is_active=account.is_active,
        experiment_group=account.experiment_group,
        wheel_states=dict(account.wheel_states),
        created_at=account.created_at,
        updated_at=account.updated_at,
    )


def new_account_state(
    slug: str,
    starting_capital: Decimal | float | int,
    *,
    name: str = "",
    mode: DecisionMode = DecisionMode.RULES,
    strategy_scope: Optional[str] = None,
    rule_profile: Optional[AccountRuleProfile] = None,
) -> SimAccountState:
    cap = money(to_decimal(starting_capital))
    return SimAccountState(
        slug=slug,
        account_id=slug,
        name=name or slug,
        mode=mode,
        strategy_scope=strategy_scope,
        starting_capital=cap,
        cash_balance=cap,
        peak_equity=cap,
        rule_profile=rule_profile or AccountRuleProfile(),
        created_at=utcnow(),
        updated_at=utcnow(),
    )
