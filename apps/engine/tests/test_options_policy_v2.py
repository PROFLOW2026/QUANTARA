"""Regression tests for Options Lab policy v2 (post 2026-11-23 loss forensics)."""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from quantara_options.contracts import (
    AIAction,
    AccountRuleProfile,
    DecisionMode,
    OptionAccountSummary,
    StrategyType,
)
from quantara_options.policy import (
    POLICY_VERSION,
    credit_after_fees_per_share,
    min_natural_credit_to_width,
    tier_profile_for_capital,
)
from quantara_options.sim.lifecycle import ExitPolicy, evaluate_exit_breach
from quantara_options.ai.groq_advisor import GroqOptionsAdvisor
from quantara_options.strategies.catalog import StrategyActionRecommendation


def _credit_strategy(*, entry_credit: float, max_loss: float, unrealized: float, estimated: bool = False):
    st = SimpleNamespace(
        strategy_type=StrategyType.BULL_PUT_CREDIT_SPREAD,
        net_entry_cash_flow=Decimal(str(entry_credit)),
        max_loss_usd=Decimal(str(max_loss)),
        metadata={},
        option_unrealized=lambda: Decimal(str(unrealized)),
        mark_is_estimated=lambda: estimated,
    )
    return st


class TestNaturalCreditGate:
    def test_tiny_fill_credit_fails_width_floor(self):
        # Day-1 AAPL pattern: mid ~0.85, fill ~0.16 on $5 width.
        width = 5.0
        fill_credit = 0.16
        after_fees = credit_after_fees_per_share(fill_credit, n_option_legs=2, qty=1)
        floor = min_natural_credit_to_width(StrategyType.BULL_PUT_CREDIT_SPREAD)
        assert after_fees < width * floor

    def test_healthy_natural_credit_passes(self):
        width = 5.0
        natural = 1.00  # $1/share = 20% of width
        after_fees = credit_after_fees_per_share(natural, n_option_legs=2, qty=1)
        floor = min_natural_credit_to_width(StrategyType.IRON_CONDOR)
        assert after_fees >= width * floor


class TestExitPolicyV2:
    def test_tiny_credit_does_not_trip_on_first_small_mark_move(self):
        # $16 credit, $484 max loss — old 2x credit stop = $32; v2 soft = 45% of max_loss ≈ $218
        st = _credit_strategy(entry_credit=16, max_loss=484, unrealized=-80)
        reason, _info = evaluate_exit_breach(st, ExitPolicy())
        assert reason is None  # -$80 would have tripped legacy 2×$16=$32 stop
        # Cross soft threshold → pending, not immediate close on first print
        st2 = _credit_strategy(entry_credit=16, max_loss=484, unrealized=-220)
        reason2, info2 = evaluate_exit_breach(st2, ExitPolicy())
        assert reason2 == "stop_loss_pending"
        assert float(info2["stop_loss_usd"]) >= 200  # ~45% of 484, not 2×16

    def test_soft_stop_requires_confirmation(self):
        st = _credit_strategy(entry_credit=50, max_loss=450, unrealized=-220)  # ~49% of max loss
        st.metadata = {}
        reason1, info1 = evaluate_exit_breach(st, ExitPolicy(soft_confirm_cycles=2))
        assert reason1 == "stop_loss_pending"
        st.metadata["soft_stop_confirm_count"] = info1["soft_stop_confirm_count"]
        reason2, _ = evaluate_exit_breach(st, ExitPolicy(soft_confirm_cycles=2))
        assert reason2 == "stop_loss_breach"

    def test_emergency_closes_immediately(self):
        st = _credit_strategy(entry_credit=50, max_loss=450, unrealized=-350)  # >75%
        reason, info = evaluate_exit_breach(st, ExitPolicy())
        assert reason == "stop_loss_emergency"
        assert info.get("urgency") == "immediate"

    def test_estimated_marks_block_soft_not_emergency(self):
        st = _credit_strategy(entry_credit=50, max_loss=450, unrealized=-220, estimated=True)
        reason, info = evaluate_exit_breach(st, ExitPolicy())
        assert reason is None
        assert info.get("soft_blocked_estimated_marks") is True
        st2 = _credit_strategy(entry_credit=50, max_loss=450, unrealized=-350, estimated=True)
        reason2, _ = evaluate_exit_breach(st2, ExitPolicy())
        assert reason2 == "stop_loss_emergency"


class TestGroqFreeSlotBlock:
    def test_discard_close_when_catalog_hold(self):
        adv = GroqOptionsAdvisor.__new__(GroqOptionsAdvisor)
        adv.config = SimpleNamespace(
            max_new_per_cycle=2, min_confidence_open=0.0, emit_hold_records=False
        )
        account = SimpleNamespace(
            id="1",
            slug="options-10k",
            mode=DecisionMode.AI,
            equity=10000.0,
            free_cash=10000.0,
            rule_profile=AccountRuleProfile(max_open_strategies=6),
        )
        open_list = [SimpleNamespace(id="gid-1", underlying_symbol="TSLA")]
        recs = [
            StrategyActionRecommendation(
                position_group_id="gid-1",
                strategy_type=StrategyType.BEAR_CALL_CREDIT_SPREAD,
                underlying_symbol="TSLA",
                action=AIAction.HOLD,
                reason_code="within_plan",
                rationale_he="בתוך התוכנית",
                rationale_en="Within plan",
                mandatory=False,
            )
        ]
        parsed = {
            "decisions": [
                {
                    "action": "CLOSE",
                    "gid": "gid-1",
                    "confidence": 0.9,
                    "rationale_en": "Close to free a slot for TSLA credit spread",
                    "rationale_he": "סגירה לפינוי מקום",
                }
            ]
        }
        res = GroqOptionsAdvisor._ground(
            adv, account, parsed, [], open_list, recs, {"cycle": 1}
        )
        assert not any(d.action == AIAction.CLOSE for d in res.decisions)
        assert any("discretionary_close_blocked_catalog_hold" in x for x in res.discarded)


class TestRiskTiers:
    def test_tier_profiles_differ_by_capital(self):
        p5 = tier_profile_for_capital(5000)
        p10 = tier_profile_for_capital(10000)
        p25 = tier_profile_for_capital(25000)
        assert float(p5["max_risk_per_trade_pct"]) < float(p10["max_risk_per_trade_pct"])
        assert int(p5["max_open_strategies"]) < int(p25["max_open_strategies"])
        assert p5["profile_name"].startswith("sim_5k")

    def test_policy_version_constant(self):
        assert POLICY_VERSION.startswith("options_lab_v2_")
