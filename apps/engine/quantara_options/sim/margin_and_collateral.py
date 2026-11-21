"""Collateral, reserved cash, max-loss and account-rule checks for the 9 QUANTARA structures.

All widths/strikes are scaled by each leg's own `multiplier` and contract count.

Credit structures reserve the *full width* in `cash_reserved` while the net credit lands in
`cash_balance`. Because `free_cash = cash_balance - cash_reserved`, free cash drops by exactly
`width * multiplier * qty - credit` == the structure's max loss (plus fees):

    cash_balance' = cash + credit - fees
    cash_reserved' = reserved + width * mult * qty
    free_cash'    = free_cash - (width * mult * qty - credit) - fees
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Iterable, Optional, Sequence

from quantara_options.contracts import (
    AccountRuleProfile,
    LegPlan,
    LegRole,
    OptionRight,
    OptionSide,
    StrategyType,
)
from quantara_options.sim.fees_and_slippage import ZERO, money, to_decimal

STRATEGY_APPROVAL_LEVEL: dict[StrategyType, int] = {
    StrategyType.COVERED_CALL: 1,
    StrategyType.CASH_SECURED_PUT: 1,
    StrategyType.WHEEL: 1,
    StrategyType.LONG_CALL: 2,
    StrategyType.LONG_PUT: 2,
    StrategyType.BULL_CALL_SPREAD: 3,
    StrategyType.BEAR_PUT_SPREAD: 3,
    StrategyType.BULL_PUT_CREDIT_SPREAD: 3,
    StrategyType.BEAR_CALL_CREDIT_SPREAD: 3,
    StrategyType.IRON_CONDOR: 3,
}

SPREAD_STRATEGIES = frozenset(
    {
        StrategyType.BULL_CALL_SPREAD,
        StrategyType.BEAR_PUT_SPREAD,
        StrategyType.BULL_PUT_CREDIT_SPREAD,
        StrategyType.BEAR_CALL_CREDIT_SPREAD,
        StrategyType.IRON_CONDOR,
    }
)

# Capital-at-risk strategies are sized by collateral %, not by the per-trade max-loss %.
CAPITAL_SECURED_STRATEGIES = frozenset(
    {StrategyType.CASH_SECURED_PUT, StrategyType.COVERED_CALL, StrategyType.WHEEL}
)


class StructureError(ValueError):
    """Legs do not form the declared strategy structure."""


@dataclass(frozen=True)
class StructureLeg:
    leg_role: LegRole
    side: OptionSide
    right: Optional[OptionRight]
    strike: Optional[Decimal]
    multiplier: int
    contracts: int                     # option contracts (or 0 for stock legs)
    expiration_date: Optional[date] = None

    @property
    def is_option(self) -> bool:
        return self.leg_role != LegRole.UNDERLYING_STOCK and self.right is not None

    @property
    def units(self) -> int:
        return self.multiplier * self.contracts


def structure_legs_from_plans(legs: Sequence[LegPlan], contracts_qty: int) -> list[StructureLeg]:
    out: list[StructureLeg] = []
    for leg in legs:
        ratio = max(1, int(leg.ratio or 1))
        out.append(
            StructureLeg(
                leg_role=leg.leg_role,
                side=leg.side,
                right=leg.right,
                strike=to_decimal(leg.strike),
                multiplier=int(leg.multiplier or 100),
                contracts=contracts_qty * ratio if leg.leg_role != LegRole.UNDERLYING_STOCK else 0,
                expiration_date=leg.expiration_date,
            )
        )
    return out


@dataclass
class CollateralRequirement:
    strategy_type: StrategyType
    cash_reserved: Decimal = ZERO          # locked in account.cash_reserved
    margin_requirement: Decimal = ZERO     # broker-style requirement (informational for defined risk)
    shares_required: int = 0               # covered-call shares that must be encumbered
    max_loss_usd: Decimal = ZERO
    max_profit_usd: Optional[Decimal] = None
    width_usd: Decimal = ZERO              # width * multiplier * contracts (spreads)
    break_evens: list[Decimal] = field(default_factory=list)
    has_naked_short: bool = False
    notes: list[str] = field(default_factory=list)


def _one(legs: Iterable[StructureLeg], role: LegRole) -> list[StructureLeg]:
    return [l for l in legs if l.leg_role == role]


def _require(cond: bool, msg: str) -> None:
    if not cond:
        raise StructureError(msg)


def _check_uniform(option_legs: list[StructureLeg]) -> tuple[int, int]:
    _require(bool(option_legs), "strategy has no option legs")
    mults = {l.multiplier for l in option_legs}
    _require(len(mults) == 1, f"mixed contract multipliers {sorted(mults)} are not supported in one structure")
    contracts = {l.contracts for l in option_legs}
    _require(len(contracts) == 1, "all option legs must have the same contract count")
    _require(all(l.strike is not None and l.strike > ZERO for l in option_legs), "every option leg needs a strike")
    exps = {l.expiration_date for l in option_legs if l.expiration_date is not None}
    _require(len(exps) <= 1, "calendar/diagonal structures are not part of the 9 supported strategies")
    return option_legs[0].multiplier, option_legs[0].contracts


def compute_collateral(
    strategy_type: StrategyType,
    legs: Sequence[StructureLeg],
    *,
    net_option_cash_flow: Decimal,
    stock_reference_price: Optional[Decimal] = None,
) -> CollateralRequirement:
    """Collateral & risk for a structure. `net_option_cash_flow`: +credit / -debit in USD (fills)."""
    option_legs = [l for l in legs if l.is_option]
    mult, contracts = _check_uniform(option_legs)
    units = mult * contracts
    credit = net_option_cash_flow if net_option_cash_flow > ZERO else ZERO
    debit = -net_option_cash_flow if net_option_cash_flow < ZERO else ZERO
    req = CollateralRequirement(strategy_type=strategy_type)
    per_share_credit = net_option_cash_flow / units

    lc = _one(option_legs, LegRole.LONG_CALL)
    sc = _one(option_legs, LegRole.SHORT_CALL)
    lp = _one(option_legs, LegRole.LONG_PUT)
    sp = _one(option_legs, LegRole.SHORT_PUT)

    st = strategy_type
    if st == StrategyType.WHEEL:
        st = StrategyType.CASH_SECURED_PUT if sp else StrategyType.COVERED_CALL

    if st == StrategyType.LONG_CALL:
        _require(len(lc) == 1 and len(option_legs) == 1, "long_call requires exactly one long call")
        req.max_loss_usd = debit
        req.max_profit_usd = None
        req.break_evens = [lc[0].strike + debit / units]  # type: ignore[operator]
    elif st == StrategyType.LONG_PUT:
        _require(len(lp) == 1 and len(option_legs) == 1, "long_put requires exactly one long put")
        k = lp[0].strike  # type: ignore[assignment]
        req.max_loss_usd = debit
        req.max_profit_usd = money(k * units - debit)
        req.break_evens = [k - debit / units]
    elif st == StrategyType.CASH_SECURED_PUT:
        _require(len(sp) == 1 and len(option_legs) == 1, "cash_secured_put requires exactly one short put")
        k = sp[0].strike  # type: ignore[assignment]
        req.cash_reserved = money(k * units)
        req.max_loss_usd = money(k * units - credit)
        req.max_profit_usd = credit
        req.break_evens = [k - per_share_credit]
    elif st == StrategyType.COVERED_CALL:
        _require(len(sc) == 1 and len(option_legs) == 1, "covered_call requires exactly one short call")
        k = sc[0].strike  # type: ignore[assignment]
        req.shares_required = units
        ref = stock_reference_price or k
        req.max_loss_usd = money(max(ZERO, ref * units - credit))
        req.max_profit_usd = money((k - ref) * units + credit)
        req.break_evens = [ref - per_share_credit]
        req.notes.append("shares_encumbered_no_cash_collateral")
    elif st == StrategyType.BULL_CALL_SPREAD:
        _require(len(lc) == 1 and len(sc) == 1 and len(option_legs) == 2, "bull_call_spread needs 1 long + 1 short call")
        _require(lc[0].strike < sc[0].strike, "bull_call_spread: long strike must be below short strike")  # type: ignore[operator]
        width = money((sc[0].strike - lc[0].strike) * units)  # type: ignore[operator]
        req.width_usd = width
        req.max_loss_usd = debit
        req.max_profit_usd = money(width - debit)
        req.break_evens = [lc[0].strike + debit / units]  # type: ignore[operator]
    elif st == StrategyType.BEAR_PUT_SPREAD:
        _require(len(lp) == 1 and len(sp) == 1 and len(option_legs) == 2, "bear_put_spread needs 1 long + 1 short put")
        _require(lp[0].strike > sp[0].strike, "bear_put_spread: long strike must be above short strike")  # type: ignore[operator]
        width = money((lp[0].strike - sp[0].strike) * units)  # type: ignore[operator]
        req.width_usd = width
        req.max_loss_usd = debit
        req.max_profit_usd = money(width - debit)
        req.break_evens = [lp[0].strike - debit / units]  # type: ignore[operator]
    elif st == StrategyType.BULL_PUT_CREDIT_SPREAD:
        _require(len(lp) == 1 and len(sp) == 1 and len(option_legs) == 2, "bull_put_credit_spread needs 1 short + 1 long put")
        _require(sp[0].strike > lp[0].strike, "bull_put_credit_spread: short strike must be above long strike")  # type: ignore[operator]
        width = money((sp[0].strike - lp[0].strike) * units)  # type: ignore[operator]
        req.width_usd = width
        req.cash_reserved = width
        req.margin_requirement = width
        req.max_loss_usd = money(max(ZERO, width - credit))
        req.max_profit_usd = credit
        req.break_evens = [sp[0].strike - per_share_credit]  # type: ignore[operator]
    elif st == StrategyType.BEAR_CALL_CREDIT_SPREAD:
        _require(len(lc) == 1 and len(sc) == 1 and len(option_legs) == 2, "bear_call_credit_spread needs 1 short + 1 long call")
        _require(sc[0].strike < lc[0].strike, "bear_call_credit_spread: short strike must be below long strike")  # type: ignore[operator]
        width = money((lc[0].strike - sc[0].strike) * units)  # type: ignore[operator]
        req.width_usd = width
        req.cash_reserved = width
        req.margin_requirement = width
        req.max_loss_usd = money(max(ZERO, width - credit))
        req.max_profit_usd = credit
        req.break_evens = [sc[0].strike + per_share_credit]  # type: ignore[operator]
    elif st == StrategyType.IRON_CONDOR:
        _require(
            len(lp) == 1 and len(sp) == 1 and len(sc) == 1 and len(lc) == 1 and len(option_legs) == 4,
            "iron_condor needs long put < short put < short call < long call",
        )
        _require(
            lp[0].strike < sp[0].strike < sc[0].strike < lc[0].strike,  # type: ignore[operator]
            "iron_condor strikes must satisfy long put < short put < short call < long call",
        )
        put_w = (sp[0].strike - lp[0].strike) * units  # type: ignore[operator]
        call_w = (lc[0].strike - sc[0].strike) * units  # type: ignore[operator]
        width = money(max(put_w, call_w))
        req.width_usd = width
        req.cash_reserved = width
        req.margin_requirement = width
        req.max_loss_usd = money(max(ZERO, width - credit))
        req.max_profit_usd = credit
        req.break_evens = [sp[0].strike - per_share_credit, sc[0].strike + per_share_credit]  # type: ignore[operator]
        req.notes.append("finra_ic_one_side_requirement")
    else:
        raise StructureError(f"unsupported strategy type {strategy_type}")

    req.break_evens = [money(b) for b in req.break_evens]
    return req


@dataclass
class RuleCheckResult:
    passed: bool = True
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def fail(self, reason: str) -> None:
        self.passed = False
        self.reasons.append(reason)


def count_recent_day_trades(day_trade_dates: Iterable[date], today: date, *, business_days: int = 5) -> int:
    cutoff = today
    counted = 0
    while counted < business_days - 1:
        cutoff -= timedelta(days=1)
        if cutoff.weekday() < 5:
            counted += 1
    return sum(1 for d in day_trade_dates if cutoff <= d <= today)


def check_open_rules(
    profile: AccountRuleProfile,
    *,
    strategy_type: StrategyType,
    requirement: CollateralRequirement,
    cash_needed_usd: Decimal,
    free_cash: Decimal,
    equity: Decimal,
    cash_reserved_after: Decimal,
    open_strategies_count: int,
    unencumbered_shares: int,
    shares_to_buy: int,
    day_trade_dates: Sequence[date] = (),
    today: Optional[date] = None,
    current_drawdown_pct: Decimal = ZERO,
    day_pnl_pct: Decimal = ZERO,
) -> RuleCheckResult:
    """Configurable per-account checks for opening a structure (never global hard blocks)."""
    res = RuleCheckResult()
    needed_level = 4 if requirement.has_naked_short else STRATEGY_APPROVAL_LEVEL.get(strategy_type, 3)
    if profile.options_approval_level < needed_level:
        res.fail(f"options_approval_level {profile.options_approval_level} < required {needed_level} for {strategy_type.value}")

    if requirement.has_naked_short and not profile.margin_enabled:
        res.fail("naked short options require margin_enabled")
    if strategy_type in SPREAD_STRATEGIES and not profile.margin_enabled:
        res.warnings.append("cash_account_spread_full_width_reserved")

    if cash_needed_usd > free_cash:
        res.fail(f"insufficient free cash: need {money(cash_needed_usd)} have {money(free_cash)}")

    if requirement.shares_required and shares_to_buy <= 0 and unencumbered_shares < requirement.shares_required:
        res.fail(
            f"covered call needs {requirement.shares_required} unencumbered shares, have {unencumbered_shares}"
        )

    if open_strategies_count >= profile.max_open_strategies:
        res.fail(f"max_open_strategies {profile.max_open_strategies} reached")

    eq = equity if equity > ZERO else Decimal("1")
    if strategy_type not in CAPITAL_SECURED_STRATEGIES:
        max_risk = money(eq * to_decimal(profile.max_risk_per_trade_pct) / 100)
        if requirement.max_loss_usd > max_risk:
            res.fail(f"max loss {requirement.max_loss_usd} exceeds max_risk_per_trade {max_risk}")
    collateral_pct = cash_reserved_after / eq * 100
    if collateral_pct > to_decimal(profile.max_collateral_pct):
        res.fail(f"collateral {money(collateral_pct)}% exceeds max_collateral_pct {profile.max_collateral_pct}%")

    if current_drawdown_pct >= to_decimal(profile.max_drawdown_pct):
        res.fail(f"account drawdown {current_drawdown_pct}% >= max_drawdown_pct {profile.max_drawdown_pct}%")
    if day_pnl_pct <= -to_decimal(profile.max_daily_loss_pct):
        res.fail(f"daily loss {day_pnl_pct}% breached max_daily_loss_pct {profile.max_daily_loss_pct}%")

    if profile.enforce_pdt_rule and today is not None and equity < to_decimal(profile.pdt_equity_threshold_usd):
        n = count_recent_day_trades(day_trade_dates, today)
        if n >= 3:
            res.fail(f"PDT rule: {n} day trades in 5 business days with equity below {profile.pdt_equity_threshold_usd}")
    return res
