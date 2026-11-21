"""Deterministic, SYNTHETIC option-chain fixtures for QUANTARA Options tests.

IMPORTANT HONESTY NOTE
----------------------
Everything in this module is generated from Black-Scholes-Merton math with fixed
inputs.  It is tagged ``DataFeedTier.FIXTURE`` / ``is_indicative=False`` and carries the
``synthetic_fixture_not_live_market`` warning.  Passing tests built on these fixtures prove
simulation / accounting / risk-guard logic ONLY.  They say NOTHING about whether live
Alpaca / OPRA data is available or accurate.

Scenarios provided
------------------
* ``build_chain``                      – liquid ETF/stock chains (SPY, AAPL, PLTR), 3 expirations
                                          (5 / 21 / 35 DTE), full strike ladders, Bid/Ask/Vol/OI/IV/Greeks.
* ``build_multiplier_chain``           – non-100 multiplier contracts (10 mini / 50 custom).
* ``build_crash_scenario``             – gap-down -12 % with +20 vol-point IV spike.
* ``build_ex_dividend_scenario``       – ITM American short call with dividend > extrinsic value.
* ``build_pin_risk_scenario``          – expiration day 15:40 ET, spot $0.20 from short strike.
* ``build_closed_market_chain``        – stale timestamps, zero bids, missing quotes, missing Greeks.
* ``make_candidate`` / ``candidate_for`` – grounded ``StrategyCandidate`` builders for all 9 strategies.
* ``StaticChainProvider``              – minimal ``OptionsDataProvider`` over prebuilt chains.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Iterable, Optional

import pytest

from quantara_options.contracts import (
    AccountRuleProfile,
    DataFeedTier,
    ExerciseStyle,
    Greeks,
    LegPlan,
    LegRole,
    OptionChainResponse,
    OptionContractSpec,
    OptionQuoteSnapshot,
    OptionRight,
    OptionSide,
    StrategyCandidate,
    StrategyType,
)
from quantara_options.math_pricing import bsm_greeks, bsm_price

# --------------------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------------------

FIXTURE_WARNING = "synthetic_fixture_not_live_market"
RISK_FREE_RATE = 0.045

# June / July 2026 is US daylight time (EDT = UTC-4) – fixed offset keeps tests tzdata-free.
ET = timezone(timedelta(hours=-4), name="ET")

# Wednesday 2026-06-10 10:30 ET – a regular liquid session.
AS_OF = datetime(2026, 6, 10, 10, 30, tzinfo=ET)
DTE_BUCKETS: tuple[int, ...] = (5, 21, 35)


def expiration_for_dte(dte: int, as_of: datetime = AS_OF) -> date:
    return as_of.date() + timedelta(days=dte)


EXP_5 = expiration_for_dte(5)    # Mon 2026-06-15
EXP_21 = expiration_for_dte(21)  # Wed 2026-07-01
EXP_35 = expiration_for_dte(35)  # Wed 2026-07-15


@dataclass(frozen=True)
class UnderlyingSpec:
    symbol: str
    spot: float
    strike_step: float
    base_iv: float
    div_yield: float
    span_pct: float
    base_open_interest: int


UNDERLYINGS: dict[str, UnderlyingSpec] = {
    "SPY": UnderlyingSpec("SPY", 695.00, 5.0, 0.16, 0.012, 0.12, 20_000),
    "AAPL": UnderlyingSpec("AAPL", 225.00, 5.0, 0.27, 0.005, 0.20, 8_000),
    "PLTR": UnderlyingSpec("PLTR", 42.00, 1.0, 0.55, 0.0, 0.20, 12_000),
}


def _d(value: float | int | str | Decimal, places: str = "0.01") -> Decimal:
    return Decimal(str(value)).quantize(Decimal(places), rounding=ROUND_HALF_UP)


def to_et(dt: datetime) -> datetime:
    return dt.astimezone(ET)


# --------------------------------------------------------------------------------------
# OCC symbols / ladders / time-to-expiry
# --------------------------------------------------------------------------------------

def occ_symbol(root: str, expiration: date, right: OptionRight, strike: float | Decimal) -> str:
    cp = "C" if right == OptionRight.CALL else "P"
    strike_milli = int((Decimal(str(strike)) * 1000).to_integral_value())
    return f"{root}{expiration:%y%m%d}{cp}{strike_milli:08d}"


def strike_ladder(center: float, step: float, span_pct: float) -> list[float]:
    lo = math.floor(center * (1.0 - span_pct) / step) * step
    hi = math.ceil(center * (1.0 + span_pct) / step) * step
    out: list[float] = []
    k = lo
    while k <= hi + 1e-9:
        out.append(round(k, 2))
        k += step
    return out


def t_years_to_expiry(expiration: date, as_of: datetime) -> float:
    expiry_dt = datetime.combine(expiration, time(16, 0), tzinfo=ET)
    seconds = (expiry_dt - as_of).total_seconds()
    return max(0.0, seconds) / (365.0 * 86400.0)


def smile_iv(spec: UnderlyingSpec, strike: float, ladder_spot: float, dte: int, iv_shift: float) -> float:
    """Deterministic put-skewed smile with a mild term structure (vol points as decimals)."""
    moneyness = (strike - ladder_spot) / ladder_spot
    if moneyness < 0:
        skew = 0.9 * (-moneyness)       # downside skew
    else:
        skew = 0.25 * moneyness         # smaller upside skew
    term = {5: 0.02, 21: 0.0, 35: -0.01}.get(dte, 0.0)
    return max(0.05, spec.base_iv * (1.0 + skew) + term + iv_shift)


# --------------------------------------------------------------------------------------
# Quote construction
# --------------------------------------------------------------------------------------

def make_quote(
    *,
    spec: UnderlyingSpec,
    right: OptionRight,
    strike: float,
    expiration: date,
    spot: float,
    as_of: datetime,
    iv: float,
    multiplier: int = 100,
    root: Optional[str] = None,
    ladder_spot: Optional[float] = None,
    min_theo: float = 0.04,
    quote_timestamp: Optional[datetime] = None,
    dividend_yield: Optional[float] = None,
) -> Optional[OptionQuoteSnapshot]:
    """Build one BSM-consistent quote with realistic Bid/Ask; ``None`` if effectively worthless."""
    dy = spec.div_yield if dividend_yield is None else dividend_yield
    t = t_years_to_expiry(expiration, as_of)
    theo = bsm_price(
        spot=spot, strike=strike, t_years=t, rate=RISK_FREE_RATE, sigma=iv, right=right, div_yield=dy
    )
    if theo < min_theo:
        return None

    half = max(0.01, round(0.012 * theo + 0.01, 2))
    bid = max(0.0, round(theo - half, 2))
    ask = max(0.01, round(theo + half, 2))
    bid_d, ask_d = _d(bid), _d(ask)
    mid = _d((bid_d + ask_d) / 2, "0.0001")
    spread_pct = float((ask_d - bid_d) / mid) if mid > 0 else None

    ref = ladder_spot if ladder_spot is not None else spot
    moneyness = abs(strike / ref - 1.0)
    oi_factor = math.exp(-((moneyness / 0.06) ** 2))
    open_interest = int(spec.base_open_interest * oi_factor) + 25
    volume = max(1, int(open_interest * 0.12 * (0.5 + oi_factor)))

    greeks = bsm_greeks(
        spot=spot, strike=strike, t_years=t, rate=RISK_FREE_RATE, sigma=iv,
        right=right, div_yield=dy, computed_locally=False,
    )

    contract = OptionContractSpec(
        occ_symbol=occ_symbol(root or spec.symbol, expiration, right, strike),
        underlying_symbol=spec.symbol,
        right=right,
        strike=_d(strike, "0.0001"),
        expiration_date=expiration,
        multiplier=multiplier,
        exercise_style=ExerciseStyle.AMERICAN,
        tradable=True,
        root_symbol=root or spec.symbol,
        open_interest=open_interest,
        close_price=mid,
    )
    return OptionQuoteSnapshot(
        contract=contract,
        underlying_price=_d(spot),
        bid=bid_d,
        ask=ask_d,
        mid=mid,
        last_trade=mid,
        volume=volume,
        open_interest=open_interest,
        implied_volatility=round(iv, 4),
        greeks=greeks,
        quote_timestamp=quote_timestamp or as_of,
        snapshot_timestamp=as_of,
        feed_tier=DataFeedTier.FIXTURE,
        is_indicative=False,
        is_delayed_trades=False,
        is_stale=False,
        spread_pct=round(spread_pct, 6) if spread_pct is not None else None,
        data_warnings=[FIXTURE_WARNING],
    )


def build_chain(
    symbol: str = "SPY",
    *,
    spot: Optional[float] = None,
    ladder_spot: Optional[float] = None,
    as_of: datetime = AS_OF,
    dtes: Iterable[int] = DTE_BUCKETS,
    expirations: Optional[Iterable[date]] = None,
    iv_shift: float = 0.0,
    multiplier: int = 100,
    root: Optional[str] = None,
    span_pct: Optional[float] = None,
    min_theo: float = 0.04,
    market_open: bool = True,
    dividend_yield: Optional[float] = None,
    extra_warnings: Optional[list[str]] = None,
) -> OptionChainResponse:
    """Full deterministic chain for ``symbol`` (SPY / AAPL / PLTR)."""
    spec = UNDERLYINGS[symbol]
    px = spec.spot if spot is None else spot
    lspot = ladder_spot if ladder_spot is not None else px
    ladder = strike_ladder(lspot, spec.strike_step, span_pct if span_pct is not None else spec.span_pct)

    exp_list: list[tuple[int, date]] = []
    if expirations is not None:
        for exp in expirations:
            exp_list.append(((exp - as_of.date()).days, exp))
    else:
        for dte in dtes:
            exp_list.append((dte, expiration_for_dte(dte, as_of)))

    quotes: list[OptionQuoteSnapshot] = []
    for dte, exp in exp_list:
        for strike in ladder:
            iv = smile_iv(spec, strike, lspot, dte, iv_shift)
            for right in (OptionRight.PUT, OptionRight.CALL):
                q = make_quote(
                    spec=spec, right=right, strike=strike, expiration=exp, spot=px, as_of=as_of,
                    iv=iv, multiplier=multiplier, root=root, ladder_spot=lspot,
                    min_theo=min_theo, dividend_yield=dividend_yield,
                )
                if q is not None:
                    quotes.append(q)

    return OptionChainResponse(
        underlying_symbol=symbol,
        underlying_price=_d(px),
        as_of=as_of,
        market_open=market_open,
        feed_tier=DataFeedTier.FIXTURE,
        is_indicative=False,
        is_delayed_trades=False,
        is_stale=False,
        expirations=[exp for _, exp in exp_list],
        contracts=quotes,
        warnings=[FIXTURE_WARNING, *(extra_warnings or [])],
        provider_name="fixture",
    )


# --------------------------------------------------------------------------------------
# Chain lookup helpers
# --------------------------------------------------------------------------------------

def quotes_by_occ(chain: OptionChainResponse) -> dict[str, OptionQuoteSnapshot]:
    return {q.contract.occ_symbol: q for q in chain.contracts}


def strikes_for(chain: OptionChainResponse, expiration: date, right: OptionRight) -> list[Decimal]:
    return sorted(
        {
            q.contract.strike
            for q in chain.contracts
            if q.contract.expiration_date == expiration and q.contract.right == right
        }
    )


def find_quote(
    chain: OptionChainResponse, expiration: date, right: OptionRight, strike: float | Decimal
) -> OptionQuoteSnapshot:
    target = Decimal(str(strike))
    for q in chain.contracts:
        c = q.contract
        if c.expiration_date == expiration and c.right == right and c.strike == target:
            return q
    raise KeyError(f"no fixture quote {right.value} {strike} exp {expiration}")


def nearest_strike(chain: OptionChainResponse, expiration: date, right: OptionRight, target: float) -> Decimal:
    strikes = strikes_for(chain, expiration, right)
    return min(strikes, key=lambda k: abs(float(k) - target))


def strike_at_or_below(chain: OptionChainResponse, expiration: date, right: OptionRight, target: float) -> Decimal:
    strikes = [k for k in strikes_for(chain, expiration, right) if float(k) <= target]
    return max(strikes)


def strike_at_or_above(chain: OptionChainResponse, expiration: date, right: OptionRight, target: float) -> Decimal:
    strikes = [k for k in strikes_for(chain, expiration, right) if float(k) >= target]
    return min(strikes)


def step_strike(
    chain: OptionChainResponse, expiration: date, right: OptionRight, strike: Decimal, steps: int
) -> Decimal:
    strikes = strikes_for(chain, expiration, right)
    idx = strikes.index(strike) + steps
    return strikes[max(0, min(idx, len(strikes) - 1))]


# --------------------------------------------------------------------------------------
# Special scenarios
# --------------------------------------------------------------------------------------

def build_multiplier_chain(multiplier: int = 10, symbol: str = "SPY") -> OptionChainResponse:
    """Non-100 multiplier contracts (10 = mini option, 50 = custom) on the same ladder."""
    if multiplier == 100:
        raise ValueError("use build_chain for multiplier=100")
    root = {10: f"{symbol}M", 50: f"{symbol}X"}.get(multiplier, f"{symbol}Z")
    chain = build_chain(symbol, multiplier=multiplier, root=root)
    chain.warnings.append(f"non_standard_multiplier_{multiplier}")
    return chain


@dataclass
class CrashScenario:
    before: OptionChainResponse
    after: OptionChainResponse
    spot_before: float
    spot_after: float
    iv_shift: float
    as_of_before: datetime
    as_of_after: datetime


def build_crash_scenario(symbol: str = "SPY", *, drop_pct: float = 0.12, iv_spike: float = 0.20) -> CrashScenario:
    """Overnight gap: underlying -12 %, IV +20 vol points; same strike ladder for both chains."""
    spec = UNDERLYINGS[symbol]
    before = build_chain(symbol)
    as_of_after = datetime(2026, 6, 11, 9, 35, tzinfo=ET)
    spot_after = round(spec.spot * (1.0 - drop_pct), 2)
    after = build_chain(
        symbol,
        spot=spot_after,
        ladder_spot=spec.spot,
        as_of=as_of_after,
        dtes=(),
        expirations=[EXP_5, EXP_21, EXP_35],
        iv_shift=iv_spike,
        min_theo=0.0,
        extra_warnings=["scenario_gap_down_iv_spike"],
    )
    return CrashScenario(
        before=before, after=after, spot_before=spec.spot, spot_after=spot_after,
        iv_shift=iv_spike, as_of_before=AS_OF, as_of_after=as_of_after,
    )


@dataclass
class ExDividendScenario:
    chain: OptionChainResponse
    short_call_occ: str
    short_call_strike: Decimal
    expiration: date
    spot: float
    dividend_per_share: Decimal
    extrinsic_per_share: Decimal
    intrinsic_per_share: Decimal
    ex_div_symbols: list[str] = field(default_factory=list)
    ex_div_date: date = AS_OF.date()


def build_ex_dividend_scenario(
    *, strike: float = 195.0, dividend: float = 0.50, symbol: str = "AAPL"
) -> ExDividendScenario:
    """Deep ITM short American call whose remaining extrinsic < upcoming dividend."""
    chain = build_chain(symbol, dtes=(5,), min_theo=0.0)
    q = find_quote(chain, EXP_5, OptionRight.CALL, strike)
    spot = chain.underlying_price
    assert spot is not None
    intrinsic = max(Decimal("0"), spot - q.contract.strike)
    extrinsic = (q.mid or Decimal("0")) - intrinsic
    return ExDividendScenario(
        chain=chain,
        short_call_occ=q.contract.occ_symbol,
        short_call_strike=q.contract.strike,
        expiration=EXP_5,
        spot=float(spot),
        dividend_per_share=_d(dividend),
        extrinsic_per_share=extrinsic,
        intrinsic_per_share=intrinsic,
        ex_div_symbols=[symbol],
    )


PIN_AS_OF = datetime(2026, 6, 15, 15, 40, tzinfo=ET)   # expiration day (Mon), 20 min before the close
PIN_SHORT_CALL_STRIKE = Decimal("700")
PIN_SPOT = 699.80                                        # $0.20 from the short strike


@dataclass
class PinRiskScenario:
    chain: OptionChainResponse
    as_of: datetime
    expiration: date
    spot: float
    short_strike: Decimal
    distance: Decimal


def build_pin_risk_scenario() -> PinRiskScenario:
    chain = build_chain(
        "SPY",
        spot=PIN_SPOT,
        ladder_spot=700.0,
        as_of=PIN_AS_OF,
        dtes=(),
        expirations=[EXP_5],
        min_theo=0.0,
        span_pct=0.05,
        extra_warnings=["scenario_pin_risk_expiration_day_1540_et"],
    )
    return PinRiskScenario(
        chain=chain, as_of=PIN_AS_OF, expiration=EXP_5, spot=PIN_SPOT,
        short_strike=PIN_SHORT_CALL_STRIKE,
        distance=PIN_SHORT_CALL_STRIKE - Decimal(str(PIN_SPOT)),
    )


CLOSED_AS_OF = datetime(2026, 6, 13, 11, 0, tzinfo=ET)       # Saturday – market closed
STALE_QUOTE_TS = datetime(2026, 6, 10, 15, 59, tzinfo=ET)    # three days old


def build_closed_market_chain(symbol: str = "SPY") -> OptionChainResponse:
    """Closed market: stale timestamps, zero bids, missing quotes and missing Greeks.

    Deterministic pattern by contract index:
      i % 4 == 0 -> Greeks and IV missing (``Greeks()`` all None)
      i % 4 == 1 -> zero bid (illiquid)
      i % 4 == 2 -> bid/ask/mid all missing (only a stale last trade)
      i % 4 == 3 -> complete but stale
    """
    base = build_chain(symbol, as_of=CLOSED_AS_OF, market_open=False)
    patched: list[OptionQuoteSnapshot] = []
    for i, q in enumerate(base.contracts):
        update: dict = {
            "is_stale": True,
            "quote_timestamp": STALE_QUOTE_TS,
            "data_warnings": [FIXTURE_WARNING, "market_closed", "stale_quote"],
        }
        mod = i % 4
        if mod == 0:
            update.update(greeks=Greeks(), implied_volatility=None)
            update["data_warnings"] = [*update["data_warnings"], "missing_greeks"]
        elif mod == 1:
            update.update(bid=Decimal("0"), mid=None, spread_pct=None)
            update["data_warnings"] = [*update["data_warnings"], "zero_bid"]
        elif mod == 2:
            update.update(bid=None, ask=None, mid=None, spread_pct=None)
            update["data_warnings"] = [*update["data_warnings"], "missing_quote"]
        patched.append(q.model_copy(update=update))
    return base.model_copy(
        update={
            "contracts": patched,
            "is_stale": True,
            "market_open": False,
            "warnings": [FIXTURE_WARNING, "market_closed", "stale_quotes", "missing_greeks", "zero_bid"],
        }
    )


# --------------------------------------------------------------------------------------
# Candidate builders (grounded in chain quotes – never invented)
# --------------------------------------------------------------------------------------

def leg_from_quote(
    q: OptionQuoteSnapshot, role: LegRole, side: OptionSide, ratio: int = 1
) -> LegPlan:
    c = q.contract
    return LegPlan(
        occ_symbol=c.occ_symbol,
        underlying_symbol=c.underlying_symbol,
        leg_role=role,
        side=side,
        right=c.right,
        strike=c.strike,
        expiration_date=c.expiration_date,
        multiplier=c.multiplier,
        ratio=ratio,
        exercise_style=c.exercise_style,
        bid=q.bid,
        ask=q.ask,
        mid=q.mid,
        greeks=q.greeks,
        implied_volatility=q.implied_volatility,
    )


def stock_leg(symbol: str, spot: Decimal, multiplier: int = 100) -> LegPlan:
    return LegPlan(
        occ_symbol=None,
        underlying_symbol=symbol,
        leg_role=LegRole.UNDERLYING_STOCK,
        side=OptionSide.LONG,
        multiplier=multiplier,
        bid=spot,
        ask=spot,
        mid=spot,
    )


_ROLE = {
    (OptionRight.CALL, OptionSide.LONG): LegRole.LONG_CALL,
    (OptionRight.CALL, OptionSide.SHORT): LegRole.SHORT_CALL,
    (OptionRight.PUT, OptionSide.LONG): LegRole.LONG_PUT,
    (OptionRight.PUT, OptionSide.SHORT): LegRole.SHORT_PUT,
}


def opt_leg(chain: OptionChainResponse, exp: date, right: OptionRight, side: OptionSide, strike: Decimal) -> LegPlan:
    return leg_from_quote(find_quote(chain, exp, right, strike), _ROLE[(right, side)], side)


def make_candidate(
    strategy_type: StrategyType,
    chain: OptionChainResponse,
    legs: list[LegPlan],
    *,
    qty: int = 1,
    expiration: Optional[date] = None,
    candidate_id: Optional[str] = None,
) -> StrategyCandidate:
    """Build a ``StrategyCandidate`` with executable (Bid/Ask) premium estimates."""
    option_legs = [leg for leg in legs if leg.leg_role != LegRole.UNDERLYING_STOCK]
    stock_legs = [leg for leg in legs if leg.leg_role == LegRole.UNDERLYING_STOCK]
    assert option_legs, "candidate requires at least one option leg"
    spot = chain.underlying_price
    assert spot is not None
    mult = option_legs[0].multiplier
    exp = expiration or option_legs[0].expiration_date
    assert exp is not None
    dte = (exp - chain.as_of.date()).days

    per_unit = Decimal("0")
    for leg in option_legs:
        px = leg.bid if leg.side == OptionSide.SHORT else leg.ask
        assert px is not None
        per_unit += (px if leg.side == OptionSide.SHORT else -px) * leg.ratio
    total = per_unit * mult * qty
    is_credit = per_unit > 0

    shorts = [leg for leg in option_legs if leg.side == OptionSide.SHORT]
    longs = [leg for leg in option_legs if leg.side == OptionSide.LONG]
    strikes = sorted(float(leg.strike) for leg in option_legs if leg.strike is not None)
    net_delta = sum(
        (leg.greeks.delta or 0.0) * (1 if leg.side == OptionSide.LONG else -1) * leg.ratio
        for leg in option_legs
    )
    net_theta = sum(
        (leg.greeks.theta or 0.0) * (1 if leg.side == OptionSide.LONG else -1) * leg.ratio
        for leg in option_legs
    )
    net_vega = sum(
        (leg.greeks.vega or 0.0) * (1 if leg.side == OptionSide.LONG else -1) * leg.ratio
        for leg in option_legs
    )

    max_profit: Optional[Decimal]
    collateral = Decimal("0")
    credit_total = total if is_credit else Decimal("0")
    debit_total = -total if not is_credit else Decimal("0")
    break_evens: list[Decimal] = []

    if len(option_legs) == 1 and not stock_legs:
        leg = option_legs[0]
        k = leg.strike or Decimal("0")
        if leg.side == OptionSide.LONG:
            max_loss = debit_total
            if leg.right == OptionRight.CALL:
                max_profit = None
                break_evens = [k - per_unit]
            else:
                max_profit = (k + per_unit) * mult * qty
                break_evens = [k + per_unit]
        else:
            if leg.right == OptionRight.PUT:
                collateral = k * mult * qty
                max_loss = collateral - credit_total
                max_profit = credit_total
                break_evens = [k - per_unit]
            else:  # covered call on existing shares
                max_loss = (spot - per_unit) * mult * qty
                max_profit = (k - spot + per_unit) * mult * qty
                break_evens = [spot - per_unit]
    elif stock_legs and len(option_legs) == 1:  # buy-write
        leg = option_legs[0]
        k = leg.strike or Decimal("0")
        max_loss = (spot - per_unit) * mult * qty
        max_profit = (k - spot + per_unit) * mult * qty
        break_evens = [spot - per_unit]
    elif len(option_legs) == 2:
        width = Decimal(str(strikes[-1] - strikes[0]))
        if is_credit:
            collateral = width * mult * qty
            max_profit = credit_total
            max_loss = collateral - credit_total
        else:
            max_loss = debit_total
            max_profit = width * mult * qty - debit_total
        short_k = shorts[0].strike if shorts else None
        if short_k is not None:
            break_evens = [short_k - per_unit if shorts[0].right == OptionRight.PUT else short_k + per_unit]
    elif len(option_legs) == 4:
        puts = sorted(float(leg.strike) for leg in option_legs if leg.right == OptionRight.PUT)
        calls = sorted(float(leg.strike) for leg in option_legs if leg.right == OptionRight.CALL)
        widest = Decimal(str(max(puts[-1] - puts[0], calls[-1] - calls[0])))
        collateral = widest * mult * qty
        max_profit = credit_total
        max_loss = collateral - credit_total
    else:  # pragma: no cover - fixtures only build supported shapes
        raise AssertionError("unsupported leg shape")

    cid = candidate_id or (
        f"{strategy_type.value}-{chain.underlying_symbol}-{exp:%Y%m%d}-"
        + "-".join(f"{s:g}" for s in strikes)
        + f"-x{qty}"
        + ("-bw" if stock_legs else "")
    )
    return StrategyCandidate(
        candidate_id=cid,
        strategy_type=strategy_type,
        underlying_symbol=chain.underlying_symbol,
        underlying_price=spot,
        contracts_qty=qty,
        legs=[*stock_legs, *option_legs],
        dte=dte,
        expiration_date=exp,
        is_credit=is_credit,
        estimated_net_premium_per_unit=per_unit,
        estimated_net_premium_total=total,
        max_profit_usd=max_profit,
        max_loss_usd=max_loss,
        collateral_required_usd=collateral,
        break_even_prices=break_evens,
        probability_of_profit=0.5,
        net_delta=net_delta,
        net_theta=net_theta,
        net_vega=net_vega,
        liquidity_score=0.9,
        rules_score=75.0,  # 0-100 scale (advisor floor is 55)
        feed_tier=DataFeedTier.FIXTURE,
        is_indicative=False,
        warnings=[FIXTURE_WARNING],
        rationale_summary="fixture candidate (synthetic data)",
    )


def candidate_for(
    strategy_type: StrategyType,
    chain: OptionChainResponse,
    *,
    dte: int = 21,
    qty: int = 1,
    width_steps: int = 2,
    short_offset_pct: float = 0.04,
    buy_write: bool = False,
) -> StrategyCandidate:
    """Pick sensible strikes for each of the 9 strategies from the chain's own ladder."""
    exp = expiration_for_dte(dte, chain.as_of)
    spot = float(chain.underlying_price or 0)
    sym = chain.underlying_symbol
    C, P = OptionRight.CALL, OptionRight.PUT
    L, S = OptionSide.LONG, OptionSide.SHORT

    atm_c = nearest_strike(chain, exp, C, spot)
    atm_p = nearest_strike(chain, exp, P, spot)

    if strategy_type == StrategyType.LONG_CALL:
        legs = [opt_leg(chain, exp, C, L, atm_c)]
    elif strategy_type == StrategyType.LONG_PUT:
        legs = [opt_leg(chain, exp, P, L, atm_p)]
    elif strategy_type == StrategyType.COVERED_CALL:
        k = strike_at_or_above(chain, exp, C, spot * (1 + short_offset_pct))
        legs = [opt_leg(chain, exp, C, S, k)]
        if buy_write:
            legs = [stock_leg(sym, chain.underlying_price or Decimal("0"), legs[0].multiplier), *legs]
    elif strategy_type in (StrategyType.CASH_SECURED_PUT, StrategyType.WHEEL):
        k = strike_at_or_below(chain, exp, P, spot * (1 - short_offset_pct))
        legs = [opt_leg(chain, exp, P, S, k)]
    elif strategy_type == StrategyType.BULL_CALL_SPREAD:
        long_k = atm_c
        short_k = step_strike(chain, exp, C, long_k, width_steps)
        legs = [opt_leg(chain, exp, C, L, long_k), opt_leg(chain, exp, C, S, short_k)]
    elif strategy_type == StrategyType.BEAR_PUT_SPREAD:
        long_k = atm_p
        short_k = step_strike(chain, exp, P, long_k, -width_steps)
        legs = [opt_leg(chain, exp, P, L, long_k), opt_leg(chain, exp, P, S, short_k)]
    elif strategy_type == StrategyType.BULL_PUT_CREDIT_SPREAD:
        short_k = strike_at_or_below(chain, exp, P, spot * (1 - short_offset_pct))
        long_k = step_strike(chain, exp, P, short_k, -width_steps)
        legs = [opt_leg(chain, exp, P, S, short_k), opt_leg(chain, exp, P, L, long_k)]
    elif strategy_type == StrategyType.BEAR_CALL_CREDIT_SPREAD:
        short_k = strike_at_or_above(chain, exp, C, spot * (1 + short_offset_pct))
        long_k = step_strike(chain, exp, C, short_k, width_steps)
        legs = [opt_leg(chain, exp, C, S, short_k), opt_leg(chain, exp, C, L, long_k)]
    elif strategy_type == StrategyType.IRON_CONDOR:
        sp = strike_at_or_below(chain, exp, P, spot * (1 - short_offset_pct))
        lp = step_strike(chain, exp, P, sp, -width_steps)
        sc = strike_at_or_above(chain, exp, C, spot * (1 + short_offset_pct))
        lc = step_strike(chain, exp, C, sc, width_steps)
        legs = [
            opt_leg(chain, exp, P, S, sp), opt_leg(chain, exp, P, L, lp),
            opt_leg(chain, exp, C, S, sc), opt_leg(chain, exp, C, L, lc),
        ]
    else:  # pragma: no cover
        raise ValueError(strategy_type)
    return make_candidate(strategy_type, chain, legs, qty=qty, expiration=exp)


def condor_for_pin_scenario(chain: OptionChainResponse, *, qty: int = 1) -> StrategyCandidate:
    """Iron condor whose short call (700) is the strike the pin scenario finishes next to.

    Built from a normal-session SPY chain (spot 695, expiration = 5 DTE = pin expiration day).
    """
    C, P = OptionRight.CALL, OptionRight.PUT
    L, S = OptionSide.LONG, OptionSide.SHORT
    legs = [
        opt_leg(chain, EXP_5, P, S, Decimal("685")),
        opt_leg(chain, EXP_5, P, L, Decimal("680")),
        opt_leg(chain, EXP_5, C, S, PIN_SHORT_CALL_STRIKE),
        opt_leg(chain, EXP_5, C, L, Decimal("705")),
    ]
    return make_candidate(StrategyType.IRON_CONDOR, chain, legs, qty=qty, expiration=EXP_5)


def rule_profile(**overrides) -> AccountRuleProfile:
    """Flexible-sim profile; override any field per test (e.g. ``options_approval_level=1``)."""
    # Accounting/lifecycle tests must not trip the default risk caps; tests that exercise a cap
    # pass it explicitly (e.g. ``rule_profile(max_risk_per_trade_pct=1)``).
    base = dict(
        max_risk_per_trade_pct=Decimal("100"), max_collateral_pct=Decimal("100"),
        max_open_strategies=20, max_daily_loss_pct=Decimal("100"), max_drawdown_pct=Decimal("100"),
    )
    base.update(overrides)
    return AccountRuleProfile(**base)


# --------------------------------------------------------------------------------------
# Static provider (protocol-compatible, no network)
# --------------------------------------------------------------------------------------

class StaticChainProvider:
    """``OptionsDataProvider`` implementation over prebuilt synthetic chains."""

    provider_name = "fixture_static"

    def __init__(self, chains: dict[str, OptionChainResponse]) -> None:
        self.chains = chains
        self.calls: list[str] = []

    def get_underlying_price(self, symbol: str) -> tuple[Decimal | None, bool, list[str]]:
        self.calls.append(f"price:{symbol}")
        chain = self.chains.get(symbol)
        if chain is None:
            return None, False, ["unknown_symbol"]
        return chain.underlying_price, chain.market_open, [FIXTURE_WARNING]

    def get_option_chain(
        self,
        symbol: str,
        *,
        min_dte: int = 3,
        max_dte: int = 60,
        strike_range_pct: float = 0.20,
        as_of: datetime | None = None,
    ) -> OptionChainResponse:
        self.calls.append(f"chain:{symbol}")
        chain = self.chains.get(symbol)
        if chain is None:
            return OptionChainResponse(
                underlying_symbol=symbol, feed_tier=DataFeedTier.UNAVAILABLE, warnings=["unknown_symbol"],
                provider_name=self.provider_name,
            )
        ref = chain.as_of.date()
        keep = [q for q in chain.contracts if min_dte <= (q.contract.expiration_date - ref).days <= max_dte]
        return chain.model_copy(
            update={
                "contracts": keep,
                "expirations": sorted({q.contract.expiration_date for q in keep}),
            }
        )

    def get_quotes_for_occ(
        self, occ_symbols: list[str], *, underlying_hint: str | None = None
    ) -> dict[str, OptionQuoteSnapshot]:
        self.calls.append("quotes")
        out: dict[str, OptionQuoteSnapshot] = {}
        for chain in self.chains.values():
            lookup = quotes_by_occ(chain)
            for occ in occ_symbols:
                if occ in lookup:
                    out[occ] = lookup[occ]
        return out

    def get_provider_health(self) -> dict:
        return {"provider": self.provider_name, "feed_tier": DataFeedTier.FIXTURE.value, "ok": True}


# --------------------------------------------------------------------------------------
# Simulation harness (in-memory repository -> runs everywhere, no database required)
# --------------------------------------------------------------------------------------

OPTQA_PREFIX = "__optqa_"


def g(obj, name: str, default=None):
    """Attribute-or-key access so tests tolerate pydantic models, dataclasses and dict rows."""
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def sid(result) -> str:
    """Extract a strategy id from a ``SimOperationResult`` / view / plain id."""
    if isinstance(result, str):
        return result
    for holder in (result, g(result, "strategy"), g(result, "new_strategy")):
        if holder is None:
            continue
        for key in ("id", "strategy_id"):
            val = g(holder, key)
            if val:
                return str(val)
    raise AssertionError(f"cannot extract strategy id from {result!r}")


def f(x) -> float:
    return float(x or 0)


class OptionsOperationRejected(AssertionError):
    """The engine refused an operation that the test expected to succeed."""


class OptionsLab:
    """Per-test sandbox around ``OptionsSimulationEngine`` + ``InMemoryOptionsRepository``.

    Uses the engine's own in-memory repository port so accounting / lifecycle tests are
    deterministic and need no PostgreSQL.  PostgreSQL persistence is covered separately
    (``test_options_ai_and_risk_guard.py``) through the real ``OptionsRepository``.
    """

    def __init__(self) -> None:
        from quantara_options.sim.engine import InMemoryOptionsRepository, OptionsSimulationEngine

        self.repo = InMemoryOptionsRepository()
        self.engine = OptionsSimulationEngine(self.repo)
        self.slugs: list[str] = []

    # ---- accounts -------------------------------------------------------------------
    def new_account(
        self,
        capital: float = 10_000,
        *,
        mode: str = "rules",
        scope: Optional[str] = None,
        profile: Optional[AccountRuleProfile] = None,
        slug: Optional[str] = None,
    ) -> str:
        import uuid

        from quantara_options.contracts import DecisionMode
        from quantara_options.sim.accounting import new_account_state

        slug = slug or f"{OPTQA_PREFIX}{uuid.uuid4().hex[:10]}"
        self.repo.add_account(
            new_account_state(
                slug, capital, name=f"QA {slug}", mode=DecisionMode(mode), strategy_scope=scope,
                rule_profile=profile or rule_profile(),
            )
        )
        self.slugs.append(slug)
        return slug

    def seed_shares(self, slug: str, symbol: str, shares: int, cost_basis: float) -> None:
        """Give the account already-owned shares (cash is debited so equity stays neutral)."""
        from quantara_options.sim.accounting import SimStockLot, new_id

        acct = self.repo.accounts[slug]
        basis = Decimal(str(cost_basis))
        acct.stock_lots.append(
            SimStockLot(
                lot_id=new_id("lot"), account_slug=slug, symbol=symbol, shares=shares,
                cost_basis_per_share=basis, last_price=basis, source="buy",
            )
        )
        acct.cash_balance -= basis * shares

    def acct(self, slug: str):
        return self.engine.get_account_summary(slug)

    def commit(self) -> None:  # in-memory repository: nothing to flush
        return None

    def state(self, slug: str):
        return self.repo.accounts[slug]

    def wheel_state(self, slug: str, symbol: str) -> str:
        return self.repo.accounts[slug].wheel_states.get(symbol, "idle")

    def set_wheel_state(self, slug: str, symbol: str, state: str) -> None:
        self.repo.accounts[slug].wheel_states[symbol] = state

    # ---- views ----------------------------------------------------------------------
    def groups(self, slug: str, status: Optional[str] = None):
        out = []
        acct = self.repo.accounts[slug]
        for st in self.repo.list_strategies(slug):
            if status == "open" and not st.is_open:
                continue
            if status == "closed" and st.is_open:
                continue
            out.append(self.engine.strategy_to_view(st, acct, with_payoff=False))
        return out

    def group(self, strategy_id: str):
        st = self.repo.strategies[strategy_id]
        return self.engine.strategy_to_view(st, self.repo.accounts[st.account_slug], with_payoff=False)

    def lot(self, slug: str, symbol: str):
        """Aggregate view of the (positive) share lot, or ``None`` when no shares are held."""
        lots = self.repo.accounts[slug].lots_for(symbol)
        if not lots:
            return None
        shares = sum(l.shares for l in lots)
        cost = sum(l.cost_basis_per_share * l.shares for l in lots) / shares
        adj = sum(l.adjusted_cost_basis_per_share * l.shares for l in lots) / shares
        return {
            "shares": shares,
            "encumbered_shares": sum(l.encumbered_shares for l in lots),
            "avg_cost_basis": cost,
            "adjusted_cost_basis": adj,
        }

    # ---- engine shortcuts (exact planned signatures) --------------------------------------
    @staticmethod
    def spots(*chains: OptionChainResponse) -> dict[str, Decimal]:
        return {c.underlying_symbol: c.underlying_price for c in chains if c.underlying_price is not None}

    @staticmethod
    def _quotes(chains) -> dict[str, OptionQuoteSnapshot]:
        quotes: dict[str, OptionQuoteSnapshot] = {}
        for c in chains:
            quotes.update(quotes_by_occ(c))
        return quotes

    def try_open(self, slug: str, candidate: StrategyCandidate, *, now: datetime = AS_OF,
                 chain: Optional[OptionChainResponse] = None, rationale=None):
        """Attempt an open and return the raw ``SimOperationResult`` (``ok`` may be False)."""
        return self.engine.open_strategy(
            slug, candidate, ai_rationale=rationale, now=now,
            quotes_by_occ=quotes_by_occ(chain) if chain is not None else None,
        )

    def open(self, slug: str, candidate: StrategyCandidate, *, now: datetime = AS_OF,
             chain: Optional[OptionChainResponse] = None, rationale=None) -> str:
        res = self.try_open(slug, candidate, now=now, chain=chain, rationale=rationale)
        if not res.ok:
            raise OptionsOperationRejected(f"open rejected: {res.reasons}")
        return sid(res)

    def mtm(self, slug: str, *chains: OptionChainResponse, now: Optional[datetime] = None):
        res = self.engine.mark_to_market(
            slug, self._quotes(chains), self.spots(*chains), now=now or chains[0].as_of
        )
        assert res.ok
        return res

    def close(self, strategy_id: str, *chains: OptionChainResponse, reason: str = "manual_close",
              now: Optional[datetime] = None):
        res = self.engine.close_strategy(
            strategy_id, self._quotes(chains), self.spots(*chains), reason=reason,
            now=now or chains[0].as_of,
        )
        if not res.ok:
            raise OptionsOperationRejected(f"close rejected: {res.reasons}")
        return res

    def roll(self, strategy_id: str, new_candidate: StrategyCandidate, *chains: OptionChainResponse,
             reason: str = "roll", now: Optional[datetime] = None):
        res = self.engine.roll_strategy(
            strategy_id, new_candidate, self._quotes(chains), self.spots(*chains), reason=reason,
            now=now or chains[0].as_of,
        )
        if not res.ok:
            raise OptionsOperationRejected(f"roll rejected: {res.reasons}")
        return res

    def expire(self, slug: str, *chains: OptionChainResponse, now: Optional[datetime] = None,
               ex_div_symbols=None):
        res = self.engine.process_expirations_and_assignments(
            slug, self._quotes(chains), self.spots(*chains), now=now or chains[0].as_of,
            ex_div_symbols=ex_div_symbols,
        )
        assert res.ok
        return res


def expiry_chain(
    symbol: str, expiration: date, *, spot: float, ladder_spot: Optional[float] = None,
    minutes_after_close: int = 5, extra_dte_expirations: Iterable[date] = (),
) -> OptionChainResponse:
    """Settlement-time chain (t=0 => quotes ~ intrinsic, bid 0 / ask 0.01 for OTM)."""
    as_of = datetime.combine(expiration, time(16, minutes_after_close), tzinfo=ET)
    return build_chain(
        symbol, spot=spot, ladder_spot=ladder_spot, as_of=as_of, dtes=(),
        expirations=[expiration, *extra_dte_expirations], min_theo=0.0, market_open=False,
        extra_warnings=["scenario_expiration_settlement"],
    )


def moved_chain(symbol: str, *, spot: float, as_of: datetime, iv_shift: float = 0.0,
                dtes: Iterable[int] = (), expirations: Optional[Iterable[date]] = None) -> OptionChainResponse:
    """Same strike ladder as the base chain but spot / time / IV moved (for MTM tests)."""
    spec = UNDERLYINGS[symbol]
    exps = list(expirations) if expirations is not None else [expiration_for_dte(d) for d in (dtes or DTE_BUCKETS)]
    return build_chain(
        symbol, spot=spot, ladder_spot=spec.spot, as_of=as_of, dtes=(), expirations=exps,
        iv_shift=iv_shift, min_theo=0.0,
    )


@pytest.fixture
def options_lab():
    """In-memory simulation sandbox (skips visibly if the sim engine is not importable)."""
    import importlib

    try:
        importlib.import_module("quantara_options.sim.engine")
    except ImportError as exc:  # implementation not merged yet
        pytest.skip(f"quantara_options.sim.engine not importable: {exc}")
    return OptionsLab()


# --------------------------------------------------------------------------------------
# PostgreSQL helpers (only used by tests that exercise the real ``OptionsRepository``)
# --------------------------------------------------------------------------------------

def pg_table_exists(session, name: str) -> bool:
    from sqlalchemy import text

    return bool(session.execute(text("SELECT to_regclass(:n) IS NOT NULL"), {"n": f"public.{name}"}).scalar())


def pg_insert_qa_account(session, slug: str, capital: float, mode: str = "rules",
                         scope: Optional[str] = None) -> None:
    """Insert a throw-away ``__optqa_*`` options account (never touches the lab accounts)."""
    import json

    from sqlalchemy import text

    assert slug.startswith(OPTQA_PREFIX), "QA accounts must carry the cleanup prefix"
    session.execute(text(
        """
        INSERT INTO opt_accounts (slug, name, mode, strategy_scope, experiment_group, starting_capital,
                                  cash_balance, equity, peak_equity, rule_profile, wheel_states)
        VALUES (:slug, :name, :mode, :scope, 'optqa', :cap, :cap, :cap, :cap, CAST(:profile AS jsonb), '{}'::jsonb)
        """),
        {"slug": slug, "name": f"QA {slug}", "mode": mode, "scope": scope, "cap": capital,
         "profile": json.dumps(rule_profile().model_dump(mode="json"))})
    session.commit()


def pg_purge_qa_rows(session) -> None:
    """Delete every ``__optqa_*`` options row (FK-safe order)."""
    from sqlalchemy import text

    like = {"p": f"{OPTQA_PREFIX}%"}
    for sql in (
        "DELETE FROM opt_fills WHERE order_id IN (SELECT o.id FROM opt_orders o JOIN opt_accounts a ON a.id=o.account_id WHERE a.slug LIKE :p)",
        "DELETE FROM opt_orders WHERE account_id IN (SELECT id FROM opt_accounts WHERE slug LIKE :p)",
        "DELETE FROM opt_ledger_events WHERE account_slug LIKE :p",
        "DELETE FROM opt_stock_lots WHERE account_slug LIKE :p",
        "DELETE FROM opt_ai_decisions WHERE account_slug LIKE :p",
        "DELETE FROM opt_strategies WHERE account_slug LIKE :p",
        "DELETE FROM opt_accounts WHERE slug LIKE :p",
    ):
        session.execute(text(sql), like)
    session.commit()


def assert_equity_identity(acct, tol: float = 0.05) -> None:
    """equity = cash + long options MV - short options liability + stock value."""
    expected = (
        f(g(acct, "cash_balance")) + f(g(acct, "long_options_market_value"))
        - f(g(acct, "short_options_liability")) + f(g(acct, "stock_holdings_value"))
    )
    assert abs(f(g(acct, "equity")) - expected) <= tol, (
        f"equity identity broken: equity={g(acct, 'equity')} expected={expected:.2f}"
    )


__all__ = [name for name in globals() if not name.startswith("_")]
