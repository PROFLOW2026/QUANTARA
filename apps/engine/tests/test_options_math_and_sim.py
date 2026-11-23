"""QUANTARA Options – pricing math + simulation accounting invariants.

Part A (pure math) runs everywhere.
Part B (simulation accounting) runs on the disposable broker-test PostgreSQL through
``OptionsRepository`` / ``OptionsSimulationEngine``.

All market data here is SYNTHETIC (``DataFeedTier.FIXTURE``).  These tests prove pricing /
accounting logic only – they do NOT prove live Alpaca / OPRA availability or accuracy.
"""

from __future__ import annotations

import math
from decimal import Decimal

import pytest

from quantara_options.contracts import (
    ExerciseStyle,
    LegPlan,
    LegRole,
    OptionRight,
    OptionSide,
    StrategyCandidate,
    StrategyType,
)
from quantara_options.math_pricing import (
    american_crr_price,
    bsm_greeks,
    bsm_price,
    compute_payoff_curve,
    implied_volatility,
)
from tests.fixtures.options_fixtures import (  # noqa: F401  (options_lab is a pytest fixture)
    AS_OF,
    EXP_21,
    FIXTURE_WARNING,
    assert_equity_identity,
    build_chain,
    build_multiplier_chain,
    candidate_for,
    expiry_chain,
    f,
    g,
    moved_chain,
    options_lab,
    rule_profile,
)

C, P = OptionRight.CALL, OptionRight.PUT

# =====================================================================================
# Part A – pricing math
# =====================================================================================


class TestBsmPricing:
    def test_reference_values(self):
        # Textbook S=K=100, T=1, r=5 %, sigma=20 %: call 10.4506, put 5.5735
        call = bsm_price(spot=100, strike=100, t_years=1, rate=0.05, sigma=0.20, right=C)
        put = bsm_price(spot=100, strike=100, t_years=1, rate=0.05, sigma=0.20, right=P)
        assert call == pytest.approx(10.4506, abs=2e-3)
        assert put == pytest.approx(5.5735, abs=2e-3)

    @pytest.mark.parametrize("q", [0.0, 0.012, 0.03])
    @pytest.mark.parametrize("strike", [80, 100, 125])
    def test_put_call_parity(self, strike, q):
        t, r, s, sig = 0.5, 0.045, 100.0, 0.27
        c = bsm_price(spot=s, strike=strike, t_years=t, rate=r, sigma=sig, right=C, div_yield=q)
        p = bsm_price(spot=s, strike=strike, t_years=t, rate=r, sigma=sig, right=P, div_yield=q)
        assert c - p == pytest.approx(s * math.exp(-q * t) - strike * math.exp(-r * t), abs=1e-6)

    def test_expiry_returns_intrinsic(self):
        assert bsm_price(spot=110, strike=100, t_years=0, rate=0.04, sigma=0.3, right=C) == 10.0
        assert bsm_price(spot=90, strike=100, t_years=0, rate=0.04, sigma=0.3, right=P) == 10.0
        assert bsm_price(spot=90, strike=100, t_years=0, rate=0.04, sigma=0.3, right=C) == 0.0

    def test_monotonic_in_vol_and_time(self):
        base = dict(spot=100, strike=100, rate=0.04, right=C)
        assert bsm_price(t_years=0.25, sigma=0.4, **base) > bsm_price(t_years=0.25, sigma=0.2, **base)
        assert bsm_price(t_years=0.5, sigma=0.3, **base) > bsm_price(t_years=0.1, sigma=0.3, **base)


class TestAmericanCrr:
    def test_american_put_has_early_exercise_premium(self):
        kw = dict(spot=80, strike=100, t_years=1.0, rate=0.06, sigma=0.25, right=P)
        eur = bsm_price(**kw)
        amer = american_crr_price(steps=200, **kw)
        assert amer > eur + 0.05  # deep ITM put with positive rates: clear premium
        assert amer >= 100 - 80 - 1e-9  # never below intrinsic

    def test_american_call_without_dividend_equals_european(self):
        kw = dict(spot=100, strike=95, t_years=0.5, rate=0.045, sigma=0.3, right=C)
        assert american_crr_price(steps=300, **kw) == pytest.approx(bsm_price(**kw), abs=0.03)

    def test_american_call_with_large_dividend_exceeds_european(self):
        kw = dict(spot=100, strike=70, t_years=0.5, rate=0.03, sigma=0.25, right=C, div_yield=0.08)
        assert american_crr_price(steps=200, **kw) > bsm_price(**kw) + 0.02

    def test_crr_converges_to_bsm_for_european_like_case(self):
        kw = dict(spot=100, strike=110, t_years=0.4, rate=0.04, sigma=0.3, right=C)
        err40 = abs(american_crr_price(steps=40, **kw) - bsm_price(**kw))
        err300 = abs(american_crr_price(steps=300, **kw) - bsm_price(**kw))
        assert err40 < 0.15
        assert err300 < 0.03


class TestGreeks:
    BASE = dict(spot=100.0, strike=100.0, t_years=0.25, rate=0.045, sigma=0.30, div_yield=0.01)

    def _price(self, right, **over):
        kw = {**self.BASE, **over}
        return bsm_price(right=right, **kw)

    @pytest.mark.parametrize("right", [C, P])
    def test_greeks_match_finite_differences(self, right):
        g_ = bsm_greeks(right=right, **self.BASE)
        h = 0.01
        delta_fd = (self._price(right, spot=100 + h) - self._price(right, spot=100 - h)) / (2 * h)
        gamma_fd = (
            self._price(right, spot=100 + h) - 2 * self._price(right) + self._price(right, spot=100 - h)
        ) / (h * h)
        vega_fd = (self._price(right, sigma=0.31) - self._price(right, sigma=0.29)) / 2.0  # per 1 vol pt
        theta_fd = self._price(right, t_years=0.25 - 1 / 365) - self._price(right)  # per calendar day
        rho_fd = (self._price(right, rate=0.046) - self._price(right, rate=0.044)) * 5.0  # per 1 % (0.01)
        assert g_.delta == pytest.approx(delta_fd, abs=2e-4)
        assert g_.gamma == pytest.approx(gamma_fd, abs=5e-4)
        assert g_.vega == pytest.approx(vega_fd, abs=2e-3)
        assert g_.theta == pytest.approx(theta_fd, abs=2e-3)
        assert g_.rho == pytest.approx(rho_fd, abs=2e-3)
        assert g_.computed_locally is True

    def test_signs_and_bounds(self):
        call = bsm_greeks(right=C, **self.BASE)
        put = bsm_greeks(right=P, **self.BASE)
        assert 0 < call.delta < 1 and -1 < put.delta < 0
        assert call.gamma == pytest.approx(put.gamma, abs=1e-6) and call.gamma > 0
        assert call.vega == pytest.approx(put.vega, abs=1e-6) and call.vega > 0
        assert call.theta < 0 and put.theta < 0  # long ATM options decay
        assert call.delta - put.delta == pytest.approx(math.exp(-0.01 * 0.25), abs=2e-3)

    def test_degenerate_inputs_do_not_crash(self):
        expired_itm = bsm_greeks(spot=110, strike=100, t_years=0, rate=0.04, sigma=0.3, right=C)
        assert expired_itm.delta == 1.0 and expired_itm.gamma == 0.0
        expired_put = bsm_greeks(spot=110, strike=100, t_years=0, rate=0.04, sigma=0.3, right=P)
        assert expired_put.delta == 0.0


class TestImpliedVolatility:
    @pytest.mark.parametrize("sigma", [0.12, 0.25, 0.55, 1.1])
    @pytest.mark.parametrize("right", [C, P])
    def test_european_roundtrip(self, sigma, right):
        px = bsm_price(spot=100, strike=102, t_years=0.2, rate=0.045, sigma=sigma, right=right)
        iv = implied_volatility(
            market_price=px, spot=100, strike=102, t_years=0.2, rate=0.045, right=right,
            exercise_style=ExerciseStyle.EUROPEAN,
        )
        assert iv == pytest.approx(sigma, abs=2e-3)

    @pytest.mark.parametrize("right", [C, P])
    def test_american_roundtrip(self, right):
        px = american_crr_price(
            spot=95, strike=100, t_years=0.3, rate=0.045, sigma=0.35, right=right, steps=24
        )
        iv = implied_volatility(
            market_price=px, spot=95, strike=100, t_years=0.3, rate=0.045, right=right,
            exercise_style=ExerciseStyle.AMERICAN,
        )
        assert iv == pytest.approx(0.35, abs=3e-3)

    def test_returns_none_for_impossible_inputs(self):
        common = dict(spot=100, strike=100, rate=0.04, right=C)
        assert implied_volatility(market_price=0.0, t_years=0.1, **common) is None
        assert implied_volatility(market_price=1.0, t_years=0.0, **common) is None
        # price far below intrinsic is not a valid option price
        assert (
            implied_volatility(
                market_price=1.0, spot=150, strike=100, t_years=0.1, rate=0.04, right=C
            )
            is None
        )


class TestMultiplierAwareness:
    """Contract multiplier is a property of each leg – NEVER hardcoded to 100."""

    def _long_call_leg(self, mult: int) -> LegPlan:
        return LegPlan(
            underlying_symbol="SPY", leg_role=LegRole.LONG_CALL, side=OptionSide.LONG, right=C,
            strike=Decimal("100"), multiplier=mult, implied_volatility=0.25,
        )

    def test_payoff_curve_scales_exactly_with_multiplier(self):
        premium = 3.0
        c10 = compute_payoff_curve(
            legs=[self._long_call_leg(10)], spot=100, entry_option_cash_flow_usd=-premium * 10,
            dte_remaining=21,
        )
        c100 = compute_payoff_curve(
            legs=[self._long_call_leg(100)], spot=100, entry_option_cash_flow_usd=-premium * 100,
            dte_remaining=21,
        )
        assert len(c10) == len(c100) > 10
        for a, b in zip(c10, c100):
            assert a.underlying_price == b.underlying_price
            assert b.expiration_pnl == pytest.approx(10 * a.expiration_pnl, abs=0.2)
        assert min(p.expiration_pnl for p in c10) == pytest.approx(-30.0, abs=0.01)
        assert min(p.expiration_pnl for p in c100) == pytest.approx(-300.0, abs=0.01)

    def test_payoff_curve_stock_leg_uses_leg_multiplier(self):
        """Buy-write: above the short strike the profit is (K - entry) * shares + credit."""
        stock = LegPlan(
            underlying_symbol="PLTR", leg_role=LegRole.UNDERLYING_STOCK, side=OptionSide.LONG,
            multiplier=100, mid=Decimal("42"),
        )
        short_call = LegPlan(
            underlying_symbol="PLTR", leg_role=LegRole.SHORT_CALL, side=OptionSide.SHORT, right=C,
            strike=Decimal("44"), multiplier=100, implied_volatility=0.5,
        )
        curve = compute_payoff_curve(
            legs=[stock, short_call], spot=42, entry_option_cash_flow_usd=90.0, dte_remaining=21
        )
        top = curve[-1]
        assert top.underlying_price > 44
        assert top.expiration_pnl == pytest.approx((44 - 42) * 100 + 90.0, abs=0.05)

    def test_candidate_totals_use_chain_multiplier_not_100(self):
        base = build_chain("SPY")
        mini = build_multiplier_chain(10)
        c100 = candidate_for(StrategyType.LONG_CALL, base)
        c10 = candidate_for(StrategyType.LONG_CALL, mini)
        assert c100.legs[0].multiplier == 100 and c10.legs[0].multiplier == 10
        assert c10.estimated_net_premium_per_unit == c100.estimated_net_premium_per_unit
        assert c100.estimated_net_premium_total == c10.estimated_net_premium_total * 10
        assert c10.max_loss_usd * 10 == c100.max_loss_usd

    def test_custom_multiplier_50_collateral(self):
        chain = build_multiplier_chain(50, "PLTR")
        cand = candidate_for(StrategyType.CASH_SECURED_PUT, chain)
        strike = cand.legs[0].strike
        assert cand.collateral_required_usd == strike * 50
        assert cand.collateral_required_usd != strike * 100


class TestFixtureHonesty:
    """The fixtures must never masquerade as a live feed."""

    def test_fixture_chain_is_tagged_synthetic(self):
        chain = build_chain("SPY")
        assert chain.feed_tier.value == "fixture"
        assert chain.is_indicative is False
        assert FIXTURE_WARNING in chain.warnings
        assert all(q.feed_tier.value == "fixture" and FIXTURE_WARNING in q.data_warnings for q in chain.contracts)

    def test_expected_fixture_shape(self):
        chain = build_chain("SPY")
        assert len(chain.expirations) == 3
        for q in chain.contracts[:50]:
            assert q.bid is not None and q.ask is not None and q.ask >= q.bid
            assert q.open_interest > 0 and q.volume > 0
            assert q.greeks.delta is not None and q.implied_volatility


# =====================================================================================
# Part B – simulation accounting (DB-backed, deterministic fixtures)
# =====================================================================================


def _mid_net_total(cand: StrategyCandidate) -> float:
    """Net option cash flow if everything filled at MID (+credit / -debit) – the unattainable ideal."""
    total = 0.0
    for leg in cand.legs:
        if leg.leg_role == LegRole.UNDERLYING_STOCK:
            continue
        sign = 1 if leg.side == OptionSide.SHORT else -1
        total += sign * float(leg.mid) * leg.multiplier * cand.contracts_qty * leg.ratio
    return total


def _spread_cost_total(cand: StrategyCandidate) -> float:
    return sum(
        float(leg.ask - leg.bid) * leg.multiplier * cand.contracts_qty * leg.ratio
        for leg in cand.legs
        if leg.leg_role != LegRole.UNDERLYING_STOCK
    )


def _short_bid_total(cand: StrategyCandidate) -> float:
    return sum(
        float(leg.bid) * leg.multiplier * cand.contracts_qty * leg.ratio
        for leg in cand.legs
        if leg.leg_role != LegRole.UNDERLYING_STOCK and leg.side == OptionSide.SHORT
    )


def _status_of(group) -> str:
    raw = g(group, "status")
    return str(getattr(raw, "value", raw)).lower()


def _n_option_contracts(cand: StrategyCandidate) -> int:
    return sum(
        cand.contracts_qty * leg.ratio for leg in cand.legs if leg.leg_role != LegRole.UNDERLYING_STOCK
    )


class TestBidAskFillRealism:
    def test_buy_fills_at_ask_or_worse_never_mid(self, options_lab):
        lab = options_lab
        chain = build_chain("SPY")
        slug = lab.new_account(10_000)
        cand = candidate_for(StrategyType.LONG_CALL, chain)
        leg = cand.legs[0]
        lab.open(slug, cand)
        a = lab.acct(slug)
        cash_drop = 10_000 - f(a.cash_balance)
        ask_cost = float(leg.ask) * leg.multiplier
        mid_cost = float(leg.mid) * leg.multiplier
        assert cash_drop > mid_cost + 0.5, "buy must never fill at free mid"
        assert cash_drop >= ask_cost - 0.01, "buy fills at Ask (+slippage) plus fees"
        assert cash_drop <= ask_cost + 0.5 * float(leg.ask - leg.bid) * 100 + 25, "slippage bounded"
        assert_equity_identity(a)

    def test_sell_fills_at_bid_or_worse_never_mid(self, options_lab):
        lab = options_lab
        chain = build_chain("PLTR")
        slug = lab.new_account(10_000)
        cand = candidate_for(StrategyType.CASH_SECURED_PUT, chain)
        leg = cand.legs[0]
        lab.open(slug, cand)
        a = lab.acct(slug)
        gross = f(a.premium_collected_gross)
        assert gross < float(leg.mid) * leg.multiplier - 0.01, "sell must never fill at free mid"
        assert gross <= float(leg.bid) * leg.multiplier + 0.01, "sell fills at Bid (-slippage)"
        assert gross >= (float(leg.bid) - float(leg.ask - leg.bid)) * leg.multiplier - 0.01

    @pytest.mark.parametrize(
        "stype", [StrategyType.LONG_CALL, StrategyType.BULL_PUT_CREDIT_SPREAD, StrategyType.IRON_CONDOR]
    )
    def test_round_trip_at_same_quotes_loses_spread_and_fees(self, options_lab, stype):
        lab = options_lab
        chain = build_chain("SPY")
        slug = lab.new_account(10_000)
        cand = candidate_for(stype, chain)
        strategy_id = lab.open(slug, cand)
        lab.close(strategy_id, chain, reason="qa_roundtrip", now=AS_OF)
        a = lab.acct(slug)
        assert f(a.realized_pnl) < 0, "instant round trip must cost spread + fees, never profit"
        bound = 2 * _spread_cost_total(cand) + 3 * _n_option_contracts(cand) * 2 + 5
        assert f(a.realized_pnl) >= -bound
        assert f(a.cash_reserved) == pytest.approx(0, abs=0.01)
        assert f(a.equity) == pytest.approx(f(a.cash_balance), abs=0.05)
        assert_equity_identity(a)

    def test_fee_accounting_per_contract_and_per_leg(self, options_lab):
        lab = options_lab
        chain = build_chain("SPY")
        slug = lab.new_account(25_000)
        cand = candidate_for(StrategyType.IRON_CONDOR, chain, qty=2)
        profile = rule_profile()
        n = _n_option_contracts(cand)
        assert n == 8  # 4 legs x 2 contracts
        lab.open(slug, cand)
        fees = f(lab.acct(slug).fees_paid)
        floor = n * float(profile.commission_per_contract_usd)
        assert fees >= floor - 0.01, "commission is charged per contract on every leg"
        assert fees <= n * 2.0, "fees are per-contract, not a notional percentage"

    def test_one_vs_two_contracts_double_fees(self, options_lab):
        lab = options_lab
        chain = build_chain("PLTR")
        s1 = lab.new_account(20_000)
        s2 = lab.new_account(20_000)
        lab.open(s1, candidate_for(StrategyType.CASH_SECURED_PUT, chain, qty=1))
        lab.open(s2, candidate_for(StrategyType.CASH_SECURED_PUT, chain, qty=2))
        f1, f2 = f(lab.acct(s1).fees_paid), f(lab.acct(s2).fees_paid)
        assert f2 == pytest.approx(2 * f1, rel=0.15)


class TestStrictPremiumLiabilityAccounting:
    """Received credit is a LIABILITY until the position is closed / expired / assigned."""

    @pytest.mark.parametrize(
        "symbol,stype",
        [
            ("SPY", StrategyType.BULL_PUT_CREDIT_SPREAD),
            ("SPY", StrategyType.BEAR_CALL_CREDIT_SPREAD),
            ("PLTR", StrategyType.CASH_SECURED_PUT),
            ("SPY", StrategyType.IRON_CONDOR),
        ],
    )
    def test_open_credit_books_no_realized_profit(self, options_lab, symbol, stype):
        lab = options_lab
        chain = build_chain(symbol)
        slug = lab.new_account(10_000)
        cand = candidate_for(stype, chain)
        lab.open(slug, cand)
        a = lab.acct(slug)

        gross = f(a.premium_collected_gross)
        fees = f(a.fees_paid)
        net_credit = f(a.cash_balance) - 10_000 + fees  # what the fills actually brought in
        assert net_credit > 0
        assert net_credit <= _mid_net_total(cand) + 0.01, "credit can never exceed the mid-based ideal"
        assert net_credit - 0.01 <= gross <= _short_bid_total(cand) + 0.01, "gross premium is Bid-based"
        # cash rises by the credit minus fees ... but it is NOT profit:
        assert f(a.realized_pnl) <= 0.01
        assert f(a.realized_pnl) >= -(fees + 0.02)
        assert f(a.premium_realized_net) <= 0.01
        assert f(a.short_options_liability) >= net_credit * 0.9, "short side is carried as a liability"
        # equity is neutral minus fees + spread/slippage (never plus the premium)
        loss = 10_000 - f(a.equity)
        assert loss > 0
        assert loss <= fees + 2 * _spread_cost_total(cand) + 1
        assert f(a.equity) < 10_000 + net_credit * 0.5
        assert_equity_identity(a)
        grp = lab.groups(slug)[0]
        assert f(g(grp, "realized_pnl")) <= 0.01
        assert abs(f(g(grp, "net_entry_cash_flow")) - net_credit) <= fees + 0.05
        assert f(g(grp, "open_liability_usd")) >= net_credit * 0.9

    def test_credit_spread_expire_otm_realizes_profit_and_releases_collateral(self, options_lab):
        lab = options_lab
        chain = build_chain("SPY")
        slug = lab.new_account(10_000)
        cand = candidate_for(StrategyType.BULL_PUT_CREDIT_SPREAD, chain)
        strategy_id = lab.open(slug, cand)
        opened = lab.acct(slug)
        net_credit = f(opened.cash_balance) - 10_000 + f(opened.fees_paid)
        assert net_credit > 0 and f(opened.cash_reserved) > 0

        settle = expiry_chain("SPY", EXP_21, spot=700.0, ladder_spot=695.0)  # far above the short put
        lab.expire(slug, settle, now=settle.as_of)
        a = lab.acct(slug)
        fees = f(a.fees_paid)
        assert f(a.cash_reserved) == pytest.approx(0, abs=0.01)
        assert f(a.short_options_liability) == pytest.approx(0, abs=0.05)
        assert f(a.realized_pnl) > 0
        assert f(a.realized_pnl) == pytest.approx(net_credit - fees, abs=0.1)
        assert f(a.premium_realized_net) > 0
        assert f(a.premium_realized_net) <= net_credit + 0.01
        assert f(a.equity) == pytest.approx(10_000 + f(a.realized_pnl), abs=0.1)
        assert f(a.open_strategies_count) == 0 and f(a.closed_strategies_count) == 1
        grp = lab.group(strategy_id)
        assert _status_of(grp) in {"expired", "closed"}
        assert_equity_identity(a)

    def test_credit_spread_close_at_profit_realizes_exact_difference(self, options_lab):
        lab = options_lab
        chain = build_chain("SPY")
        slug = lab.new_account(10_000)
        cand = candidate_for(StrategyType.BULL_PUT_CREDIT_SPREAD, chain)
        strategy_id = lab.open(slug, cand)
        opened = lab.acct(slug)
        net_credit = f(opened.cash_balance) - 10_000 + f(opened.fees_paid)

        later = moved_chain("SPY", spot=720.0, as_of=AS_OF.replace(day=12, hour=11))
        lab.close(strategy_id, later, reason="take_profit", now=later.as_of)
        a = lab.acct(slug)
        assert f(a.realized_pnl) > 0
        assert f(a.realized_pnl) < net_credit, "buy-to-close cost and fees reduce the retained credit"
        assert f(a.cash_reserved) == pytest.approx(0, abs=0.01)
        assert f(a.equity) == pytest.approx(f(a.cash_balance), abs=0.05)

    def test_long_debit_open_is_not_a_realized_loss(self, options_lab):
        lab = options_lab
        chain = build_chain("SPY")
        slug = lab.new_account(10_000)
        lab.open(slug, candidate_for(StrategyType.LONG_CALL, chain))
        a = lab.acct(slug)
        assert f(a.long_options_market_value) > 0
        assert f(a.realized_pnl) >= -(f(a.fees_paid) + 0.02)
        assert f(a.premium_collected_gross) == pytest.approx(0, abs=0.01)
        assert f(a.cash_reserved) == pytest.approx(0, abs=0.01)


class TestMarkToMarket:
    def test_long_call_gains_when_spot_rises(self, options_lab):
        lab = options_lab
        chain = build_chain("SPY")
        slug = lab.new_account(10_000)
        lab.open(slug, candidate_for(StrategyType.LONG_CALL, chain))
        before = lab.acct(slug)
        up = moved_chain("SPY", spot=709.0, as_of=AS_OF.replace(hour=14))
        lab.mtm(slug, up, now=up.as_of)
        after = lab.acct(slug)
        assert f(after.equity) > f(before.equity)
        assert f(after.unrealized_pnl) > f(before.unrealized_pnl)
        assert f(after.realized_pnl) == pytest.approx(f(before.realized_pnl), abs=0.01)
        assert_equity_identity(after)

    def test_short_put_liability_grows_when_spot_falls(self, options_lab):
        lab = options_lab
        chain = build_chain("PLTR")
        slug = lab.new_account(10_000)
        lab.open(slug, candidate_for(StrategyType.CASH_SECURED_PUT, chain))
        before = lab.acct(slug)
        down = moved_chain("PLTR", spot=38.5, as_of=AS_OF.replace(hour=14))
        lab.mtm(slug, down, now=down.as_of)
        after = lab.acct(slug)
        assert f(after.short_options_liability) > f(before.short_options_liability)
        assert f(after.equity) < f(before.equity)
        assert f(after.realized_pnl) == pytest.approx(f(before.realized_pnl), abs=0.01)


class TestCollateralAndMarginConservation:
    def test_debit_structures_lock_no_collateral(self, options_lab):
        lab = options_lab
        chain = build_chain("SPY")
        for stype in (StrategyType.BULL_CALL_SPREAD, StrategyType.BEAR_PUT_SPREAD, StrategyType.LONG_PUT):
            slug = lab.new_account(10_000)
            cand = candidate_for(stype, chain)
            lab.open(slug, cand)
            a = lab.acct(slug)
            debit = -f(cand.estimated_net_premium_total)
            assert f(a.cash_reserved) == pytest.approx(0, abs=0.01)
            drop = 10_000 - f(a.cash_balance)
            assert drop >= -_mid_net_total(cand) - 0.01, "never cheaper than mid"
            assert drop <= debit + 0.5 * _spread_cost_total(cand) + 25
            assert drop <= f(cand.max_loss_usd) + 0.5 * _spread_cost_total(cand) + 25, "defined risk"

    def test_cash_secured_put_reserves_full_strike_notional(self, options_lab):
        lab = options_lab
        chain = build_chain("PLTR")
        slug = lab.new_account(10_000)
        cand = candidate_for(StrategyType.CASH_SECURED_PUT, chain, qty=2)
        strike = float(cand.legs[0].strike)
        lab.open(slug, cand)
        a = lab.acct(slug)
        assert f(a.cash_reserved) == pytest.approx(strike * 100 * 2, abs=0.01)
        assert f(a.free_cash) == pytest.approx(f(a.cash_balance) - f(a.cash_reserved), abs=0.01)
        assert f(a.free_cash) >= 0

    def test_covered_call_encumbers_shares_without_cash_collateral(self, options_lab):
        lab = options_lab
        chain = build_chain("PLTR")
        slug = lab.new_account(10_000)
        lab.seed_shares(slug, "PLTR", 100, 42.0)
        cand = candidate_for(StrategyType.COVERED_CALL, chain)
        lab.open(slug, cand)
        a = lab.acct(slug)
        lot = lab.lot(slug, "PLTR")
        assert int(g(lot, "shares")) == 100
        assert int(g(lot, "encumbered_shares")) == 100 * cand.contracts_qty
        assert f(a.cash_reserved) == pytest.approx(0, abs=0.01)

    def test_buy_write_creates_and_encumbers_shares_and_conserves_equity(self, options_lab):
        lab = options_lab
        chain = build_chain("PLTR")
        slug = lab.new_account(10_000)
        cand = candidate_for(StrategyType.COVERED_CALL, chain, buy_write=True)
        assert any(leg.leg_role == LegRole.UNDERLYING_STOCK for leg in cand.legs)
        lab.open(slug, cand)
        a = lab.acct(slug)
        lot = lab.lot(slug, "PLTR")
        assert int(g(lot, "shares")) == 100
        assert int(g(lot, "encumbered_shares")) == 100
        assert f(a.stock_holdings_value) == pytest.approx(42.0 * 100, rel=0.02)
        # stock bought at spot, call sold: equity only loses fees + spread
        assert 0 < 10_000 - f(a.equity) <= f(a.fees_paid) + 2 * _spread_cost_total(cand) + 5
        assert_equity_identity(a)

    def test_credit_spread_free_cash_drops_by_exact_max_loss_plus_fees(self, options_lab):
        lab = options_lab
        chain = build_chain("SPY")
        slug = lab.new_account(10_000)
        cand = candidate_for(StrategyType.BULL_PUT_CREDIT_SPREAD, chain)
        width = float(cand.legs[0].strike - cand.legs[1].strike)
        lab.open(slug, cand)
        a = lab.acct(slug)
        fees = f(a.fees_paid)
        net_credit = f(a.cash_balance) - 10_000 + fees
        assert f(a.cash_reserved) == pytest.approx(width * 100, abs=0.01)
        assert f(a.free_cash) == pytest.approx(f(a.cash_balance) - f(a.cash_reserved), abs=0.01)
        # algebra: free_cash = start + credit - fees - width*mult  =>  drop = (width*mult - credit) + fees
        drop = 10_000 - f(a.free_cash)
        assert drop == pytest.approx(width * 100 - net_credit + fees, abs=0.05)
        assert drop <= f(cand.max_loss_usd) + _spread_cost_total(cand) + fees + 1  # max loss + costs

    def test_credit_spread_equity_not_inflated_when_short_liability_counted(self, options_lab):
        """Regression: long MV without short liability would fake a large equity jump at open."""
        lab = options_lab
        chain = build_chain("SPY")
        slug = lab.new_account(25_000)
        cand = candidate_for(StrategyType.BULL_PUT_CREDIT_SPREAD, chain)
        lab.open(slug, cand)
        a = lab.acct(slug)
        assert_equity_identity(a)
        assert f(a.short_options_liability) > 0
        assert f(a.long_options_market_value) > 0
        # At entry marks, equity stays near starting capital (fees only), not +long MV.
        assert f(a.equity) <= 25_000 + 1.0
        assert f(a.equity) >= 25_000 - f(a.fees_paid) - 5.0
        inflated = f(a.cash_balance) + f(a.long_options_market_value)
        assert inflated - f(a.equity) == pytest.approx(f(a.short_options_liability), abs=0.05)
        assert inflated - 25_000 > 100, "sanity: missing liability would look like a large phantom gain"

    def test_iron_condor_reserves_single_side_max_width(self, options_lab):
        lab = options_lab
        chain = build_chain("SPY")
        slug = lab.new_account(10_000)
        cand = candidate_for(StrategyType.IRON_CONDOR, chain, width_steps=2)
        put_w = float(cand.legs[0].strike - cand.legs[1].strike)
        call_w = float(cand.legs[3].strike - cand.legs[2].strike)
        lab.open(slug, cand)
        a = lab.acct(slug)
        assert f(a.cash_reserved) == pytest.approx(max(put_w, call_w) * 100, abs=0.01)
        assert f(a.cash_reserved) < (put_w + call_w) * 100, "only ONE wing can finish in the money"
        fees = f(a.fees_paid)
        net_credit = f(a.cash_balance) - 10_000 + fees
        drop = 10_000 - f(a.free_cash)
        assert drop == pytest.approx(max(put_w, call_w) * 100 - net_credit + fees, abs=0.05)
        assert drop <= f(cand.max_loss_usd) + _spread_cost_total(cand) + fees + 1

    @pytest.mark.parametrize("mult,symbol", [(10, "SPY"), (50, "PLTR")])
    def test_non_100_multiplier_cash_flows_and_collateral(self, options_lab, mult, symbol):
        lab = options_lab
        chain = build_multiplier_chain(mult, symbol)
        slug = lab.new_account(10_000)

        long_cand = candidate_for(StrategyType.LONG_CALL, chain)
        lab.open(slug, long_cand)
        a = lab.acct(slug)
        ask = float(long_cand.legs[0].ask)
        drop = 10_000 - f(a.cash_balance)
        assert drop >= ask * mult - 0.01
        assert drop < ask * mult + 0.5 * float(long_cand.legs[0].ask - long_cand.legs[0].bid) * mult + 10
        assert drop < ask * 100 * 0.6, "multiplier must not be silently replaced by 100"
        assert f(a.fees_paid) < 5.0, "fees are per contract regardless of multiplier"

        slug2 = lab.new_account(10_000)
        csp = candidate_for(StrategyType.CASH_SECURED_PUT, chain)
        lab.open(slug2, csp)
        a2 = lab.acct(slug2)
        assert f(a2.cash_reserved) == pytest.approx(float(csp.legs[0].strike) * mult, abs=0.01)
        assert abs(f(a2.cash_reserved) - float(csp.legs[0].strike) * 100) > 1.0
        assert f(a2.premium_collected_gross) <= float(csp.legs[0].bid) * mult + 0.01

    def test_collateral_fully_released_on_close(self, options_lab):
        lab = options_lab
        chain = build_chain("SPY")
        slug = lab.new_account(10_000)
        ids = [
            lab.open(slug, candidate_for(StrategyType.BULL_PUT_CREDIT_SPREAD, chain)),
            lab.open(slug, candidate_for(StrategyType.IRON_CONDOR, chain, dte=35)),
        ]
        assert f(lab.acct(slug).cash_reserved) > 0
        for strategy_id in ids:
            lab.close(strategy_id, chain, reason="qa_release")
        a = lab.acct(slug)
        assert f(a.cash_reserved) == pytest.approx(0, abs=0.01)
        assert f(a.free_cash) == pytest.approx(f(a.cash_balance), abs=0.01)
