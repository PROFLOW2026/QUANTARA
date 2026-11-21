"""QUANTARA Options – full lifecycle tests for ALL 9 strategies (open -> MTM -> close/roll/expiry).

Uses SYNTHETIC fixture chains (``DataFeedTier.FIXTURE``) and the disposable broker-test
PostgreSQL via ``OptionsRepository`` + ``OptionsSimulationEngine``.  Nothing here proves live
Alpaca / OPRA availability.

Symbol choice (keeps cash/collateral realistic for $5K-$25K accounts):
* PLTR (~$42)  – CSP / Wheel / Covered Call / spreads / condor expiry & assignment tests
* SPY (~$695)  – defined-risk open/close, pin-risk, gap-down stress (cash-settled style checks)
* AAPL (~$225) – ex-dividend early assignment
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import pytest

from quantara_options.contracts import (
    LegRole,
    OptionRight,
    OptionSide,
    StrategyType,
)
from tests.fixtures.options_fixtures import (  # noqa: F401  (options_lab is a pytest fixture)
    AS_OF,
    EXP_5,
    EXP_21,
    EXP_35,
    ET,
    PIN_AS_OF,
    PIN_SHORT_CALL_STRIKE,
    assert_equity_identity,
    build_chain,
    build_closed_market_chain,
    build_crash_scenario,
    build_ex_dividend_scenario,
    build_pin_risk_scenario,
    candidate_for,
    condor_for_pin_scenario,
    expiration_for_dte,
    expiry_chain,
    f,
    find_quote,
    g,
    make_candidate,
    moved_chain,
    nearest_strike,
    opt_leg,
    options_lab,
    strike_at_or_above,
)

C, P = OptionRight.CALL, OptionRight.PUT
L, S = OptionSide.LONG, OptionSide.SHORT


# ------------------------------------------------------------------ helpers

def _status(lab, strategy_id: str) -> str:
    raw = g(lab.group(strategy_id), "status")
    return str(getattr(raw, "value", raw)).lower()


def _open_ids(lab, slug: str) -> list[str]:
    return [str(g(x, "id")) for x in lab.groups(slug, "open")]


def _net_credit(before, after) -> float:
    """Option cash brought in by one fill set (credit +, debit -), fees excluded."""
    return (f(after.cash_balance) - f(before.cash_balance)) + (f(after.fees_paid) - f(before.fees_paid))


def _is_terminal(status: str) -> bool:
    return status in {"closed", "expired", "assigned", "rolled"}


PLTR = "PLTR"


# ======================================================================================
# 1) Long CALL  /  2) Long PUT
# ======================================================================================


class TestLongCallAndPut:
    @pytest.mark.parametrize("right,stype", [(C, StrategyType.LONG_CALL), (P, StrategyType.LONG_PUT)])
    def test_open_mtm_profit_close(self, options_lab, right, stype):
        lab = options_lab
        chain = build_chain("SPY")
        slug = lab.new_account(10_000)
        sid_ = lab.open(slug, candidate_for(stype, chain))
        opened = lab.acct(slug)
        assert _status(lab, sid_) == "open"
        assert f(opened.cash_reserved) == pytest.approx(0, abs=0.01)

        move = 712.0 if right == C else 678.0
        later = moved_chain("SPY", spot=move, as_of=AS_OF.replace(day=11, hour=11))
        lab.mtm(slug, later, now=later.as_of)
        marked = lab.acct(slug)
        assert f(marked.unrealized_pnl) > 0 and f(marked.equity) > f(opened.equity)
        assert f(marked.realized_pnl) == pytest.approx(f(opened.realized_pnl), abs=0.01)

        lab.close(sid_, later, reason="take_profit", now=later.as_of)
        closed = lab.acct(slug)
        assert _is_terminal(_status(lab, sid_))
        assert f(closed.realized_pnl) > 0
        assert f(closed.unrealized_pnl) == pytest.approx(0, abs=0.05)
        assert f(closed.equity) == pytest.approx(f(closed.cash_balance), abs=0.05)
        assert f(closed.closed_strategies_count) == 1 and f(closed.open_strategies_count) == 0
        assert_equity_identity(closed)

    @pytest.mark.parametrize("right,stype,spot", [(C, StrategyType.LONG_CALL, 38.0), (P, StrategyType.LONG_PUT, 46.0)])
    def test_expire_otm_loses_exactly_the_premium(self, options_lab, right, stype, spot):
        lab = options_lab
        chain = build_chain(PLTR)
        slug = lab.new_account(10_000)
        before = lab.acct(slug)
        sid_ = lab.open(slug, candidate_for(stype, chain))
        opened = lab.acct(slug)
        debit = -_net_credit(before, opened)
        assert debit > 0

        settle = expiry_chain(PLTR, EXP_21, spot=spot, ladder_spot=42.0)
        lab.expire(slug, settle, now=settle.as_of)
        a = lab.acct(slug)
        assert _is_terminal(_status(lab, sid_))
        assert f(a.realized_pnl) == pytest.approx(-(debit + f(a.fees_paid)), abs=0.15)
        assert f(a.long_options_market_value) == pytest.approx(0, abs=0.05)
        assert f(a.equity) == pytest.approx(10_000 - debit - f(a.fees_paid), abs=0.15)

    @pytest.mark.parametrize("right,stype,spot", [(C, StrategyType.LONG_CALL, 46.0), (P, StrategyType.LONG_PUT, 38.0)])
    def test_expire_itm_is_resolved_not_left_open(self, options_lab, right, stype, spot):
        lab = options_lab
        chain = build_chain(PLTR)
        slug = lab.new_account(10_000)
        before = lab.acct(slug)
        cand = candidate_for(stype, chain)
        strike = float(cand.legs[0].strike)
        sid_ = lab.open(slug, cand)
        debit = -_net_credit(before, lab.acct(slug))

        settle = expiry_chain(PLTR, EXP_21, spot=spot, ladder_spot=42.0)
        lab.expire(slug, settle, now=settle.as_of)
        lab.mtm(slug, settle, now=settle.as_of)
        a = lab.acct(slug)
        assert _is_terminal(_status(lab, sid_)), "ITM long must be exercised / auto-closed, never left open"
        assert _open_ids(lab, slug) == []
        intrinsic = abs(spot - strike) * 100
        # Whether closed for cash or exercised into shares (marked at spot), equity captures intrinsic.
        assert f(a.equity) == pytest.approx(10_000 - debit + intrinsic - f(a.fees_paid), abs=max(15.0, 0.05 * intrinsic))
        assert f(a.cash_balance) > -0.01


# ======================================================================================
# 3) Covered Call (buy-write + existing shares + encumbrance)
# ======================================================================================


class TestCoveredCall:
    def test_buy_write_then_expire_otm_keeps_shares_and_premium(self, options_lab):
        lab = options_lab
        chain = build_chain(PLTR)
        slug = lab.new_account(10_000)
        before = lab.acct(slug)
        cand = candidate_for(StrategyType.COVERED_CALL, chain, buy_write=True)
        sid_ = lab.open(slug, cand)
        lot = lab.lot(slug, PLTR)
        assert int(lot["shares"]) == 100 and int(lot["encumbered_shares"]) == 100

        settle = expiry_chain(PLTR, EXP_21, spot=42.5, ladder_spot=42.0)  # below the 44 strike
        lab.expire(slug, settle, now=settle.as_of)
        lab.mtm(slug, settle, now=settle.as_of)
        lot = lab.lot(slug, PLTR)
        a = lab.acct(slug)
        assert _is_terminal(_status(lab, sid_))
        assert int(lot["shares"]) == 100, "shares stay after the call expires worthless"
        assert int(lot["encumbered_shares"]) == 0, "shares are released from the covered call"
        assert f(a.premium_realized_net) > 0
        assert f(a.equity) > 10_000 - 120  # premium offsets costs; stock valued ~spot
        assert f(before.cash_balance) == 10_000

    def test_existing_shares_called_away_realizes_combined_stock_and_premium_pnl(self, options_lab):
        lab = options_lab
        chain = build_chain(PLTR)
        slug = lab.new_account(10_000)
        lab.seed_shares(slug, PLTR, 100, 42.0)
        before = lab.acct(slug)
        cand = candidate_for(StrategyType.COVERED_CALL, chain)
        strike = float(cand.legs[0].strike)
        sid_ = lab.open(slug, cand)
        opened = lab.acct(slug)
        credit = _net_credit(before, opened)
        basis = float(lab.lot(slug, PLTR)["avg_cost_basis"])

        settle = expiry_chain(PLTR, EXP_21, spot=strike + 3.0, ladder_spot=42.0)
        lab.expire(slug, settle, now=settle.as_of)
        a = lab.acct(slug)
        lot = lab.lot(slug, PLTR)
        assert _is_terminal(_status(lab, sid_))
        assert lot is None or int(lot["shares"]) == 0, "shares called away at the strike"
        assert f(a.cash_balance) == pytest.approx(
            f(before.cash_balance) + credit + strike * 100 - f(a.fees_paid), abs=0.15
        )
        expected_realized = (strike - basis) * 100 + credit - f(a.fees_paid)
        assert f(a.realized_pnl) == pytest.approx(expected_realized, abs=0.2)
        assert f(a.equity) == pytest.approx(f(a.cash_balance), abs=0.05)

    def test_encumbered_shares_cannot_back_a_second_call(self, options_lab):
        lab = options_lab
        chain = build_chain(PLTR)
        slug = lab.new_account(10_000)
        lab.seed_shares(slug, PLTR, 100, 42.0)
        lab.open(slug, candidate_for(StrategyType.COVERED_CALL, chain))
        second = candidate_for(StrategyType.COVERED_CALL, chain, dte=35)
        res = lab.try_open(slug, second, now=AS_OF)
        assert res.ok is False, "100 shares cannot back two covered calls"
        assert res.reasons, "rejection must be explained"
        lot = lab.lot(slug, PLTR)
        assert int(lot["encumbered_shares"]) == 100 and int(lot["shares"]) == 100
        cc_open = [x for x in lab.groups(slug, "open") if "covered_call" in str(g(x, "strategy_type"))]
        assert len(cc_open) == 1


# ======================================================================================
# 4) Cash-Secured PUT
# ======================================================================================


class TestCashSecuredPut:
    def test_cash_reserved_then_otm_expiry_realizes_premium(self, options_lab):
        lab = options_lab
        chain = build_chain(PLTR)
        slug = lab.new_account(10_000)
        before = lab.acct(slug)
        cand = candidate_for(StrategyType.CASH_SECURED_PUT, chain)
        strike = float(cand.legs[0].strike)
        sid_ = lab.open(slug, cand)
        opened = lab.acct(slug)
        credit = _net_credit(before, opened)
        assert f(opened.cash_reserved) == pytest.approx(strike * 100, abs=0.01)
        assert f(opened.realized_pnl) <= 0.01, "premium is a liability until expiry"

        settle = expiry_chain(PLTR, EXP_21, spot=strike + 2.0, ladder_spot=42.0)
        lab.expire(slug, settle, now=settle.as_of)
        a = lab.acct(slug)
        assert _is_terminal(_status(lab, sid_))
        assert f(a.cash_reserved) == pytest.approx(0, abs=0.01)
        assert f(a.realized_pnl) == pytest.approx(credit - f(a.fees_paid), abs=0.15)
        assert f(a.premium_realized_net) > 0
        assert lab.lot(slug, PLTR) is None or int(lab.lot(slug, PLTR)["shares"]) == 0

    def test_itm_expiry_assigns_100_shares_at_strike(self, options_lab):
        lab = options_lab
        chain = build_chain(PLTR)
        slug = lab.new_account(10_000)
        before = lab.acct(slug)
        cand = candidate_for(StrategyType.CASH_SECURED_PUT, chain)
        strike = float(cand.legs[0].strike)
        credit_ps = float(cand.legs[0].bid)
        sid_ = lab.open(slug, cand)
        opened = lab.acct(slug)
        credit = _net_credit(before, opened)

        spot = strike - 2.0
        settle = expiry_chain(PLTR, EXP_21, spot=spot, ladder_spot=42.0)
        lab.expire(slug, settle, now=settle.as_of)
        lab.mtm(slug, settle, now=settle.as_of)
        a = lab.acct(slug)
        lot = lab.lot(slug, PLTR)
        assert _is_terminal(_status(lab, sid_))
        assert lot is not None and int(lot["shares"]) == 100, "assignment delivers exactly 100 shares"
        assert int(lot["encumbered_shares"]) == 0
        basis = float(lot["avg_cost_basis"])
        assert strike - credit_ps - 0.2 <= basis <= strike + 0.01  # raw strike or premium-adjusted basis
        assert f(opened.cash_balance) - f(a.cash_balance) == pytest.approx(strike * 100, abs=5.0)
        assert f(a.cash_reserved) == pytest.approx(0, abs=0.01)
        assert f(a.free_cash) >= -0.01
        # equity = start + credit - fees + (spot - strike) * 100  (stock marked at spot)
        assert f(a.equity) == pytest.approx(
            10_000 + credit - f(a.fees_paid) + (spot - strike) * 100, abs=8.0
        )


# ======================================================================================
# 5) Wheel – IDLE -> SHORT_PUT -> HOLDING_SHARES -> SHORT_CALL -> IDLE
# ======================================================================================


class TestWheelFullCycle:
    @pytest.mark.parametrize("capital", [5_000, 10_000])
    def test_complete_wheel_cycle_adjusted_basis_and_cumulative_profit(self, options_lab, capital):
        lab = options_lab
        slug = lab.new_account(capital)
        assert lab.wheel_state(slug, PLTR) == "idle"
        a0 = lab.acct(slug)

        # ---- leg 1: short put (state SHORT_PUT) ----
        chain = build_chain(PLTR)
        put_cand = candidate_for(StrategyType.WHEEL, chain)
        put_strike = float(put_cand.legs[0].strike)
        assert put_cand.legs[0].leg_role == LegRole.SHORT_PUT
        put_id = lab.open(slug, put_cand)
        a1 = lab.acct(slug)
        put_credit = _net_credit(a0, a1)
        assert lab.wheel_state(slug, PLTR) == "short_put"
        assert f(a1.cash_reserved) == pytest.approx(put_strike * 100, abs=0.01)

        # ---- assigned at expiry (state HOLDING_SHARES) ----
        spot_down = put_strike - 2.0
        settle_put = expiry_chain(PLTR, EXP_21, spot=spot_down, ladder_spot=42.0)
        lab.expire(slug, settle_put, now=settle_put.as_of)
        a2 = lab.acct(slug)
        lot = lab.lot(slug, PLTR)
        assert _is_terminal(_status(lab, put_id))
        assert lab.wheel_state(slug, PLTR) == "holding_shares"
        assert int(lot["shares"]) == 100
        adj_basis = float(lot["avg_cost_basis"])
        put_credit_ps = put_credit / 100.0
        assert put_strike - put_credit_ps - 0.2 <= adj_basis <= put_strike + 0.01
        assert f(a2.cash_reserved) == pytest.approx(0, abs=0.01)

        # ---- covered call above cost basis (state SHORT_CALL) ----
        t2 = datetime(2026, 7, 2, 9, 35, tzinfo=ET)
        chain2 = moved_chain(PLTR, spot=spot_down, as_of=t2, expirations=[EXP_35])
        call_strike = strike_at_or_above(chain2, EXP_35, C, max(put_strike - put_credit_ps + 0.5, spot_down * 1.04))
        call_cand = make_candidate(
            StrategyType.WHEEL, chain2, [opt_leg(chain2, EXP_35, C, S, call_strike)], expiration=EXP_35
        )
        assert call_cand.legs[0].leg_role == LegRole.SHORT_CALL
        assert float(call_strike) >= adj_basis, "never sell the call below the (adjusted) cost basis"
        call_id = lab.open(slug, call_cand, now=t2)
        a3 = lab.acct(slug)
        call_credit = _net_credit(a2, a3)
        assert lab.wheel_state(slug, PLTR) == "short_call"
        assert int(lab.lot(slug, PLTR)["encumbered_shares"]) == 100

        # ---- called away (state IDLE) ----
        settle_call = expiry_chain(PLTR, EXP_35, spot=float(call_strike) + 2.0, ladder_spot=42.0)
        lab.expire(slug, settle_call, now=settle_call.as_of)
        a4 = lab.acct(slug)
        lot_after = lab.lot(slug, PLTR)
        assert _is_terminal(_status(lab, call_id))
        assert lab.wheel_state(slug, PLTR) == "idle"
        assert lot_after is None or int(lot_after["shares"]) == 0
        assert f(a4.cash_reserved) == pytest.approx(0, abs=0.01)

        expected_final = (
            capital + put_credit + call_credit + (float(call_strike) - put_strike) * 100 - f(a4.fees_paid)
        )
        assert f(a4.equity) == pytest.approx(expected_final, abs=0.5)
        assert f(a4.realized_pnl) == pytest.approx(expected_final - capital, abs=0.5)
        assert f(a4.realized_pnl) > 0, "a completed wheel with a higher call strike is profitable"
        assert f(a4.closed_strategies_count) == 2 and f(a4.open_strategies_count) == 0
        assert_equity_identity(a4)

    def test_wheel_on_5k_account_cannot_sell_put_it_cannot_secure(self, options_lab):
        lab = options_lab
        slug = lab.new_account(5_000)
        chain = build_chain("AAPL")  # 21k of collateral on a 5k account
        cand = candidate_for(StrategyType.WHEEL, chain)
        before = lab.acct(slug)
        res = lab.try_open(slug, cand, now=AS_OF)
        after = lab.acct(slug)
        assert res.ok is False and res.reasons, "unsecured cash-secured put must be rejected, with a reason"
        assert f(after.cash_reserved) == 0 and f(after.cash_balance) == f(before.cash_balance) == 5_000
        assert after.open_strategies_count == 0
        assert lab.wheel_state(slug, "AAPL") == "idle"


# ======================================================================================
# 6) Bull Call Spread  /  7) Bear Put Spread  (debit verticals)
# ======================================================================================


class TestDebitSpreads:
    @pytest.mark.parametrize(
        "stype,max_profit_spot,max_loss_spot",
        [(StrategyType.BULL_CALL_SPREAD, 50.0, 36.0), (StrategyType.BEAR_PUT_SPREAD, 36.0, 50.0)],
    )
    def test_defined_risk_expiry_at_max_profit_and_max_loss(
        self, options_lab, stype, max_profit_spot, max_loss_spot
    ):
        lab = options_lab
        chain = build_chain(PLTR)
        cand = candidate_for(stype, chain)
        width = abs(float(cand.legs[0].strike - cand.legs[1].strike))

        # max profit
        s1 = lab.new_account(10_000)
        b1 = lab.acct(s1)
        id1 = lab.open(s1, cand)
        debit = -_net_credit(b1, lab.acct(s1))
        assert debit > 0 and f(lab.acct(s1).cash_reserved) == pytest.approx(0, abs=0.01)
        settle = expiry_chain(PLTR, EXP_21, spot=max_profit_spot, ladder_spot=42.0)
        lab.expire(s1, settle, now=settle.as_of)
        lab.mtm(s1, settle, now=settle.as_of)
        a1 = lab.acct(s1)
        assert _is_terminal(_status(lab, id1))
        assert f(a1.equity) == pytest.approx(10_000 + width * 100 - debit - f(a1.fees_paid), abs=max(10.0, 0.05 * width * 100))
        assert f(a1.equity) > 10_000 - 5

        # max loss = debit paid
        s2 = lab.new_account(10_000)
        b2 = lab.acct(s2)
        id2 = lab.open(s2, cand)
        debit2 = -_net_credit(b2, lab.acct(s2))
        settle2 = expiry_chain(PLTR, EXP_21, spot=max_loss_spot, ladder_spot=42.0)
        lab.expire(s2, settle2, now=settle2.as_of)
        a2 = lab.acct(s2)
        assert _is_terminal(_status(lab, id2))
        assert f(a2.realized_pnl) == pytest.approx(-(debit2 + f(a2.fees_paid)), abs=0.2)
        assert f(a2.equity) >= 10_000 - float(cand.max_loss_usd) - f(a2.fees_paid) - 60

    @pytest.mark.parametrize(
        "stype,spot", [(StrategyType.BULL_CALL_SPREAD, 712.0), (StrategyType.BEAR_PUT_SPREAD, 678.0)]
    )
    def test_spy_spread_mtm_and_close(self, options_lab, stype, spot):
        lab = options_lab
        chain = build_chain("SPY")
        slug = lab.new_account(10_000)
        cand = candidate_for(stype, chain, width_steps=2)
        sid_ = lab.open(slug, cand)
        later = moved_chain("SPY", spot=spot, as_of=AS_OF.replace(day=12, hour=10))
        lab.mtm(slug, later, now=later.as_of)
        a = lab.acct(slug)
        assert f(a.unrealized_pnl) > 0
        lab.close(sid_, later, reason="take_profit", now=later.as_of)
        closed = lab.acct(slug)
        assert f(closed.realized_pnl) > 0
        assert f(closed.realized_pnl) < float(cand.max_profit_usd), "can never exceed max profit"


# ======================================================================================
# 8) Bull Put / Bear Call credit spreads (+ rolling)
# ======================================================================================


class TestCreditSpreads:
    @pytest.mark.parametrize(
        "stype,safe_spot,breach_spot",
        [
            (StrategyType.BULL_PUT_CREDIT_SPREAD, 44.0, 35.0),
            (StrategyType.BEAR_CALL_CREDIT_SPREAD, 40.0, 49.0),
        ],
    )
    def test_otm_expiry_keeps_credit_and_breach_loses_at_most_defined_risk(
        self, options_lab, stype, safe_spot, breach_spot
    ):
        lab = options_lab
        chain = build_chain(PLTR)
        cand = candidate_for(stype, chain)
        width = abs(float(cand.legs[0].strike - cand.legs[1].strike))

        s1 = lab.new_account(10_000)
        b1 = lab.acct(s1)
        id1 = lab.open(s1, cand)
        credit1 = _net_credit(b1, lab.acct(s1))
        assert f(lab.acct(s1).cash_reserved) == pytest.approx(width * 100, abs=0.01)
        settle = expiry_chain(PLTR, EXP_21, spot=safe_spot, ladder_spot=42.0)
        lab.expire(s1, settle, now=settle.as_of)
        a1 = lab.acct(s1)
        assert _is_terminal(_status(lab, id1))
        assert f(a1.realized_pnl) == pytest.approx(credit1 - f(a1.fees_paid), abs=0.2)
        assert f(a1.cash_reserved) == pytest.approx(0, abs=0.01)

        s2 = lab.new_account(10_000)
        b2 = lab.acct(s2)
        id2 = lab.open(s2, cand)
        credit2 = _net_credit(b2, lab.acct(s2))
        settle2 = expiry_chain(PLTR, EXP_21, spot=breach_spot, ladder_spot=42.0)
        lab.expire(s2, settle2, now=settle2.as_of)
        lab.mtm(s2, settle2, now=settle2.as_of)
        a2 = lab.acct(s2)
        assert _is_terminal(_status(lab, id2))
        assert f(a2.cash_reserved) == pytest.approx(0, abs=0.01)
        assert f(a2.equity) == pytest.approx(10_000 - (width * 100 - credit2) - f(a2.fees_paid), abs=max(8.0, 0.05 * width * 100))
        assert f(a2.equity) >= 10_000 - float(cand.max_loss_usd) - f(a2.fees_paid) - 60

    @pytest.mark.parametrize(
        "stype", [StrategyType.BULL_PUT_CREDIT_SPREAD, StrategyType.BEAR_CALL_CREDIT_SPREAD]
    )
    def test_roll_out_closes_old_and_opens_new_with_conserved_accounting(self, options_lab, stype):
        lab = options_lab
        chain = build_chain(PLTR)
        slug = lab.new_account(10_000)
        old_id = lab.open(slug, candidate_for(stype, chain, dte=21))

        t_roll = AS_OF.replace(day=24, hour=10)  # ~7 days before the 21-DTE expiry
        roll_chain = moved_chain(PLTR, spot=42.3, as_of=t_roll, expirations=[EXP_21, EXP_35])
        new_cand = candidate_for(stype, roll_chain, dte=(EXP_35 - t_roll.date()).days)
        assert new_cand.expiration_date == EXP_35

        lab.mtm(slug, roll_chain, now=t_roll)
        eq_before = f(lab.acct(slug).equity)
        lab.roll(old_id, new_cand, roll_chain, reason="roll_out", now=t_roll)
        a = lab.acct(slug)

        assert _status(lab, old_id) in {"rolled", "closed"}
        opens = lab.groups(slug, "open")
        assert len(opens) == 1
        new = opens[0]
        assert str(g(new, "id")) != old_id
        assert g(new, "expiration_date") == EXP_35
        width = abs(float(new_cand.legs[0].strike - new_cand.legs[1].strike))
        assert f(a.cash_reserved) == pytest.approx(width * 100, abs=0.01), "only the NEW spread holds collateral"
        # rolling pays spread + fees; it never manufactures equity
        assert f(a.equity) <= eq_before + 0.5
        assert f(a.equity) >= eq_before - 60
        assert f(g(new, "realized_pnl")) <= 0.01, "new credit is not realized profit"
        assert_equity_identity(a)


# ======================================================================================
# 9) Iron Condor (+ pin-risk pre-expiry auto-close)
# ======================================================================================


class TestIronCondor:
    def test_four_legs_single_side_margin_and_full_credit_in_range(self, options_lab):
        lab = options_lab
        chain = build_chain(PLTR)
        slug = lab.new_account(10_000)
        b = lab.acct(slug)
        cand = candidate_for(StrategyType.IRON_CONDOR, chain)
        assert len(cand.legs) == 4
        sid_ = lab.open(slug, cand)
        a = lab.acct(slug)
        grp = lab.group(sid_)
        assert len(g(grp, "legs")) == 4
        put_w = float(cand.legs[0].strike - cand.legs[1].strike)
        call_w = float(cand.legs[3].strike - cand.legs[2].strike)
        assert f(a.cash_reserved) == pytest.approx(max(put_w, call_w) * 100, abs=0.01)
        credit = _net_credit(b, a)

        settle = expiry_chain(PLTR, EXP_21, spot=42.0, ladder_spot=42.0)
        lab.expire(slug, settle, now=settle.as_of)
        end = lab.acct(slug)
        assert _is_terminal(_status(lab, sid_))
        assert f(end.realized_pnl) == pytest.approx(credit - f(end.fees_paid), abs=0.2)
        assert f(end.cash_reserved) == pytest.approx(0, abs=0.01)
        assert f(end.equity) == pytest.approx(10_000 + f(end.realized_pnl), abs=0.2)

    def test_breached_wing_loses_at_most_one_side_of_width(self, options_lab):
        lab = options_lab
        chain = build_chain(PLTR)
        slug = lab.new_account(10_000)
        b = lab.acct(slug)
        cand = candidate_for(StrategyType.IRON_CONDOR, chain)
        sid_ = lab.open(slug, cand)
        credit = _net_credit(b, lab.acct(slug))
        width = float(cand.legs[0].strike - cand.legs[1].strike)

        settle = expiry_chain(PLTR, EXP_21, spot=33.0, ladder_spot=42.0)  # blows through the put wing
        lab.expire(slug, settle, now=settle.as_of)
        lab.mtm(slug, settle, now=settle.as_of)
        a = lab.acct(slug)
        assert _is_terminal(_status(lab, sid_))
        assert f(a.equity) >= 10_000 - (width * 100 - credit) - f(a.fees_paid) - 15
        assert f(a.equity) <= 10_000 - (width * 100 - credit) - f(a.fees_paid) + 15, "only ONE wing pays out"

    def test_pin_risk_warning_then_pre_expiry_auto_close_on_expiration_day(self, options_lab):
        lab = options_lab
        pin = build_pin_risk_scenario()
        assert pin.as_of == PIN_AS_OF and float(pin.distance) == pytest.approx(0.20, abs=1e-9)
        normal = build_chain("SPY")
        cand = condor_for_pin_scenario(normal)
        assert cand.expiration_date == EXP_5

        # A) warning is raised by MTM on the pin chain
        sa = lab.new_account(10_000)
        ida = lab.open(sa, cand)
        lab.mtm(sa, pin.chain, now=PIN_AS_OF)
        grp = lab.group(ida)
        if _status(lab, ida) == "open":
            assert g(grp, "pin_risk_warning") is True

        # B) expiration sweep at 15:40 ET closes (does NOT hold into the pin / assignment)
        sb = lab.new_account(10_000)
        b = lab.acct(sb)
        idb = lab.open(sb, cand)
        opened = lab.acct(sb)
        credit = _net_credit(b, opened)
        lab.expire(sb, pin.chain, now=PIN_AS_OF)
        a = lab.acct(sb)
        grp_b = lab.group(idb)
        assert _is_terminal(_status(lab, idb)), "must auto-close ahead of the closing bell"
        reason = str(g(grp_b, "close_reason") or "").lower()
        assert any(k in reason for k in ("pin", "auto", "expiry", "expir")), reason
        assert lab.lot(sb, "SPY") is None, "no stock delivery / assignment from a pin-risk close"
        assert f(a.cash_reserved) == pytest.approx(0, abs=0.01)
        assert f(a.realized_pnl) <= credit + 0.01
        assert f(a.equity) == pytest.approx(f(a.cash_balance), abs=0.05)
        assert PIN_SHORT_CALL_STRIKE == Decimal("700")


# ======================================================================================
# Multi-day holding – NO 15:55 forced flatten
# ======================================================================================

_HOLD_CASES = [
    (StrategyType.LONG_CALL, "SPY", 10_000, {}),
    (StrategyType.LONG_PUT, "SPY", 10_000, {}),
    (StrategyType.CASH_SECURED_PUT, PLTR, 10_000, {}),
    (StrategyType.WHEEL, PLTR, 10_000, {}),
    (StrategyType.BULL_CALL_SPREAD, "SPY", 10_000, {}),
    (StrategyType.BEAR_PUT_SPREAD, "SPY", 10_000, {}),
    (StrategyType.BULL_PUT_CREDIT_SPREAD, "SPY", 10_000, {}),
    (StrategyType.BEAR_CALL_CREDIT_SPREAD, "SPY", 10_000, {}),
    (StrategyType.IRON_CONDOR, "SPY", 10_000, {}),
    (StrategyType.COVERED_CALL, PLTR, 10_000, {"buy_write": True}),
]


class TestMultiDayHolding:
    @pytest.mark.parametrize("stype,symbol,capital,kw", _HOLD_CASES, ids=[c[0].value for c in _HOLD_CASES])
    def test_position_survives_1555_session_boundary_and_following_days(self, options_lab, stype, symbol, capital, kw):
        lab = options_lab
        chain = build_chain(symbol)
        slug = lab.new_account(capital)
        sid_ = lab.open(slug, candidate_for(stype, chain, dte=35, **kw))
        legs_before = len(g(lab.group(sid_), "legs"))
        reserved_before = f(lab.acct(slug).cash_reserved)

        spot = float(chain.underlying_price)
        for day, hh, mm in [(10, 15, 55), (10, 15, 58), (11, 9, 35), (12, 15, 56), (15, 10, 0)]:
            t = AS_OF.replace(day=day, hour=hh, minute=mm)
            c = moved_chain(symbol, spot=spot, as_of=t, expirations=[expiration_for_dte(35)])
            lab.mtm(slug, c, now=t)
            lab.expire(slug, c, now=t)  # expiration sweep must not flatten a 35-DTE position
            assert _status(lab, sid_) == "open", f"forced flatten at {t:%a %H:%M} for {stype.value}"
        assert len(g(lab.group(sid_), "legs")) == legs_before
        assert f(lab.acct(slug).cash_reserved) == pytest.approx(reserved_before, abs=0.01)
        assert f(lab.acct(slug).open_strategies_count) == 1


# ======================================================================================
# Wheel covered-call roll up-and-out
# ======================================================================================


class TestWheelCoveredCallRoll:
    def test_roll_up_and_out_moves_strike_up_expiry_out_and_keeps_shares_encumbered(self, options_lab):
        lab = options_lab
        chain = build_chain(PLTR)
        slug = lab.new_account(10_000)
        lab.seed_shares(slug, PLTR, 100, 40.0)
        lab.set_wheel_state(slug, PLTR, "holding_shares")
        lab.commit()

        k_old = nearest_strike(chain, EXP_21, C, 44.0)
        old_cand = make_candidate(
            StrategyType.WHEEL, chain, [opt_leg(chain, EXP_21, C, S, k_old)], expiration=EXP_21
        )
        old_id = lab.open(slug, old_cand)
        assert lab.wheel_state(slug, PLTR) == "short_call"

        t_roll = AS_OF.replace(day=24, hour=10)
        roll_chain = moved_chain(PLTR, spot=44.6, as_of=t_roll, expirations=[EXP_21, EXP_35])  # call is tested
        k_new = strike_at_or_above(roll_chain, EXP_35, C, 47.0)
        new_cand = make_candidate(
            StrategyType.WHEEL, roll_chain, [opt_leg(roll_chain, EXP_35, C, S, k_new)], expiration=EXP_35
        )
        lab.mtm(slug, roll_chain, now=t_roll)
        lab.roll(old_id, new_cand, roll_chain, reason="roll_up_and_out", now=t_roll)

        assert _status(lab, old_id) in {"rolled", "closed"}
        opens = lab.groups(slug, "open")
        assert len(opens) == 1
        assert g(opens[0], "expiration_date") == EXP_35
        assert float(g(opens[0], "legs")[0].strike) > float(k_old)
        lot = lab.lot(slug, PLTR)
        assert int(lot["shares"]) == 100 and int(lot["encumbered_shares"]) == 100
        assert lab.wheel_state(slug, PLTR) == "short_call"
        assert_equity_identity(lab.acct(slug))


# ======================================================================================
# Early assignment (ex-dividend)
# ======================================================================================


class TestEarlyAssignment:
    def _setup(self, lab, *, strike: float):
        ex = build_ex_dividend_scenario()
        slug = lab.new_account(25_000)
        lab.seed_shares(slug, "AAPL", 100, 190.0)
        leg = opt_leg(ex.chain, EXP_5, C, S, Decimal(str(strike)))
        cand = make_candidate(StrategyType.COVERED_CALL, ex.chain, [leg], expiration=EXP_5)
        sid_ = lab.open(slug, cand)
        return ex, slug, sid_

    def test_fixture_scenario_has_dividend_above_extrinsic(self):
        ex = build_ex_dividend_scenario()
        assert ex.dividend_per_share > ex.extrinsic_per_share >= 0
        assert ex.intrinsic_per_share > Decimal("20")

    def test_deep_itm_short_call_assigned_when_dividend_exceeds_extrinsic(self, options_lab):
        lab = options_lab
        ex, slug, sid_ = self._setup(lab, strike=195.0)
        before = lab.acct(slug)
        lab.expire(slug, ex.chain, now=ex.chain.as_of, ex_div_symbols={"AAPL": ex.dividend_per_share})
        a = lab.acct(slug)
        lot = lab.lot(slug, "AAPL")
        assert _status(lab, sid_) == "assigned"
        assert lot is None or int(lot["shares"]) == 0, "shares called away early"
        assert f(a.cash_balance) == pytest.approx(f(before.cash_balance) + 195 * 100, abs=5.0)
        assert f(a.short_options_liability) == pytest.approx(0, abs=0.05)
        assert f(a.equity) == pytest.approx(f(a.cash_balance), abs=0.05)

    def test_no_assignment_without_ex_dividend_flag(self, options_lab):
        lab = options_lab
        ex, slug, sid_ = self._setup(lab, strike=195.0)
        lab.expire(slug, ex.chain, now=ex.chain.as_of, ex_div_symbols=[])
        assert _status(lab, sid_) == "open"

    def test_atm_call_not_assigned_even_on_ex_dividend(self, options_lab):
        lab = options_lab
        ex = build_ex_dividend_scenario()
        slug = lab.new_account(25_000)
        lab.seed_shares(slug, "AAPL", 100, 190.0)
        atm = nearest_strike(ex.chain, EXP_5, C, 225.0)
        # extrinsic of an ATM call dwarfs the dividend -> early assignment is irrational
        q = find_quote(ex.chain, EXP_5, C, atm)
        assert q.mid - max(Decimal("0"), ex.chain.underlying_price - atm) > ex.dividend_per_share
        cand = make_candidate(
            StrategyType.COVERED_CALL, ex.chain, [opt_leg(ex.chain, EXP_5, C, S, atm)], expiration=EXP_5
        )
        sid_ = lab.open(slug, cand)
        lab.expire(slug, ex.chain, now=ex.chain.as_of, ex_div_symbols={"AAPL": ex.dividend_per_share})
        assert _status(lab, sid_) == "open"


# ======================================================================================
# Gap-down / IV-spike stress + degraded (closed-market) data
# ======================================================================================


class TestGapAndVolatilityStress:
    def test_scenario_shape(self):
        crash = build_crash_scenario()
        assert crash.spot_after == pytest.approx(crash.spot_before * 0.88, abs=0.01)
        q_before = find_quote(crash.before, EXP_21, P, 695)
        q_after = find_quote(crash.after, EXP_21, P, 695)
        assert q_after.implied_volatility - q_before.implied_volatility == pytest.approx(0.20, abs=0.02)
        assert q_after.mid > q_before.mid * 3

    def test_credit_spread_loss_capped_at_defined_risk_through_a_gap(self, options_lab):
        lab = options_lab
        crash = build_crash_scenario()
        slug = lab.new_account(10_000)
        cand = candidate_for(StrategyType.BULL_PUT_CREDIT_SPREAD, crash.before)
        lab.open(slug, cand)
        opened = lab.acct(slug)
        lab.mtm(slug, crash.after, now=crash.as_of_after)
        a = lab.acct(slug)
        fees = f(a.fees_paid)
        # The engine's stop-loss guard fires on a gap straight through the stop: the spread is
        # closed at the (wide, post-gap) bid/ask, the loss is realized and collateral is released.
        assert lab.groups(slug, "open") == []
        closed = lab.groups(slug, "closed")
        assert len(closed) == 1 and "stop_loss" in str(g(closed[0], "close_reason"))
        assert f(opened.cash_reserved) > 0 and f(a.cash_reserved) == pytest.approx(0, abs=0.01)
        assert f(a.realized_pnl) < -500, "the gap produced a deep realized loss"
        assert f(a.realized_pnl) >= -(float(cand.max_loss_usd) + fees + 0.02), "loss is capped by the wing"
        assert f(a.equity) >= f(opened.equity) - float(cand.max_loss_usd) - 80
        assert f(a.free_cash) >= -0.01 and f(a.free_cash) == pytest.approx(f(a.cash_balance), abs=0.01)
        assert f(a.max_drawdown_usd) > 400 and f(a.max_drawdown_pct) > 4
        assert_equity_identity(a)

    def test_long_put_profits_and_long_call_loss_is_bounded_by_premium(self, options_lab):
        lab = options_lab
        crash = build_crash_scenario()
        s_put = lab.new_account(10_000)
        s_call = lab.new_account(10_000)
        put_c = candidate_for(StrategyType.LONG_PUT, crash.before)
        call_c = candidate_for(StrategyType.LONG_CALL, crash.before)
        lab.open(s_put, put_c)
        lab.open(s_call, call_c)
        put_open, call_open = lab.acct(s_put), lab.acct(s_call)
        lab.mtm(s_put, crash.after, now=crash.as_of_after)
        lab.mtm(s_call, crash.after, now=crash.as_of_after)
        put_after, call_after = lab.acct(s_put), lab.acct(s_call)
        assert f(put_after.equity) > f(put_open.equity) + 500
        assert f(call_after.equity) < f(call_open.equity)
        assert f(call_after.equity) >= 10_000 - float(call_c.max_loss_usd) - f(call_after.fees_paid) - 20

    def test_missing_or_stale_quotes_never_crash_mtm_or_create_phantom_equity(self, options_lab):
        lab = options_lab
        chain = build_chain("SPY")
        slug = lab.new_account(10_000)
        lab.open(slug, candidate_for(StrategyType.IRON_CONDOR, chain))
        before = lab.acct(slug)
        closed = build_closed_market_chain("SPY")
        lab.mtm(slug, closed, now=closed.as_of)
        after = lab.acct(slug)
        assert f(after.equity) == f(after.equity)  # not NaN
        assert f(after.equity) <= f(before.equity) + 150, "stale/missing data must not mint profit"
        assert f(after.equity) >= f(before.equity) - 1_000
        assert f(after.cash_reserved) == pytest.approx(f(before.cash_reserved), abs=0.01)
        assert _open_ids(lab, slug), "market closure alone never closes positions"
