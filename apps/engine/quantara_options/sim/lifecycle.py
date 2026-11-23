"""Option lifecycle: marking, expiration sweep, pin risk, early assignment, exercise & stock delivery.

Settlement conventions (all keep `equity = start + realized + unrealized` exact):

* Physical assignment / exercise closes the option leg at 0 for PnL (its premium is fully
  resolved into realized PnL) and moves the exercise economics into a stock trade at the strike.
  CSP assigned  -> buy `multiplier * qty` shares at strike, lot basis = strike,
                   wheel adjusted basis = strike - credit_per_share.
  CC assigned   -> sell the encumbered shares at strike, realizing stock + premium PnL.
* Multi-leg structures (spreads / iron condors) resolve ITM legs by *net cash-equivalent
  settlement* at intrinsic value (assignment + exercise + immediate stock liquidation at spot),
  so no unintended stock position is created; assignment fees and sale regulatory fees still apply.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone, tzinfo
from decimal import Decimal
from typing import Any, Optional

from quantara_options.contracts import (
    AccountRuleProfile,
    ExerciseStyle,
    Greeks,
    LegRole,
    OptionQuoteSnapshot,
    OptionRight,
    OptionSide,
    StrategyStatus,
    StrategyType,
    WheelState,
)
from quantara_options.math_pricing import american_crr_price, bsm_greeks, bsm_price
from quantara_options.sim.accounting import (
    LegStatus,
    SimAccountState,
    SimJournalEvent,
    SimLeg,
    SimStrategy,
    book_option_close,
    buy_stock,
    charge_fee,
    release_cash,
    sell_stock_from_lot,
    sell_unencumbered_shares,
)
from quantara_options.sim.fees_and_slippage import (
    DEFAULT_REG_FEES,
    ZERO,
    RegulatoryFeeSchedule,
    compute_assignment_fees,
    money,
    to_decimal,
)

ITM_THRESHOLD = Decimal("0.01")  # OCC exercise-by-exception threshold
MARK_PRECISION = Decimal("0.0001")
MARKET_OPEN = time(9, 30)
MARKET_CLOSE = time(16, 0)
DEFAULT_RATE = 0.045
DEFAULT_IV = 0.30

try:
    from zoneinfo import ZoneInfo

    MARKET_TZ: Optional[tzinfo] = ZoneInfo("America/New_York")
except Exception:  # pragma: no cover - tzdata missing
    MARKET_TZ = None


def _nth_sunday(year: int, month: int, n: int) -> date:
    d = date(year, month, 1)
    d += timedelta(days=(6 - d.weekday()) % 7)
    return d + timedelta(weeks=n - 1)


def _fallback_et_offset(now_utc: datetime) -> timedelta:
    y = now_utc.year
    dst_start = datetime.combine(_nth_sunday(y, 3, 2), time(7, 0), tzinfo=timezone.utc)
    dst_end = datetime.combine(_nth_sunday(y, 11, 1), time(6, 0), tzinfo=timezone.utc)
    return timedelta(hours=-4) if dst_start <= now_utc < dst_end else timedelta(hours=-5)


def ensure_aware(now: Optional[datetime]) -> datetime:
    if now is None:
        return datetime.now(timezone.utc)
    return now if now.tzinfo is not None else now.replace(tzinfo=timezone.utc)


def to_market_time(now: datetime) -> datetime:
    now = ensure_aware(now)
    if MARKET_TZ is not None:
        return now.astimezone(MARKET_TZ)
    utc = now.astimezone(timezone.utc)
    return utc.astimezone(timezone(_fallback_et_offset(utc)))


def market_date(now: datetime) -> date:
    return to_market_time(now).date()


def market_close_utc(day: date) -> datetime:
    if MARKET_TZ is not None:
        return datetime.combine(day, MARKET_CLOSE, tzinfo=MARKET_TZ).astimezone(timezone.utc)
    guess = datetime.combine(day, time(21, 0), tzinfo=timezone.utc)
    return datetime.combine(day, MARKET_CLOSE, tzinfo=timezone(_fallback_et_offset(guess))).astimezone(timezone.utc)


def calendar_dte(expiration: date, now: datetime) -> int:
    return (expiration - market_date(now)).days


def minutes_to_expiry_close(expiration: date, now: datetime) -> float:
    return (market_close_utc(expiration) - ensure_aware(now).astimezone(timezone.utc)).total_seconds() / 60.0


def time_to_expiry_years(expiration: date, now: datetime) -> float:
    return max(0.0, minutes_to_expiry_close(expiration, now)) / (365.0 * 24 * 60)


def expiration_due(expiration: date, now: datetime) -> bool:
    return minutes_to_expiry_close(expiration, now) <= 0


def intrinsic_value(right: OptionRight, strike: Decimal, spot: Decimal) -> Decimal:
    return max(ZERO, spot - strike) if right == OptionRight.CALL else max(ZERO, strike - spot)


# ---------------------------------------------------------------------------
# Marking
# ---------------------------------------------------------------------------


@dataclass
class LegMark:
    mark: Decimal
    bid: Optional[Decimal]
    ask: Optional[Decimal]
    estimated: bool
    source: str  # quote_mid | quote_side | quote_mid_field | last_trade | model_bsm | held
    implied_volatility: Optional[float]
    greeks: Greeks
    warnings: list[str] = field(default_factory=list)


def model_price(leg: SimLeg, *, spot: Decimal, now: datetime, iv: float, rate: float = DEFAULT_RATE) -> Decimal:
    if leg.strike is None or leg.right is None or leg.expiration_date is None:
        return ZERO
    t = time_to_expiry_years(leg.expiration_date, now)
    pricer = american_crr_price if leg.exercise_style == ExerciseStyle.AMERICAN else bsm_price
    px = pricer(spot=float(spot), strike=float(leg.strike), t_years=t, rate=rate, sigma=iv, right=leg.right)
    return max(money(px), leg.intrinsic(spot))


def mark_leg(
    leg: SimLeg,
    quote: Optional[OptionQuoteSnapshot],
    spot: Optional[Decimal],
    now: datetime,
    *,
    policy: str = "mid",
    default_iv: float = DEFAULT_IV,
    rate: float = DEFAULT_RATE,
) -> LegMark:
    """Mark one option leg. Missing/invalid quotes fall back to a model price labeled `estimated`."""
    warnings: list[str] = []
    if spot is None and quote is not None:
        spot = to_decimal(quote.underlying_price)
    iv = None
    if quote is not None and quote.implied_volatility:
        iv = float(quote.implied_volatility)
    iv_for_model = iv or leg.implied_volatility or leg.entry_implied_volatility or default_iv

    bid = to_decimal(quote.bid) if quote is not None else None
    ask = to_decimal(quote.ask) if quote is not None else None
    mark: Optional[Decimal] = None
    estimated = False
    source = "held"

    if quote is not None and bid is not None and ask is not None and ask > ZERO and ZERO <= bid <= ask:
        if policy == "liquidation":
            mark = bid if leg.side == OptionSide.LONG else ask
            source = "quote_side"
        else:
            mark = (bid + ask) / 2
            source = "quote_mid"
        if quote.is_stale:
            warnings.append("stale_quote_mark")
    elif quote is not None and quote.mid is not None and to_decimal(quote.mid) >= ZERO:
        mark = to_decimal(quote.mid)
        source = "quote_mid_field"
        warnings.append("one_sided_or_crossed_quote")
    elif quote is not None and quote.last_trade is not None and not quote.is_stale:
        mark = to_decimal(quote.last_trade)
        source = "last_trade"
        estimated = True
        warnings.append("last_trade_mark")

    if mark is None:
        if spot is not None and spot > ZERO:
            mark = model_price(leg, spot=spot, now=now, iv=iv_for_model, rate=rate)
            source = "model_bsm"
            estimated = True
            warnings.append("estimated_mark_model_fallback")
        else:
            mark = leg.current_mark
            source = "held"
            estimated = True
            warnings.append("no_quote_no_spot_mark_held")

    if spot is not None and spot > ZERO and leg.exercise_style == ExerciseStyle.AMERICAN:
        intr = leg.intrinsic(spot)
        if mark < intr:
            mark = intr
            warnings.append("mark_clamped_to_intrinsic")

    greeks = quote.greeks if (quote is not None and quote.greeks.delta is not None) else leg.greeks
    if (quote is None or quote.greeks.delta is None) and spot is not None and spot > ZERO and leg.strike and leg.right:
        greeks = bsm_greeks(
            spot=float(spot),
            strike=float(leg.strike),
            t_years=time_to_expiry_years(leg.expiration_date, now) if leg.expiration_date else 0.0,
            rate=rate,
            sigma=iv_for_model,
            right=leg.right,
            computed_locally=True,
        )
    return LegMark(
        mark=mark.quantize(MARK_PRECISION),
        bid=bid,
        ask=ask,
        estimated=estimated,
        source=source,
        implied_volatility=iv or leg.implied_volatility,
        greeks=greeks,
        warnings=warnings,
    )


def apply_mark(leg: SimLeg, m: LegMark) -> None:
    leg.current_mark = m.mark
    leg.bid = m.bid
    leg.ask = m.ask
    leg.mark_is_estimated = m.estimated
    leg.implied_volatility = m.implied_volatility
    leg.greeks = m.greeks


def extrinsic_value(leg: SimLeg, spot: Decimal) -> Decimal:
    return max(ZERO, leg.current_mark - leg.intrinsic(spot))


# ---------------------------------------------------------------------------
# Risk detectors
# ---------------------------------------------------------------------------


def detect_pin_risk(
    strategy: SimStrategy,
    spot: Optional[Decimal],
    now: datetime,
    *,
    threshold_pct: Decimal = Decimal("0.005"),
    max_dte: int = 2,
) -> bool:
    if spot is None or spot <= ZERO or calendar_dte(strategy.expiration_date, now) > max_dte:
        return False
    return any(abs(spot - k) / k <= threshold_pct for k in strategy.short_strikes() if k > ZERO)


def any_leg_itm(strategy: SimStrategy, spot: Optional[Decimal]) -> bool:
    if spot is None:
        return False
    return any(l.intrinsic(spot) >= ITM_THRESHOLD for l in strategy.open_option_legs)


@dataclass
class EarlyAssignmentSignal:
    leg_id: str
    reason: str
    execute: bool
    extrinsic: Decimal
    dividend: Optional[Decimal] = None


def evaluate_early_assignment(
    leg: SimLeg,
    *,
    spot: Optional[Decimal],
    now: datetime,
    ex_div_today: bool = False,
    dividend: Optional[Decimal] = None,
    deep_itm_extrinsic: Decimal = Decimal("0.05"),
    unknown_dividend_extrinsic: Decimal = Decimal("0.10"),
    put_delta_threshold: float = -0.90,
    call_delta_threshold: float = 0.90,
    near_expiry_dte: int = 1,
) -> Optional[EarlyAssignmentSignal]:
    """Heuristic early-assignment model for short American legs (warning or execution)."""
    if (
        spot is None
        or not leg.is_open
        or not leg.is_option
        or leg.side != OptionSide.SHORT
        or leg.exercise_style != ExerciseStyle.AMERICAN
        or leg.expiration_date is None
    ):
        return None
    intr = leg.intrinsic(spot)
    if intr < ITM_THRESHOLD:
        return None
    extr = extrinsic_value(leg, spot)
    delta = leg.greeks.delta
    dte = calendar_dte(leg.expiration_date, now)

    if leg.right == OptionRight.CALL:
        if ex_div_today:
            if dividend is not None and dividend > extr:
                return EarlyAssignmentSignal(leg.leg_id, "ex_dividend_dividend_exceeds_extrinsic", True, extr, dividend)
            if dividend is None and extr < unknown_dividend_extrinsic:
                return EarlyAssignmentSignal(leg.leg_id, "ex_dividend_low_extrinsic", True, extr, None)
        deep = delta is not None and delta >= call_delta_threshold
        if dte <= near_expiry_dte and extr < deep_itm_extrinsic and (deep or delta is None):
            return EarlyAssignmentSignal(leg.leg_id, "deep_itm_call_near_expiry", True, extr)
        if extr < deep_itm_extrinsic * 2:
            return EarlyAssignmentSignal(leg.leg_id, "low_extrinsic_call_watch", False, extr, dividend)
        return None

    deep_put = delta is not None and delta < put_delta_threshold
    if delta is None and leg.strike:
        deep_put = intr / leg.strike >= Decimal("0.10")
    if extr < deep_itm_extrinsic and deep_put:
        return EarlyAssignmentSignal(leg.leg_id, "deep_itm_put_no_extrinsic", True, extr)
    if extr < deep_itm_extrinsic * 2 and (deep_put or (delta is not None and delta < -0.80)):
        return EarlyAssignmentSignal(leg.leg_id, "deep_itm_put_watch", False, extr)
    return None


@dataclass(frozen=True)
class ExitPolicy:
    """v2 defaults: credit stops keyed to max_loss, not tiny post-fill credit multiples.

    Soft breaches require `soft_confirm_cycles` consecutive mark hits before auto-close.
    Emergency breaches close immediately. Capital-secured structures ride by default.
    """

    credit_stop_multiple: Optional[Decimal] = None  # deprecated; kept for override compatibility
    debit_stop_fraction: Optional[Decimal] = Decimal("0.35")
    credit_soft_stop_of_max_loss: Decimal = Decimal("0.45")
    credit_emergency_of_max_loss: Decimal = Decimal("0.75")
    soft_confirm_cycles: int = 2
    max_loss_trigger_pct: Decimal = Decimal("0.98")
    stop_capital_secured: bool = False
    capital_secured_emergency_of_max_loss: Optional[Decimal] = Decimal("0.90")
    require_fresh_marks_for_soft: bool = True


def evaluate_exit_breach(strategy: SimStrategy, policy: ExitPolicy = ExitPolicy()) -> tuple[Optional[str], dict[str, Any]]:
    """Detect stop / max-loss / take-profit breaches on the current marks.

    Returns reason codes:
      - take_profit / max_loss_breach / stop_loss_emergency → close immediately
      - stop_loss_pending → soft breach (caller increments confirm counter; do not close yet)
      - stop_loss_breach → soft breach confirmed across cycles → close
    """
    from quantara_options.policy import EXIT_THRESHOLDS, POLICY_VERSION

    pnl = strategy.option_unrealized()
    meta = strategy.metadata or {}
    info: dict[str, Any] = {
        "unrealized_pnl": float(pnl),
        "policy_version": POLICY_VERSION,
        "exit_policy": "v2_max_loss_anchored",
    }

    tp = to_decimal(meta.get("take_profit_usd"))
    if tp is not None and tp > ZERO and pnl >= tp:
        return "take_profit", {**info, "take_profit_usd": float(tp)}

    capital_secured = strategy.strategy_type in (
        StrategyType.CASH_SECURED_PUT, StrategyType.COVERED_CALL, StrategyType.WHEEL
    )
    max_loss = strategy.max_loss_usd if strategy.max_loss_usd and strategy.max_loss_usd > ZERO else ZERO
    entry = strategy.net_entry_cash_flow
    loss_usd = money(-pnl) if pnl < ZERO else ZERO

    # Explicit per-trade override still wins.
    stop = to_decimal(meta.get("stop_loss_usd"))
    emergency = to_decimal(meta.get("stop_loss_emergency_usd"))

    if stop is None and not capital_secured:
        if entry < ZERO and policy.debit_stop_fraction is not None:
            # Long / debit structures: fraction of debit paid.
            stop = money(-entry * policy.debit_stop_fraction)
        elif entry > ZERO and max_loss > ZERO:
            # Credit structures: fraction of defined max loss (width - credit), not 2× tiny credit.
            soft_pct = to_decimal(meta.get("credit_soft_stop_of_max_loss")) or policy.credit_soft_stop_of_max_loss
            stop = money(max_loss * soft_pct)
            # Legacy override: if caller still sets credit_stop_multiple, use max(soft, multiple*credit)
            # but never below soft-of-max-loss floor when max_loss is known.
            if policy.credit_stop_multiple is not None:
                stop = max(stop, money(entry * policy.credit_stop_multiple))
        elif entry > ZERO and policy.credit_stop_multiple is not None:
            stop = money(entry * policy.credit_stop_multiple)

    if emergency is None and max_loss > ZERO:
        if capital_secured and policy.capital_secured_emergency_of_max_loss is not None:
            emergency = money(max_loss * policy.capital_secured_emergency_of_max_loss)
        elif not capital_secured:
            emergency = money(max_loss * policy.credit_emergency_of_max_loss)

    if stop is not None and max_loss > ZERO and not capital_secured:
        stop = min(stop, max_loss)

    # Stale / estimated marks: do not fire soft stops (emergency still allowed).
    marks_estimated = bool(getattr(strategy, "mark_is_estimated", lambda: False)())
    if callable(getattr(strategy, "mark_is_estimated", None)):
        try:
            marks_estimated = bool(strategy.mark_is_estimated())
        except Exception:
            marks_estimated = False
    info["marks_estimated"] = marks_estimated

    if emergency is not None and emergency > ZERO and loss_usd >= emergency:
        return "stop_loss_emergency", {
            **info,
            "stop_loss_usd": float(emergency),
            "gap_through_stop_usd": float(money(loss_usd - emergency)),
            "urgency": "immediate",
        }

    if not capital_secured and max_loss > ZERO and loss_usd >= money(max_loss * policy.max_loss_trigger_pct):
        return "max_loss_breach", {**info, "max_loss_usd": float(max_loss), "urgency": "immediate"}

    if stop is not None and stop > ZERO and loss_usd >= stop:
        if policy.require_fresh_marks_for_soft and marks_estimated:
            return None, {**info, "soft_blocked_estimated_marks": True, "stop_loss_usd": float(stop)}
        confirm_needed = max(1, int(policy.soft_confirm_cycles))
        prior = int(meta.get("soft_stop_confirm_count") or 0)
        info.update({
            "stop_loss_usd": float(stop),
            "gap_through_stop_usd": float(money(loss_usd - stop)),
            "soft_stop_confirm_count": prior + 1,
            "soft_confirm_needed": confirm_needed,
        })
        if prior + 1 >= confirm_needed:
            return "stop_loss_breach", {**info, "urgency": "confirmed"}
        return "stop_loss_pending", {**info, "urgency": "soft"}

    # Clear pending counter when no longer breached (caller should persist).
    if int(meta.get("soft_stop_confirm_count") or 0) > 0:
        info["soft_stop_confirm_count"] = 0
        info["soft_stop_cleared"] = True
    return None, info


def detect_gap(prev_spot: Optional[Decimal], spot: Optional[Decimal], threshold_pct: Decimal = Decimal("0.03")) -> Optional[Decimal]:
    if prev_spot is None or spot is None or prev_spot <= ZERO:
        return None
    move = (spot - prev_spot) / prev_spot
    return money(move * 100) if abs(move) >= threshold_pct else None


def detect_vol_shock(entry_iv: Optional[float], current_iv: Optional[float], rel_threshold: float = 0.5) -> bool:
    if not entry_iv or not current_iv or entry_iv <= 0 or math.isnan(current_iv):
        return False
    return abs(current_iv - entry_iv) / entry_iv >= rel_threshold


# ---------------------------------------------------------------------------
# Resolution handlers
# ---------------------------------------------------------------------------

WHEEL_LIKE = frozenset({StrategyType.WHEEL, StrategyType.CASH_SECURED_PUT, StrategyType.COVERED_CALL})
PIN_GUARD_STRATEGIES = frozenset(
    {
        StrategyType.LONG_CALL,
        StrategyType.LONG_PUT,
        StrategyType.BULL_CALL_SPREAD,
        StrategyType.BEAR_PUT_SPREAD,
        StrategyType.BULL_PUT_CREDIT_SPREAD,
        StrategyType.BEAR_CALL_CREDIT_SPREAD,
        StrategyType.IRON_CONDOR,
    }
)


@dataclass
class LifecycleContext:
    account: SimAccountState
    profile: AccountRuleProfile
    now: datetime
    events: list[SimJournalEvent] = field(default_factory=list)
    schedule: RegulatoryFeeSchedule = DEFAULT_REG_FEES

    def emit(self, strategy: Optional[SimStrategy], event_type: str, description: str, **kw: Any) -> SimJournalEvent:
        ev = SimJournalEvent(
            account_slug=self.account.slug,
            event_type=event_type,
            underlying_symbol=strategy.underlying_symbol if strategy else kw.pop("symbol", ""),
            strategy_type=strategy.strategy_type.value if strategy else kw.pop("strategy_type", ""),
            strategy_group_id=strategy.strategy_id if strategy else None,
            description=description,
            created_at=self.now,
            cash_delta=money(kw.pop("cash_delta", ZERO)),
            realized_pnl_delta=money(kw.pop("realized_pnl_delta", ZERO)),
            collateral_delta=money(kw.pop("collateral_delta", ZERO)),
            fee_usd=money(kw.pop("fee_usd", ZERO)),
            metadata=kw.pop("metadata", {}) or {},
        )
        self.events.append(ev)
        return ev


def is_wheel_participant(account: SimAccountState, strategy: SimStrategy) -> bool:
    return strategy.strategy_type == StrategyType.WHEEL or (
        strategy.strategy_type in WHEEL_LIKE and strategy.underlying_symbol in account.wheel_states
    )


def set_wheel_state(account: SimAccountState, strategy: SimStrategy, state: WheelState) -> None:
    if is_wheel_participant(account, strategy):
        account.wheel_states[strategy.underlying_symbol] = state.value
        strategy.wheel_state = state.value


def wheel_state_after_resolution(account: SimAccountState, strategy: SimStrategy) -> None:
    if not is_wheel_participant(account, strategy):
        return
    holding = account.unencumbered_shares(strategy.underlying_symbol) + sum(
        l.encumbered_shares for l in account.lots_for(strategy.underlying_symbol)
    )
    set_wheel_state(account, strategy, WheelState.HOLDING_SHARES if holding > 0 else WheelState.IDLE)


def expire_leg_worthless(ctx: LifecycleContext, strategy: SimStrategy, leg: SimLeg) -> None:
    _, realized = book_option_close(ctx.account, strategy, leg, ZERO, status=LegStatus.EXPIRED, cash_settled=False)
    ctx.emit(
        strategy,
        "EXPIRE",
        f"{leg.occ_symbol or leg.leg_role.value} expired worthless",
        realized_pnl_delta=realized,
        metadata={"leg_id": leg.leg_id, "side": leg.side.value, "strike": str(leg.strike)},
    )


def _assignment_fee(ctx: LifecycleContext, strategy: SimStrategy, *, sale_proceeds: Decimal = ZERO, shares_sold: int = 0) -> tuple[Decimal, Decimal]:
    fees = compute_assignment_fees(
        ctx.profile, events=1, stock_sale_proceeds_usd=sale_proceeds, shares_sold=shares_sold, schedule=ctx.schedule
    )
    opt_fee = charge_fee(ctx.account, strategy, fees.assignment)
    stock_fee = charge_fee(ctx.account, strategy, fees.regulatory, stock=True)
    return opt_fee, stock_fee


def assign_short_put_physical(ctx: LifecycleContext, strategy: SimStrategy, leg: SimLeg, spot: Decimal) -> None:
    """CSP / wheel put assigned: buy `multiplier * qty` shares at strike out of the reserved cash."""
    assert leg.strike is not None
    shares = leg.units
    credit_ps = strategy.credit_per_share()
    _, realized = book_option_close(ctx.account, strategy, leg, ZERO, status=LegStatus.ASSIGNED, cash_settled=False)
    released = release_cash(ctx.account, strategy)
    lot = buy_stock(
        ctx.account,
        symbol=strategy.underlying_symbol,
        shares=shares,
        price=leg.strike,
        source="assignment",
        strategy_id=strategy.strategy_id,
        now=ctx.now,
        premium_credit_per_share=credit_ps,
    )
    lot.last_price = spot
    opt_fee, _ = _assignment_fee(ctx, strategy)
    cost = money(leg.strike * shares)
    ctx.emit(
        strategy,
        "ASSIGN",
        f"Short put {leg.strike} assigned: premium resolved",
        realized_pnl_delta=realized - opt_fee,
        fee_usd=opt_fee,
        metadata={"leg_id": leg.leg_id, "spot": str(spot), "settlement": "physical"},
    )
    ctx.emit(
        strategy,
        "STOCK_DELIVERY",
        f"Bought {shares} {strategy.underlying_symbol} @ {leg.strike} (adjusted basis {money(lot.adjusted_cost_basis_per_share)})",
        cash_delta=-cost,
        collateral_delta=-released,
        metadata={
            "lot_id": lot.lot_id,
            "shares": shares,
            "price": str(leg.strike),
            "adjusted_cost_basis_per_share": str(money(lot.adjusted_cost_basis_per_share)),
        },
    )
    set_wheel_state(ctx.account, strategy, WheelState.HOLDING_SHARES)


def assign_short_call_physical(ctx: LifecycleContext, strategy: SimStrategy, leg: SimLeg, spot: Decimal) -> None:
    """Covered call assigned (called away): deliver encumbered shares at strike."""
    assert leg.strike is not None
    to_deliver = leg.units
    _, realized_opt = book_option_close(ctx.account, strategy, leg, ZERO, status=LegStatus.ASSIGNED, cash_settled=False)
    proceeds_total = ZERO
    stock_realized = ZERO
    delivered = 0
    for lot_id, n in list(strategy.encumbered_lots.items()):
        if to_deliver <= 0:
            break
        lot = ctx.account.find_lot(lot_id)
        if lot is None:
            strategy.encumbered_lots.pop(lot_id, None)
            continue
        take = min(n, to_deliver, lot.shares)
        cash, realized = sell_stock_from_lot(
            ctx.account, lot, shares=take, price=leg.strike, strategy=strategy, from_encumbered=True
        )
        proceeds_total += cash
        stock_realized += realized
        delivered += take
        to_deliver -= take
        left = n - take
        if left > 0:
            strategy.encumbered_lots[lot_id] = left
        else:
            strategy.encumbered_lots.pop(lot_id, None)

    shortfall_note = None
    if to_deliver > 0:
        # Uncovered remainder: settle at intrinsic instead of creating a short stock position.
        value = money(leg.intrinsic(spot) * to_deliver)
        ctx.account.cash_balance = money(ctx.account.cash_balance - value)
        ctx.account.realized_pnl = money(ctx.account.realized_pnl - value)
        strategy.option_realized_gross = money(strategy.option_realized_gross - value)
        realized_opt -= value
        shortfall_note = f"{to_deliver} uncovered shares cash-settled at intrinsic"

    opt_fee, stock_fee = _assignment_fee(ctx, strategy, sale_proceeds=proceeds_total, shares_sold=delivered)
    ctx.emit(
        strategy,
        "ASSIGN",
        f"Covered call {leg.strike} assigned (called away)",
        realized_pnl_delta=realized_opt - opt_fee,
        fee_usd=opt_fee,
        metadata={"leg_id": leg.leg_id, "spot": str(spot), "settlement": "physical", "note": shortfall_note},
    )
    ctx.emit(
        strategy,
        "STOCK_DELIVERY",
        f"Delivered {delivered} {strategy.underlying_symbol} @ {leg.strike}",
        cash_delta=proceeds_total,
        realized_pnl_delta=stock_realized - stock_fee,
        fee_usd=stock_fee,
        metadata={"shares": delivered, "price": str(leg.strike), "stock_realized_gross": str(stock_realized)},
    )


def net_settle_leg(ctx: LifecycleContext, strategy: SimStrategy, leg: SimLeg, spot: Decimal, *, event_type: str) -> None:
    """Cash-equivalent settlement at intrinsic (assignment/exercise + immediate stock liquidation at spot)."""
    assert leg.strike is not None
    intr = leg.intrinsic(spot)
    status = LegStatus.ASSIGNED if leg.side == OptionSide.SHORT else LegStatus.EXERCISED
    cash, realized = book_option_close(ctx.account, strategy, leg, intr, status=status, cash_settled=True)
    shares = leg.units
    if leg.right == OptionRight.PUT:
        sale_px = spot if leg.side == OptionSide.SHORT else leg.strike
    else:
        sale_px = leg.strike if leg.side == OptionSide.SHORT else spot
    opt_fee, stock_fee = _assignment_fee(ctx, strategy, sale_proceeds=money(sale_px * shares), shares_sold=shares)
    ctx.emit(
        strategy,
        event_type,
        f"{leg.side.value} {leg.right.value if leg.right else ''} {leg.strike} settled at intrinsic {money(intr)}",
        cash_delta=cash,
        realized_pnl_delta=realized - opt_fee - stock_fee,
        fee_usd=opt_fee + stock_fee,
        metadata={"leg_id": leg.leg_id, "spot": str(spot), "settlement": "net_cash_equivalent"},
    )


def exercise_long_leg(ctx: LifecycleContext, strategy: SimStrategy, leg: SimLeg, spot: Decimal, *, prefer_physical: bool = True) -> None:
    """Long ITM at expiry: physical delivery when funded/covered, otherwise net settlement at intrinsic."""
    assert leg.strike is not None
    shares = leg.units
    acct = ctx.account
    if prefer_physical and leg.right == OptionRight.CALL:
        cost = money(leg.strike * shares)
        if acct.free_cash - cost - to_decimal(ctx.profile.assignment_fee_usd) >= ZERO:
            _, realized = book_option_close(acct, strategy, leg, ZERO, status=LegStatus.EXERCISED, cash_settled=False)
            lot = buy_stock(
                acct, symbol=strategy.underlying_symbol, shares=shares, price=leg.strike,
                source="exercise", strategy_id=strategy.strategy_id, now=ctx.now,
            )
            lot.last_price = spot
            opt_fee, _ = _assignment_fee(ctx, strategy)
            ctx.emit(
                strategy, "EXERCISE", f"Exercised long call {leg.strike}: bought {shares} shares",
                cash_delta=-cost, realized_pnl_delta=realized - opt_fee, fee_usd=opt_fee,
                metadata={"leg_id": leg.leg_id, "lot_id": lot.lot_id, "settlement": "physical"},
            )
            return
    if prefer_physical and leg.right == OptionRight.PUT and acct.unencumbered_shares(strategy.underlying_symbol) >= shares:
        _, realized = book_option_close(acct, strategy, leg, ZERO, status=LegStatus.EXERCISED, cash_settled=False)
        cash, stock_realized, sold = sell_unencumbered_shares(
            acct, symbol=strategy.underlying_symbol, shares=shares, price=leg.strike, strategy=strategy
        )
        opt_fee, stock_fee = _assignment_fee(ctx, strategy, sale_proceeds=cash, shares_sold=sold)
        ctx.emit(
            strategy, "EXERCISE", f"Exercised long put {leg.strike}: sold {sold} shares",
            cash_delta=cash, realized_pnl_delta=realized + stock_realized - opt_fee - stock_fee,
            fee_usd=opt_fee + stock_fee, metadata={"leg_id": leg.leg_id, "settlement": "physical"},
        )
        return
    net_settle_leg(ctx, strategy, leg, spot, event_type="EXERCISE")


def credit_premium_to_lots(account: SimAccountState, strategy: SimStrategy) -> None:
    """Wheel adjusted basis: a covered call resolved without assignment credits its net premium to its lots."""
    total = sum(strategy.encumbered_lots.values())
    if total <= 0 or strategy.option_realized_gross == ZERO:
        return
    per_share = strategy.option_realized_gross / total
    for lot_id in strategy.encumbered_lots:
        lot = account.find_lot(lot_id)
        if lot is not None:
            lot.premium_credit_per_share += per_share


def resolve_at_expiration(ctx: LifecycleContext, strategy: SimStrategy, spot: Decimal) -> StrategyStatus:
    """Resolve every open option leg at expiration settlement price `spot`."""
    assigned = False
    st = strategy.strategy_type
    for leg in list(strategy.open_option_legs):
        if leg.intrinsic(spot) < ITM_THRESHOLD:
            expire_leg_worthless(ctx, strategy, leg)
            continue
        if leg.side == OptionSide.SHORT:
            assigned = True
            if leg.right == OptionRight.PUT and st in (StrategyType.CASH_SECURED_PUT, StrategyType.WHEEL):
                assign_short_put_physical(ctx, strategy, leg, spot)
            elif leg.right == OptionRight.CALL and st in (StrategyType.COVERED_CALL, StrategyType.WHEEL):
                assign_short_call_physical(ctx, strategy, leg, spot)
            else:
                net_settle_leg(ctx, strategy, leg, spot, event_type="ASSIGN")
        elif st in (StrategyType.LONG_CALL, StrategyType.LONG_PUT):
            exercise_long_leg(ctx, strategy, leg, spot)
        else:
            net_settle_leg(ctx, strategy, leg, spot, event_type="EXERCISE")
    return StrategyStatus.ASSIGNED if assigned else StrategyStatus.EXPIRED


def has_stock_leg(strategy: SimStrategy) -> bool:
    return any(l.leg_role == LegRole.UNDERLYING_STOCK for l in strategy.legs)
