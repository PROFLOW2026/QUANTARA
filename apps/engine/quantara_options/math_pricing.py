"""Black-Scholes-Merton & CRR Binomial pricing, Greeks, Implied Volatility, and Payoff math.

Pure Python `math` — zero external heavy dependencies (SciPy not required).
Strictly respects each contract's `multiplier` (default 100, never hardcoded blindly).
"""

from __future__ import annotations

import math
from decimal import Decimal
from typing import Iterable

from quantara_options.contracts import (
    ExerciseStyle,
    Greeks,
    LegPlan,
    LegRole,
    OptionRight,
    OptionSide,
    PayoffPoint,
)

SQRT_2 = math.sqrt(2.0)
SQRT_2PI = math.sqrt(2.0 * math.pi)


def norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / SQRT_2))


def norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / SQRT_2PI


def bsm_price(
    *,
    spot: float,
    strike: float,
    t_years: float,
    rate: float,
    sigma: float,
    right: OptionRight,
    div_yield: float = 0.0,
) -> float:
    if spot <= 0 or strike <= 0:
        return 0.0
    if t_years <= 1e-7 or sigma <= 1e-6:
        intrinsic = max(0.0, spot - strike) if right == OptionRight.CALL else max(0.0, strike - spot)
        return intrinsic
    sqrt_t = math.sqrt(t_years)
    d1 = (math.log(spot / strike) + (rate - div_yield + 0.5 * sigma * sigma) * t_years) / (sigma * sqrt_t)
    d2 = d1 - sigma * sqrt_t
    df_q = math.exp(-div_yield * t_years)
    df_r = math.exp(-rate * t_years)
    if right == OptionRight.CALL:
        return max(0.0, spot * df_q * norm_cdf(d1) - strike * df_r * norm_cdf(d2))
    return max(0.0, strike * df_r * norm_cdf(-d2) - spot * df_q * norm_cdf(-d1))


def american_crr_price(
    *,
    spot: float,
    strike: float,
    t_years: float,
    rate: float,
    sigma: float,
    right: OptionRight,
    div_yield: float = 0.0,
    steps: int = 40,
) -> float:
    """Cox-Ross-Rubinstein binomial tree supporting American early exercise."""
    if t_years <= 1e-7 or sigma <= 1e-6 or spot <= 0 or strike <= 0:
        return bsm_price(
            spot=spot, strike=strike, t_years=t_years, rate=rate,
            sigma=sigma, right=right, div_yield=div_yield,
        )
    dt = t_years / steps
    u = math.exp(sigma * math.sqrt(dt))
    d = 1.0 / u
    disc = math.exp(-rate * dt)
    growth = math.exp((rate - div_yield) * dt)
    p = (growth - d) / (u - d)
    p = min(max(p, 0.0), 1.0)

    values = [0.0] * (steps + 1)
    for i in range(steps + 1):
        s_t = spot * (u ** (steps - i)) * (d ** i)
        values[i] = max(0.0, s_t - strike) if right == OptionRight.CALL else max(0.0, strike - s_t)

    for step in range(steps - 1, -1, -1):
        for i in range(step + 1):
            cont = disc * (p * values[i] + (1.0 - p) * values[i + 1])
            s_t = spot * (u ** (step - i)) * (d ** i)
            ex = max(0.0, s_t - strike) if right == OptionRight.CALL else max(0.0, strike - s_t)
            values[i] = max(cont, ex)
    return values[0]


def bsm_greeks(
    *,
    spot: float,
    strike: float,
    t_years: float,
    rate: float,
    sigma: float,
    right: OptionRight,
    div_yield: float = 0.0,
    computed_locally: bool = True,
) -> Greeks:
    if spot <= 0 or strike <= 0 or t_years <= 1e-7 or sigma <= 1e-6:
        itm = (spot > strike) if right == OptionRight.CALL else (strike > spot)
        delta = (1.0 if itm else 0.0) if right == OptionRight.CALL else (-1.0 if itm else 0.0)
        return Greeks(delta=delta, gamma=0.0, theta=0.0, vega=0.0, rho=0.0, computed_locally=computed_locally)

    sqrt_t = math.sqrt(t_years)
    d1 = (math.log(spot / strike) + (rate - div_yield + 0.5 * sigma * sigma) * t_years) / (sigma * sqrt_t)
    d2 = d1 - sigma * sqrt_t
    df_q = math.exp(-div_yield * t_years)
    df_r = math.exp(-rate * t_years)
    pdf_d1 = norm_pdf(d1)

    if right == OptionRight.CALL:
        delta = df_q * norm_cdf(d1)
        theta_annual = (
            -(spot * df_q * pdf_d1 * sigma) / (2.0 * sqrt_t)
            - rate * strike * df_r * norm_cdf(d2)
            + div_yield * spot * df_q * norm_cdf(d1)
        )
        rho = strike * t_years * df_r * norm_cdf(d2) / 100.0
    else:
        delta = df_q * (norm_cdf(d1) - 1.0)
        theta_annual = (
            -(spot * df_q * pdf_d1 * sigma) / (2.0 * sqrt_t)
            + rate * strike * df_r * norm_cdf(-d2)
            - div_yield * spot * df_q * norm_cdf(-d1)
        )
        rho = -strike * t_years * df_r * norm_cdf(-d2) / 100.0

    gamma = (df_q * pdf_d1) / (spot * sigma * sqrt_t)
    vega = spot * df_q * pdf_d1 * sqrt_t / 100.0
    theta_daily = theta_annual / 365.0

    return Greeks(
        delta=round(delta, 5),
        gamma=round(gamma, 6),
        theta=round(theta_daily, 5),
        vega=round(vega, 5),
        rho=round(rho, 5),
        computed_locally=computed_locally,
    )


def implied_volatility(
    *,
    market_price: float,
    spot: float,
    strike: float,
    t_years: float,
    rate: float,
    right: OptionRight,
    div_yield: float = 0.0,
    exercise_style: ExerciseStyle = ExerciseStyle.AMERICAN,
) -> float | None:
    """Solve IV using bracketed Newton-Raphson / bisection."""
    if market_price <= 0 or spot <= 0 or strike <= 0 or t_years <= 1e-7:
        return None
    intrinsic = max(0.0, spot - strike) if right == OptionRight.CALL else max(0.0, strike - spot)
    if market_price <= intrinsic * 0.98:
        return None

    pricer = american_crr_price if exercise_style == ExerciseStyle.AMERICAN else bsm_price
    low, high = 0.01, 4.0

    p_low = pricer(spot=spot, strike=strike, t_years=t_years, rate=rate, sigma=low, right=right, div_yield=div_yield)
    p_high = pricer(spot=spot, strike=strike, t_years=t_years, rate=rate, sigma=high, right=right, div_yield=div_yield)
    if market_price < p_low:
        return low
    if market_price > p_high:
        return high

    sigma = 0.30
    for _ in range(40):
        mid_p = bsm_price(spot=spot, strike=strike, t_years=t_years, rate=rate, sigma=sigma, right=right, div_yield=div_yield)
        if exercise_style == ExerciseStyle.AMERICAN:
            mid_p = american_crr_price(
                spot=spot, strike=strike, t_years=t_years, rate=rate, sigma=sigma,
                right=right, div_yield=div_yield, steps=24,
            )
        diff = mid_p - market_price
        if abs(diff) < 1e-4:
            return round(sigma, 4)
        if diff > 0:
            high = sigma
        else:
            low = sigma
        # Bisection step guarantees robust convergence even near zero vega
        sigma = 0.5 * (low + high)
        if high - low < 1e-4:
            return round(sigma, 4)
    return round(sigma, 4)


def compute_payoff_curve(
    *,
    legs: Iterable[LegPlan],
    spot: float,
    entry_option_cash_flow_usd: float,
    contracts_qty: int = 1,
    dte_remaining: int = 21,
    rate: float = 0.045,
    default_iv: float = 0.25,
    points: int = 41,
) -> list[PayoffPoint]:
    """Generate expiration and T+0 payoff points around spot.

    `entry_option_cash_flow_usd`: net cash flow of OPTION legs ONLY at entry
    (+credit received, -debit paid, already scaled by qty & contract multiplier).
    Any `UNDERLYING_STOCK` leg computes its own `(px - entry_ref) * shares * sign`
    so stock purchase cost is never double-counted.
    """
    legs_list = list(legs)
    if spot <= 0 or not legs_list:
        return []
    strikes = [float(l.strike) for l in legs_list if l.strike is not None]
    min_k = min([spot, *strikes])
    max_k = max([spot, *strikes])
    lo = max(1.0, min_k * 0.80)
    hi = max_k * 1.20
    if hi <= lo:
        lo, hi = spot * 0.85, spot * 1.15
    step = (hi - lo) / max(1, points - 1)
    t_rem = max(1, dte_remaining) / 365.0

    curve: list[PayoffPoint] = []
    for i in range(points):
        px = round(lo + i * step, 2)
        exp_val = 0.0
        t0_val = 0.0
        for leg in legs_list:
            mult = float(leg.multiplier or 100)
            qty_units = float(contracts_qty * max(1, leg.ratio))
            sign = 1.0 if leg.side == OptionSide.LONG else -1.0
            if leg.leg_role == LegRole.UNDERLYING_STOCK or leg.right is None or leg.strike is None:
                ref_px = float(leg.mid or spot)
                delta_stock = (px - ref_px) * float(contracts_qty * mult) * sign
                exp_val += delta_stock
                t0_val += delta_stock
                continue

            k = float(leg.strike)
            intrinsic = max(0.0, px - k) if leg.right == OptionRight.CALL else max(0.0, k - px)
            exp_val += sign * intrinsic * mult * qty_units

            iv = float(leg.implied_volatility or default_iv)
            theo = bsm_price(
                spot=px, strike=k, t_years=t_rem, rate=rate, sigma=iv, right=leg.right
            )
            t0_val += sign * theo * mult * qty_units

        curve.append(
            PayoffPoint(
                underlying_price=px,
                expiration_pnl=round(exp_val + entry_option_cash_flow_usd, 2),
                t0_pnl=round(t0_val + entry_option_cash_flow_usd, 2),
            )
        )
    return curve
