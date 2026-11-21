"""QUANTARA Options - AI decision layer, deterministic Risk Guard, persistence and cohort isolation.

HONESTY NOTE: every market input here is a SYNTHETIC fixture (``DataFeedTier.FIXTURE``).
Passing tests prove advisor / guard / accounting / persistence logic only; they say nothing
about live Alpaca / OPRA availability.  The Groq HTTP layer is replaced by an in-process fake
transport (no network, no real key, no cost).
"""

from __future__ import annotations

import json
import urllib.error
import uuid
from decimal import Decimal
from pathlib import Path

import pytest

from quantara_options.ai import (
    DECIDED_BY_AI,
    DECIDED_BY_FALLBACK,
    DECIDED_BY_RULES,
    AdvisorConfig,
    GroqOptionsAdvisor,
    OptionsRiskGuard,
    RiskGuardConfig,
)
from quantara_options.contracts import (
    AIAction,
    DataFeedTier,
    DecisionMode,
    OptionRight,
    StrategyType,
)
from quantara_options.db.seed_lab import (
    LAB_ACCOUNT_SEEDS,
    LAB_ACCOUNT_SLUGS,
    STRATEGY_SCOPE_TYPES,
    strategy_types_for_scope,
)
from quantara_options.strategies.catalog import StrategyActionRecommendation
from tests.fixtures.options_fixtures import (
    AS_OF,
    EXP_21,
    OPTQA_PREFIX,
    build_chain,
    candidate_for,
    f,
    g,
    options_lab,  # noqa: F401  (pytest fixture)
    pg_insert_qa_account,
    pg_purge_qa_rows,
    pg_table_exists,
    rule_profile,
    sid,
)

P = OptionRight.PUT
C = OptionRight.CALL
SPY, AAPL, PLTR = "SPY", "AAPL", "PLTR"
FAKE_KEY = "gsk_UNITTESTKEY000000000000"
MODEL = "openai/gpt-oss-120b"
MISSING_SECRETS = Path("__optqa_no_such_secrets_file__.json")


# ======================================================================================
# helpers
# ======================================================================================


def _reply(content, *, tin: int = 700, tout: int = 90):
    text = content if isinstance(content, str) else json.dumps(content)
    return 200, {"choices": [{"message": {"content": text}}],
                 "usage": {"prompt_tokens": tin, "completion_tokens": tout}}, {}


class FakeGroq:
    """In-process transport for ``GroqOptionsAdvisor`` (records every request)."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls: list[dict] = []

    def __call__(self, url, headers, body, timeout):
        self.calls.append({"url": url, "headers": headers, "body": json.loads(body), "timeout": timeout})
        r = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        if isinstance(r, BaseException):
            raise r
        return r


def make_advisor(transport, *, env=None, **config):
    cfg = dict(retries=0)
    cfg.update(config)
    return GroqOptionsAdvisor(
        AdvisorConfig(**cfg),
        env={"GROQ_API_KEY": FAKE_KEY} if env is None else env,
        secrets_path=MISSING_SECRETS,
        transport=transport,
        sleep=lambda _s: None,
    )


def candidate_pool():
    """Three grounded fixture candidates on three different underlyings."""
    return [
        candidate_for(StrategyType.BULL_PUT_CREDIT_SPREAD, build_chain(SPY)),
        candidate_for(StrategyType.LONG_CALL, build_chain(AAPL)),
        candidate_for(StrategyType.IRON_CONDOR, build_chain(PLTR)),
    ]


def _ai_account(lab, capital=25_000, mode="ai", **profile):
    slug = lab.new_account(capital, mode=mode, profile=rule_profile(**profile) if profile else None)
    return slug, lab.acct(slug)


def _open_decisions(res):
    return [d for d in res.decisions if d.action == AIAction.OPEN]


def _ai_pick(pool_item, *, conf=0.8, qty=1):
    return {"action": "OPEN", "id": pool_item.candidate_id, "qty": qty, "confidence": conf,
            "rationale_he": "בדיקה", "rationale_en": "unit-test pick"}


# ======================================================================================
# Rules Mode
# ======================================================================================


class TestRulesMode:
    def test_rules_mode_never_calls_groq_and_is_deterministic(self, options_lab):
        lab = options_lab
        _, acct = _ai_account(lab, mode="rules")
        pool = candidate_pool()
        fake = FakeGroq(_reply({"decisions": []}))
        adv = make_advisor(fake)
        r1 = adv.decide(account=acct, candidates=pool)
        r2 = adv.decide(account=acct, candidates=list(reversed(pool)))
        assert fake.calls == [], "rules mode must not touch the AI transport"
        assert r1.decided_by == DECIDED_BY_RULES == "rules_engine"
        assert r1.mode == DecisionMode.RULES and r1.ai_called is False
        ids1 = [d.candidate_id for d in _open_decisions(r1)]
        ids2 = [d.candidate_id for d in _open_decisions(r2)]
        assert ids1 == ids2 and 1 <= len(ids1) <= 2, "same input => same decisions (max_new_per_cycle=2)"
        pool_ids = {c.candidate_id for c in pool}
        assert set(ids1) <= pool_ids, "rules mode may only pick grounded candidates"
        unders = [d.underlying_symbol for d in _open_decisions(r1)]
        assert len(unders) == len(set(unders)), "one new position per underlying per cycle"
        assert all(d.decided_by == DECIDED_BY_RULES and d.model_name is None for d in r1.decisions)

    def test_rules_mode_respects_cash_budget_on_a_tiny_account(self, options_lab):
        lab = options_lab
        _, acct = _ai_account(lab, capital=1_000, mode="rules")
        res = make_advisor(FakeGroq(_reply({}))).decide(account=acct, candidates=candidate_pool())
        for d in _open_decisions(res):
            cand = next(c for c in candidate_pool() if c.candidate_id == d.candidate_id)
            need = max(0.0, -float(cand.estimated_net_premium_total)) + float(cand.collateral_required_usd)
            assert need <= acct.free_cash * 0.95 + 1e-6

    def test_low_score_and_unapproved_candidates_are_filtered_out(self, options_lab):
        lab = options_lab
        _, acct = _ai_account(lab, mode="rules", options_approval_level=1)
        pool = [c.model_copy(update={"rules_score": 30.0}) for c in candidate_pool()]
        res = make_advisor(FakeGroq(_reply({}))).decide(account=acct, candidates=pool)
        assert _open_decisions(res) == []
        _, acct2 = _ai_account(lab, mode="rules", options_approval_level=1)
        # approval level 1 allows only CSP / covered call / wheel -> spreads and long options drop out
        res2 = make_advisor(FakeGroq(_reply({}))).decide(account=acct2, candidates=candidate_pool())
        assert _open_decisions(res2) == []

    def test_rules_decisions_flow_through_guard_into_the_simulator(self, options_lab):
        lab = options_lab
        slug, acct = _ai_account(lab, mode="rules")
        pool = candidate_pool()
        guard = OptionsRiskGuard()
        res = make_advisor(FakeGroq(_reply({}))).decide(account=acct, candidates=pool)
        decisions = _open_decisions(res)
        assert decisions
        for d in decisions:
            cand = next(c for c in pool if c.candidate_id == d.candidate_id)
            chain = build_chain(cand.underlying_symbol)
            check = guard.validate_open(
                cand, account=lab.acct(slug), chain=chain, known_candidate_ids=[c.candidate_id for c in pool],
                open_strategies=lab.groups(slug, "open"),
            )
            assert check.passed, check.reasons
            lab.open(slug, cand, chain=chain)
        a = lab.acct(slug)
        assert a.open_strategies_count == len(decisions)
        assert f(a.free_cash) >= -0.01


# ======================================================================================
# AI Mode (mocked Groq openai/gpt-oss-120b)
# ======================================================================================


class TestAIModeWithMockedGroq:
    def test_ai_open_decision_is_grounded_tagged_and_request_is_correct(self, options_lab):
        lab = options_lab
        _, acct = _ai_account(lab)
        pool = candidate_pool()
        pick = pool[1]  # AAPL long call
        fake = FakeGroq(_reply({"decisions": [_ai_pick(pick, conf=0.82)], "view_he": "שורי", "view_en": "bullish"}))
        res = make_advisor(fake).decide(account=acct, candidates=pool, market_context={"feed_tier": "fixture"})
        assert len(fake.calls) == 1
        call = fake.calls[0]
        assert call["body"]["model"] == MODEL
        assert call["url"].startswith("https://api.groq.com/") and call["url"].endswith("/chat/completions")
        assert call["headers"]["Authorization"] == f"Bearer {FAKE_KEY}"
        assert call["body"]["response_format"] == {"type": "json_object"}
        assert call["body"]["messages"][1]["role"] == "user"
        assert res.ai_called is True and res.decided_by == DECIDED_BY_AI == "groq_openai_gpt_oss_120b"
        assert res.mode == DecisionMode.AI and res.fallback_reason is None
        assert res.tokens_in == 700 and res.tokens_out == 90
        opens = _open_decisions(res)
        assert [d.candidate_id for d in opens] == [pick.candidate_id]
        d = opens[0]
        assert d.decided_by == DECIDED_BY_AI and d.model_name == MODEL
        assert d.confidence == pytest.approx(0.82) and d.rationale_en == "unit-test pick"
        assert res.market_view_en == "bullish"
        assert set(res.candidate_ids_sent) <= {c.candidate_id for c in pool}
        # never leak the secret into anything that gets persisted / returned to the UI
        assert FAKE_KEY not in json.dumps(res.as_dict(), default=str)

    def test_prompt_carries_only_grounded_precomputed_candidates(self, options_lab):
        lab = options_lab
        _, acct = _ai_account(lab)
        pool = candidate_pool()
        fake = FakeGroq(_reply({"decisions": []}))
        make_advisor(fake).decide(account=acct, candidates=pool)
        user = json.loads(fake.calls[0]["body"]["messages"][1]["content"])
        assert {c["id"] for c in user["candidates"]} <= {c.candidate_id for c in pool}
        for c in user["candidates"]:
            assert c["maxL"] >= 0 and "qty" in c

    def test_invented_candidate_id_is_discarded_never_opened(self, options_lab):
        lab = options_lab
        _, acct = _ai_account(lab)
        fake = FakeGroq(_reply({"decisions": [
            {"action": "OPEN", "id": "SPY260701C00999000-INVENTED", "qty": 1, "confidence": 0.99},
        ]}))
        res = make_advisor(fake).decide(account=acct, candidates=candidate_pool())
        assert res.ai_called and _open_decisions(res) == []
        assert any(x.startswith("ungrounded_candidate") for x in res.discarded)

    def test_low_confidence_open_is_discarded(self, options_lab):
        lab = options_lab
        _, acct = _ai_account(lab)
        pool = candidate_pool()
        res = make_advisor(FakeGroq(_reply({"decisions": [_ai_pick(pool[0], conf=0.30)]}))).decide(
            account=acct, candidates=pool)
        assert _open_decisions(res) == [] and any(x.startswith("low_confidence") for x in res.discarded)

    def test_ai_cannot_size_up_beyond_the_scanned_quantity(self, options_lab):
        lab = options_lab
        _, acct = _ai_account(lab)
        pool = candidate_pool()
        res = make_advisor(FakeGroq(_reply({"decisions": [_ai_pick(pool[0], qty=9)]}))).decide(
            account=acct, candidates=pool)
        (d,) = _open_decisions(res)
        assert d.contracts_qty == pool[0].contracts_qty == 1

    def test_ai_is_capped_at_max_new_positions_per_cycle(self, options_lab):
        lab = options_lab
        _, acct = _ai_account(lab)
        pool = candidate_pool()
        reply = _reply({"decisions": [_ai_pick(c) for c in pool]})
        res = make_advisor(FakeGroq(reply), max_new_per_cycle=2).decide(account=acct, candidates=pool)
        assert len(_open_decisions(res)) == 2
        assert any(x.startswith("max_new_exceeded") for x in res.discarded)

    def test_ai_cannot_open_two_positions_on_one_underlying_in_a_cycle(self, options_lab):
        lab = options_lab
        _, acct = _ai_account(lab)
        a = candidate_for(StrategyType.BULL_PUT_CREDIT_SPREAD, build_chain(SPY))
        b = candidate_for(StrategyType.LONG_CALL, build_chain(SPY))
        res = make_advisor(FakeGroq(_reply({"decisions": [_ai_pick(a), _ai_pick(b)]}))).decide(
            account=acct, candidates=[a, b])
        assert len(_open_decisions(res)) == 1
        assert any(x.startswith("same_underlying_twice") for x in res.discarded)

    def test_ai_skip_and_unknown_action_are_handled(self, options_lab):
        lab = options_lab
        _, acct = _ai_account(lab)
        res = make_advisor(FakeGroq(_reply({"decisions": [
            {"action": "SKIP", "confidence": 0.7, "rationale_en": "nothing attractive"},
            {"action": "YOLO", "id": "x"},
        ]}))).decide(account=acct, candidates=candidate_pool())
        assert [d.action for d in res.decisions] == [AIAction.SKIP]
        assert any(x.startswith("bad_action") for x in res.discarded)

    def test_json_fenced_reply_is_parsed(self, options_lab):
        lab = options_lab
        _, acct = _ai_account(lab)
        pool = candidate_pool()
        text = "Here you go:\n```json\n" + json.dumps({"decisions": [_ai_pick(pool[2])]}) + "\n```"
        res = make_advisor(FakeGroq(_reply(text))).decide(account=acct, candidates=pool)
        assert [d.candidate_id for d in _open_decisions(res)] == [pool[2].candidate_id]

    def test_http_400_downgrades_json_mode_then_succeeds(self, options_lab):
        lab = options_lab
        _, acct = _ai_account(lab)
        pool = candidate_pool()
        fake = FakeGroq((400, {"error": {"message": "response_format unsupported"}}, {}),
                        _reply({"decisions": [_ai_pick(pool[0])]}))
        res = make_advisor(fake, retries=1).decide(account=acct, candidates=pool)
        assert len(fake.calls) == 2 and "response_format" in fake.calls[0]["body"]
        assert "response_format" not in fake.calls[1]["body"]
        assert res.decided_by == DECIDED_BY_AI and len(_open_decisions(res)) == 1


# ======================================================================================
# Honest fallback on AI failure
# ======================================================================================


FAILURES = [
    pytest.param(lambda: FakeGroq((500, {"error": "boom"}, {})), "http_500", id="http-500"),
    pytest.param(lambda: FakeGroq((503, {}, {})), "http_503", id="http-503"),
    pytest.param(lambda: FakeGroq((401, {"error": "bad key"}, {})), "http_401", id="http-401"),
    pytest.param(lambda: FakeGroq((429, {"error": "rate"}, {"retry-after": "7"})), "http_429", id="http-429"),
    pytest.param(lambda: FakeGroq(urllib.error.URLError("dns down")), "network", id="network-down"),
    pytest.param(lambda: FakeGroq(TimeoutError("timed out")), "network", id="timeout"),
    pytest.param(lambda: FakeGroq(_reply("I cannot comply, no JSON here")), "bad_json", id="not-json"),
    pytest.param(lambda: FakeGroq((200, {"unexpected": True}, {})), "bad_response", id="bad-shape"),
]


class TestHonestFallback:
    @pytest.mark.parametrize("make_fake, reason", FAILURES)
    def test_failure_falls_back_to_rules_and_says_so(self, options_lab, make_fake, reason):
        lab = options_lab
        _, acct = _ai_account(lab)
        pool = candidate_pool()
        fake = make_fake()
        adv = make_advisor(fake)
        res = adv.decide(account=acct, candidates=pool)
        assert res.fallback_reason == reason
        assert res.decided_by == DECIDED_BY_FALLBACK == "skills_rules_fallback"
        assert res.mode == DecisionMode.AI and res.ai_called is False
        opens = _open_decisions(res)
        assert opens, "fallback still produces rules-based OPEN decisions"
        assert {d.candidate_id for d in opens} <= {c.candidate_id for c in pool}
        for d in res.decisions:
            assert d.decided_by == DECIDED_BY_FALLBACK, "never labelled as AI when AI did not decide"
            assert d.model_name is None
            assert "rules fallback" in d.rationale_en
            assert d.market_context.get("fallback_reason") == reason
        assert adv.fallback_count == 1 and adv.last_error

    def test_secrets_are_redacted_from_errors_and_status(self, options_lab):
        lab = options_lab
        _, acct = _ai_account(lab)
        fake = FakeGroq((401, {"error": {"message": f"invalid key {FAKE_KEY}"}}, {}))
        adv = make_advisor(fake)
        adv.decide(account=acct, candidates=candidate_pool())
        status = adv.status()
        assert FAKE_KEY not in json.dumps(status, default=str)
        assert FAKE_KEY not in (adv.last_error or "")
        assert status["model_name"] == MODEL and status["configured"] is True

    def test_429_starts_cooldown_and_next_cycle_does_not_hammer_the_api(self, options_lab):
        lab = options_lab
        _, acct = _ai_account(lab)
        fake = FakeGroq((429, {"error": "rate"}, {"retry-after": "30"}))
        adv = make_advisor(fake)
        first = adv.decide(account=acct, candidates=candidate_pool())
        second = adv.decide(account=acct, candidates=candidate_pool())
        assert first.fallback_reason == "http_429" and second.fallback_reason == "rate_limited_cooldown"
        assert len(fake.calls) == 1
        assert adv.status()["cooldown_remaining_s"] > 0 and adv.groq_available is False

    def test_missing_key_never_calls_transport(self, options_lab):
        lab = options_lab
        _, acct = _ai_account(lab)
        fake = FakeGroq(_reply({"decisions": []}))
        adv = make_advisor(fake, env={})
        res = adv.decide(account=acct, candidates=candidate_pool())
        assert fake.calls == [] and res.fallback_reason == "ai_not_configured"
        assert res.decided_by == DECIDED_BY_FALLBACK and adv.status()["configured"] is False

    def test_kill_switch_env_var_disables_ai(self, options_lab):
        lab = options_lab
        _, acct = _ai_account(lab)
        fake = FakeGroq(_reply({"decisions": []}))
        adv = make_advisor(fake, env={"GROQ_API_KEY": FAKE_KEY, "QUANTARA_OPTIONS_AI_DISABLED": "1"})
        res = adv.decide(account=acct, candidates=candidate_pool())
        assert fake.calls == [] and res.decided_by == DECIDED_BY_FALLBACK

    def test_daily_call_cap_falls_back_instead_of_overspending(self, options_lab):
        lab = options_lab
        _, acct = _ai_account(lab)
        pool = candidate_pool()
        fake = FakeGroq(_reply({"decisions": [_ai_pick(pool[0])]}))
        adv = make_advisor(fake, max_calls_per_day=1)
        first = adv.decide(account=acct, candidates=pool)
        adv.begin_cycle()
        second = adv.decide(account=acct, candidates=pool)
        assert first.decided_by == DECIDED_BY_AI
        assert second.fallback_reason == "daily_call_cap" and len(fake.calls) == 1

    def test_network_error_is_retried_with_backoff_before_falling_back(self, options_lab):
        lab = options_lab
        _, acct = _ai_account(lab)
        slept: list[float] = []
        fake = FakeGroq(urllib.error.URLError("flaky"))
        adv = GroqOptionsAdvisor(
            AdvisorConfig(retries=2, backoff_s=1.0), env={"GROQ_API_KEY": FAKE_KEY},
            secrets_path=MISSING_SECRETS, transport=fake, sleep=slept.append,
        )
        res = adv.decide(account=acct, candidates=candidate_pool())
        assert len(fake.calls) == 3 and slept == [1.0, 2.0]
        assert res.fallback_reason == "network"

    def test_mandatory_risk_authority_exit_survives_an_ai_that_says_hold(self, options_lab):
        lab = options_lab
        slug, acct = _ai_account(lab)
        cand = candidate_for(StrategyType.BULL_PUT_CREDIT_SPREAD, build_chain(SPY))
        gid = lab.open(slug, cand)
        rec = StrategyActionRecommendation(
            position_group_id=gid, strategy_type=cand.strategy_type, underlying_symbol=SPY,
            action=AIAction.CLOSE, reason_code="stop_loss_breach", rationale_he="סטופ", rationale_en="stop",
            urgency="high", mandatory=True,
        )
        fake = FakeGroq(_reply({"decisions": [{"action": "HOLD", "gid": gid, "confidence": 0.9}]}))
        only_mandatory = make_advisor(fake).decide(
            account=lab.acct(slug), candidates=[], open_strategies=lab.groups(slug, "open"),
            open_recommendations=[rec],
        )
        assert fake.calls == [], "nothing for the AI to decide -> no paid call, rules authority acts alone"
        assert [d.action for d in only_mandatory.decisions] == [AIAction.CLOSE]
        res = make_advisor(fake).decide(
            account=lab.acct(slug), candidates=[candidate_for(StrategyType.LONG_CALL, build_chain(AAPL))],
            open_strategies=lab.groups(slug, "open"), open_recommendations=[rec],
        )
        assert len(fake.calls) == 1 and res.ai_called
        closes = [d for d in res.decisions if d.action == AIAction.CLOSE]
        assert len(closes) == 1 and closes[0].position_group_id == gid
        assert closes[0].decided_by == DECIDED_BY_RULES, "risk authority is rules-based, not AI"
        assert closes[0].market_context.get("risk_authority") is True


# ======================================================================================
# Risk Guard
# ======================================================================================


def _as_indicative(chain, cand):
    """Re-tag a fixture chain/candidate as an Alpaca indicative feed so strict (non-fixture) quote
    checks apply.  Prices are unchanged - this only switches which guard thresholds are used."""
    chain2 = chain.model_copy(update={
        "feed_tier": DataFeedTier.ALPACA_INDICATIVE, "is_indicative": True,
        "contracts": [q.model_copy(update={"feed_tier": DataFeedTier.ALPACA_INDICATIVE}) for q in chain.contracts],
    })
    return chain2, cand.model_copy(update={"feed_tier": DataFeedTier.ALPACA_INDICATIVE, "is_indicative": True})


def _patch_quote(chain, occ, **updates):
    contracts = [q.model_copy(update=updates) if q.contract.occ_symbol == occ else q for q in chain.contracts]
    return chain.model_copy(update={"contracts": contracts})


@pytest.fixture
def guard():
    return OptionsRiskGuard()


class TestRiskGuard:
    def _spread(self):
        chain = build_chain(SPY)
        return chain, candidate_for(StrategyType.BULL_PUT_CREDIT_SPREAD, chain)

    def test_grounded_candidate_on_a_funded_account_passes(self, options_lab, guard):
        slug, acct = _ai_account(options_lab, 25_000)
        chain, cand = self._spread()
        res = guard.validate_open(cand, account=acct, chain=chain, known_candidate_ids=[cand.candidate_id])
        assert res.passed and res.reasons == []
        assert res.checks["fixture_spread_override"] is True
        assert 0 < res.effective_risk_usd <= res.risk_budget_usd
        # a credit spread needs at least its max loss in free cash (collateral net of credit, plus fees)
        assert res.cash_required_usd >= float(cand.max_loss_usd) - 1.0
        assert res.cash_required_usd <= float(cand.collateral_required_usd) + res.fees_estimate_usd + 1e-6

    def test_invented_occ_symbol_is_blocked(self, options_lab, guard):
        slug, acct = _ai_account(options_lab, 25_000)
        chain, cand = self._spread()
        legs = list(cand.legs)
        legs[0] = legs[0].model_copy(update={"occ_symbol": "SPY260701P00123000"})
        res = guard.validate_open(cand.model_copy(update={"legs": legs}), account=acct, chain=chain)
        assert not res.passed and any(r.startswith("unknown_occ") for r in res.reasons)

    def test_leg_that_disagrees_with_its_chain_contract_is_blocked(self, options_lab, guard):
        slug, acct = _ai_account(options_lab, 25_000)
        chain, cand = self._spread()
        legs = list(cand.legs)
        legs[0] = legs[0].model_copy(update={"strike": legs[0].strike + 5})
        res = guard.validate_open(cand.model_copy(update={"legs": legs}), account=acct, chain=chain)
        assert not res.passed and any(r.startswith("contract_mismatch") for r in res.reasons)

    def test_multiplier_mismatch_is_blocked(self, options_lab, guard):
        slug, acct = _ai_account(options_lab, 25_000)
        chain, cand = self._spread()
        legs = [l.model_copy(update={"multiplier": 50}) for l in cand.legs]
        res = guard.validate_open(cand.model_copy(update={"legs": legs}), account=acct, chain=chain)
        assert any(r.startswith("multiplier_mismatch") for r in res.reasons)

    def test_candidate_not_produced_by_scanner_is_ungrounded(self, options_lab, guard):
        slug, acct = _ai_account(options_lab, 25_000)
        chain, cand = self._spread()
        res = guard.validate_open(cand, account=acct, chain=chain, known_candidate_ids=["something-else"])
        assert not res.passed and any(r.startswith("ungrounded_candidate") for r in res.reasons)

    def test_wide_spread_blocked_on_real_feed_and_even_on_fixture_extremes(self, options_lab, guard):
        slug, acct = _ai_account(options_lab, 25_000)
        chain, cand = self._spread()
        occ = cand.legs[0].occ_symbol
        bid = next(q.bid for q in chain.contracts if q.contract.occ_symbol == occ)
        wide = _patch_quote(chain, occ, ask=bid * 4)  # ~120 % spread
        res_fixture = guard.validate_open(cand, account=acct, chain=wide)
        assert any(r.startswith("wide_spread") for r in res_fixture.reasons), "60 % fixture cap still bites"
        chain_ind, cand_ind = _as_indicative(chain, cand)
        moderate = _patch_quote(chain_ind, occ, ask=bid * Decimal("1.6"))  # ~46 % spread: ok on fixture, not live
        res_live = guard.validate_open(cand_ind, account=acct, chain=moderate)
        assert res_live.checks["fixture_spread_override"] is False
        assert any(r.startswith("wide_spread") for r in res_live.reasons)
        assert not any(r.startswith("wide_spread") for r in guard.validate_open(
            cand, account=acct, chain=_patch_quote(chain, occ, ask=bid * Decimal("1.6"))).reasons)

    def test_stale_quote_blocked_on_real_feed(self, options_lab, guard):
        slug, acct = _ai_account(options_lab, 25_000)
        chain, cand = self._spread()
        chain_ind, cand_ind = _as_indicative(chain, cand)
        stale = _patch_quote(chain_ind, cand.legs[1].occ_symbol, is_stale=True)
        res = guard.validate_open(cand_ind, account=acct, chain=stale)
        assert any(r.startswith("stale_quote") for r in res.reasons)
        tolerant = OptionsRiskGuard(RiskGuardConfig(allow_stale_quotes=True))
        assert not any(r.startswith("stale_quote") for r in
                       tolerant.validate_open(cand_ind, account=acct, chain=stale).reasons)

    def test_zero_bid_and_missing_quote_are_blocked(self, options_lab, guard):
        slug, acct = _ai_account(options_lab, 25_000)
        chain, cand = self._spread()
        occ = cand.legs[0].occ_symbol
        res = guard.validate_open(cand, account=acct, chain=_patch_quote(chain, occ, bid=Decimal("0")))
        assert any(r.startswith("no_bid") for r in res.reasons)
        res2 = guard.validate_open(cand, account=acct, chain=_patch_quote(chain, occ, ask=None))
        assert any(r.startswith("no_ask") for r in res2.reasons)

    def test_insufficient_cash_and_collateral_blocked(self, options_lab, guard):
        slug, acct = _ai_account(options_lab, 5_000)
        chain = build_chain(AAPL)
        csp = candidate_for(StrategyType.CASH_SECURED_PUT, chain)  # ~$21k of collateral on a 5k account
        res = guard.validate_open(csp, account=acct, chain=chain)
        assert not res.passed
        assert any(r.startswith("insufficient_free_cash") for r in res.reasons)
        assert any(r.startswith("max_collateral_pct") for r in res.reasons)

    def test_covered_call_without_enough_shares_blocked(self, options_lab, guard):
        slug, acct = _ai_account(options_lab, 25_000)
        chain = build_chain(PLTR)
        cc = candidate_for(StrategyType.COVERED_CALL, chain)
        cc = cc.model_copy(update={"skills_evidence": {"covered_by_existing_shares": True, "shares_held": 50}})
        res = guard.validate_open(cc, account=acct, chain=chain)
        assert any(r.startswith("insufficient_shares") for r in res.reasons)

    def test_max_risk_per_trade_breach_blocked(self, options_lab, guard):
        slug, acct = _ai_account(options_lab, 10_000, max_risk_per_trade_pct=Decimal("1"))
        chain, cand = self._spread()
        res = guard.validate_open(cand, account=acct, chain=chain)
        assert not res.passed
        assert any(r.startswith("max_risk_per_trade") for r in res.reasons)
        assert res.effective_risk_usd > res.risk_budget_usd

    def test_absolute_risk_cap_blocks_even_a_generous_profile(self, options_lab, guard):
        slug, acct = _ai_account(options_lab, 5_000)  # permissive profile, but 915 > 12 % of 5k
        chain, cand = self._spread()
        res = guard.validate_open(cand, account=acct, chain=chain)
        assert any(r.startswith("absolute_risk_cap") for r in res.reasons)

    def test_duplicate_position_blocked_even_when_rolling_is_exempt(self, options_lab, guard):
        lab = options_lab
        slug, _ = _ai_account(lab, 25_000)
        chain, cand = self._spread()
        gid = lab.open(slug, cand, chain=chain)
        res = guard.validate_open(cand, account=lab.acct(slug), chain=chain, open_strategies=lab.groups(slug, "open"))
        assert not res.passed and any(r.startswith("duplicate_strategy") for r in res.reasons)
        roll = guard.validate_open(cand, account=lab.acct(slug), chain=chain, open_strategies=lab.groups(slug, "open"),
                                   is_roll=True, rolling_group_id=gid,
                                   released_collateral_usd=float(cand.collateral_required_usd))
        assert not any(r.startswith("duplicate_strategy") for r in roll.reasons)

    def test_options_approval_level_gates_strategies(self, options_lab, guard):
        slug, acct = _ai_account(options_lab, 25_000, options_approval_level=1)
        chain = build_chain(SPY)
        for st in (StrategyType.LONG_CALL, StrategyType.BULL_PUT_CREDIT_SPREAD, StrategyType.IRON_CONDOR):
            res = guard.validate_open(candidate_for(st, chain), account=acct, chain=chain)
            assert any(r.startswith("approval_level") for r in res.reasons), st
        pltr = build_chain(PLTR)
        ok = guard.validate_open(candidate_for(StrategyType.CASH_SECURED_PUT, pltr), account=acct, chain=pltr)
        assert not any(r.startswith("approval_level") for r in ok.reasons)

    def test_max_open_strategies_and_daily_loss_limits(self, options_lab, guard):
        lab = options_lab
        slug, _ = _ai_account(lab, 25_000, max_open_strategies=1)
        chain, cand = self._spread()
        lab.open(slug, cand, chain=chain)
        other = candidate_for(StrategyType.LONG_CALL, build_chain(AAPL))
        res = guard.validate_open(other, account=lab.acct(slug), chain=build_chain(AAPL),
                                  open_strategies=lab.groups(slug, "open"))
        assert any(r.startswith("max_open_strategies") for r in res.reasons)
        slug2, acct2 = _ai_account(lab, 25_000, max_daily_loss_pct=Decimal("2"))
        res2 = guard.validate_open(cand, account=acct2, chain=chain, daily_pnl_usd=-600.0)
        assert any(r.startswith("max_daily_loss") for r in res2.reasons)

    def test_suggest_contracts_qty_never_exceeds_cash_or_risk(self, options_lab, guard):
        slug, acct = _ai_account(options_lab, 10_000)
        chain, cand = self._spread()
        q = guard.suggest_contracts_qty(cand, acct)
        assert 0 <= q <= guard.config.max_qty_per_trade
        sized = cand.model_copy(update={"contracts_qty": 1})
        assert q * float(sized.collateral_required_usd) <= acct.free_cash + 1e-6

    # ---------------- PDT toggle -----------------------------------------------------------
    def test_pdt_rule_is_off_by_default_and_toggleable_on_close(self, options_lab, guard):
        lab = options_lab
        chain = build_chain(SPY)
        cand = candidate_for(StrategyType.LONG_CALL, chain)
        off = lab.new_account(10_000, profile=rule_profile(enforce_pdt_rule=False))
        on = lab.new_account(10_000, profile=rule_profile(enforce_pdt_rule=True))
        gid_off, gid_on = lab.open(off, cand, chain=chain), lab.open(on, cand, chain=chain)
        v_off, v_on = lab.group(gid_off), lab.group(gid_on)
        blocked = guard.validate_close(v_on, account=lab.acct(on), day_trades_last_5d=3, now=v_on.opened_at)
        assert not blocked.passed and any(r.startswith("pdt_rule") for r in blocked.reasons)
        allowed = guard.validate_close(v_off, account=lab.acct(off), day_trades_last_5d=3, now=v_off.opened_at)
        assert allowed.passed and allowed.checks["pdt_enforced"] is False
        # risk-authority exits are never blocked by PDT, nor is a 4th trade with <3 prior day trades
        assert guard.validate_close(v_on, account=lab.acct(on), day_trades_last_5d=3, mandatory=True,
                                    now=v_on.opened_at).passed
        assert guard.validate_close(v_on, account=lab.acct(on), day_trades_last_5d=2, now=v_on.opened_at).passed

    def test_pdt_rule_only_applies_below_the_equity_threshold(self, options_lab, guard):
        lab = options_lab
        chain = build_chain(SPY)
        cand = candidate_for(StrategyType.LONG_CALL, chain)
        slug = lab.new_account(30_000, profile=rule_profile(enforce_pdt_rule=True))
        gid = lab.open(slug, cand, chain=chain)
        view = lab.group(gid)
        assert guard.validate_close(view, account=lab.acct(slug), day_trades_last_5d=5, now=view.opened_at).passed

    def test_sim_engine_enforces_pdt_only_when_profile_says_so(self, options_lab):
        lab = options_lab
        chain = build_chain(SPY)
        cand = candidate_for(StrategyType.LONG_CALL, chain)
        results = {}
        for flag in (True, False):
            slug = lab.new_account(10_000, profile=rule_profile(enforce_pdt_rule=flag))
            for _ in range(3):  # three same-day round trips = three day trades
                gid = lab.open(slug, cand, chain=chain)
                lab.close(gid, chain)
            results[flag] = lab.try_open(slug, cand, chain=chain)
        assert results[False].ok is True
        assert results[True].ok is False and any("PDT" in r for r in results[True].reasons)

    def test_close_blocked_when_group_not_open(self, options_lab, guard):
        lab = options_lab
        chain = build_chain(SPY)
        slug = lab.new_account(10_000)
        gid = lab.open(slug, candidate_for(StrategyType.LONG_CALL, chain), chain=chain)
        lab.close(gid, chain)
        res = guard.validate_close(lab.group(gid), account=lab.acct(slug))
        assert not res.passed and any(r.startswith("not_open") for r in res.reasons)


# ======================================================================================
# Cohort / account isolation (in-memory engine)
# ======================================================================================


class TestCohortIsolation:
    def test_lab_seed_defines_5k_10k_25k_ai_accounts_plus_five_10k_rules_cohorts(self):
        by_slug = {s.slug: s for s in LAB_ACCOUNT_SEEDS}
        assert LAB_ACCOUNT_SLUGS[:3] == ("options-5k", "options-10k", "options-25k")
        assert [float(by_slug[s].starting_capital) for s in ("options-5k", "options-10k", "options-25k")] == [
            5_000.0, 10_000.0, 25_000.0]
        assert all(by_slug[s].mode == DecisionMode.AI for s in ("options-5k", "options-10k", "options-25k"))
        cohorts = [s for s in LAB_ACCOUNT_SEEDS if s.mode == DecisionMode.RULES]
        assert len(cohorts) == 5 and {float(c.starting_capital) for c in cohorts} == {10_000.0}
        assert {c.strategy_scope for c in cohorts} == {None, *STRATEGY_SCOPE_TYPES}
        assert len(set(LAB_ACCOUNT_SLUGS)) == len(LAB_ACCOUNT_SLUGS)
        assert all(s.rule_profile.enforce_pdt_rule is False for s in LAB_ACCOUNT_SEEDS)

    def test_scopes_partition_the_nine_strategies_without_overlap(self):
        flat = [t for types in STRATEGY_SCOPE_TYPES.values() for t in types]
        assert len(flat) == len(set(flat)), "a strategy type belongs to at most one cohort scope"
        assert strategy_types_for_scope(None) is None
        assert strategy_types_for_scope("long_only") == {"long_call", "long_put"}
        assert strategy_types_for_scope("iron_condor") == {"iron_condor"}
        assert strategy_types_for_scope("wheel_csp_cc") == {"cash_secured_put", "covered_call", "wheel"}
        assert strategy_types_for_scope("credit_spreads") == {"bull_put_credit_spread", "bear_call_credit_spread"}

    def test_trading_in_one_account_never_moves_another(self, options_lab):
        lab = options_lab
        slugs = {name: lab.new_account(cap, mode=m, scope=sc) for name, cap, m, sc in [
            ("5k", 5_000, "ai", None), ("10k", 10_000, "ai", None), ("25k", 25_000, "ai", None),
            ("rules", 10_000, "rules", None), ("long", 10_000, "rules", "long_only"),
            ("credit", 10_000, "rules", "credit_spreads"), ("wheel", 10_000, "rules", "wheel_csp_cc"),
            ("condor", 10_000, "rules", "iron_condor")]}
        before = {k: lab.acct(s).model_dump(exclude={"updated_at"}) for k, s in slugs.items()}
        chain = build_chain(SPY)
        lab.open(slugs["25k"], candidate_for(StrategyType.BULL_PUT_CREDIT_SPREAD, chain), chain=chain)
        lab.open(slugs["long"], candidate_for(StrategyType.LONG_CALL, chain), chain=chain)
        after = {k: lab.acct(s).model_dump(exclude={"updated_at"}) for k, s in slugs.items()}
        changed = {k for k in slugs if before[k] != after[k]}
        assert changed == {"25k", "long"}
        for k in set(slugs) - changed:
            assert after[k]["cash_balance"] == before[k]["cash_balance"] == after[k]["starting_capital"]
            assert after[k]["open_strategies_count"] == 0
        assert lab.groups(slugs["25k"], "open") and not lab.groups(slugs["10k"], "open")
        assert lab.acct(slugs["5k"]).starting_capital == 5_000

    def test_cohort_accounts_with_same_strategy_keep_independent_books(self, options_lab):
        lab = options_lab
        a = lab.new_account(10_000, mode="rules", scope="credit_spreads")
        b = lab.new_account(10_000, mode="rules", scope="credit_spreads")
        chain = build_chain(SPY)
        cand = candidate_for(StrategyType.BULL_PUT_CREDIT_SPREAD, chain)
        gid_a = lab.open(a, cand, chain=chain)
        gid_b = lab.open(b, cand, chain=chain)
        assert gid_a != gid_b
        lab.close(gid_a, chain)
        assert [g(x, "id") for x in lab.groups(b, "open")] == [gid_b]
        assert f(lab.acct(b).cash_reserved) > 0 and f(lab.acct(a).cash_reserved) == pytest.approx(0, abs=0.01)

    def test_scope_filter_selects_exactly_the_allowed_candidates(self):
        chain = build_chain(SPY)
        cands = [candidate_for(st, chain) for st in StrategyType if st != StrategyType.WHEEL]
        allowed = strategy_types_for_scope("credit_spreads")
        kept = {c.strategy_type.value for c in cands if c.strategy_type.value in allowed}
        assert kept == {"bull_put_credit_spread", "bear_call_credit_spread"}


# ======================================================================================
# PostgreSQL persistence across restart (real OptionsRepository)
# ======================================================================================


@pytest.fixture
def pg(broker_test_database):
    """Real-PostgreSQL ``OptionsRepository`` factory with guaranteed cleanup of ``__optqa_*`` rows."""
    from quantara_engine.db import session as dbs
    from quantara_options.db.repository import OptionsRepository

    s0 = dbs.SessionLocal()
    if not pg_table_exists(s0, "opt_accounts"):
        s0.close()
        pytest.skip("opt_* tables not present (migration 0025 not applied to the test database)")
    pg_purge_qa_rows(s0)
    s0.close()

    opened = []

    def new_repo():
        sess = dbs.SessionLocal()
        opened.append(sess)
        return OptionsRepository(sess, autocommit=True)

    try:
        yield new_repo
    finally:
        for sess in opened:
            sess.rollback()
            sess.close()
        cleaner = dbs.SessionLocal()
        try:
            pg_purge_qa_rows(cleaner)
        finally:
            cleaner.close()


def _restart(broker_test_database):
    """Simulate a process restart: drop every pooled connection of the shared engine."""
    from quantara_engine.db import session as dbs

    dbs.engine.dispose()


def _new_qa_slug(tag: str) -> str:
    return f"{OPTQA_PREFIX}{tag}_{uuid.uuid4().hex[:8]}"


class TestPostgresPersistence:
    def test_open_position_and_stock_lot_survive_a_restart(self, pg, broker_test_database):
        repo = pg()
        slug = _new_qa_slug("persist")
        pg_insert_qa_account(repo.session, slug, 10_000)
        chain = build_chain(SPY)
        cand = candidate_for(StrategyType.BULL_PUT_CREDIT_SPREAD, chain)
        view = repo.create_strategy_with_legs_and_fill(
            slug, cand, fills=[{"fill_price": l.bid if l.side.value == "short" else l.ask, "fee_usd": 0.73}
                               for l in cand.legs],
            idempotency_key=f"optqa:{slug}:open",
        )
        repo.upsert_stock_lot(slug, PLTR, shares=100, avg_cost_basis=Decimal("41.5"), encumbered_shares=100)
        repo.set_wheel_state(slug, PLTR, "short_call")
        before = repo.get_account_summary(slug)
        assert view.status == "open" and before.open_strategies_count == 1
        legs_before = sorted((l.occ_symbol, l.entry_price, l.quantity, l.multiplier) for l in view.legs)

        _restart(broker_test_database)  # brand-new connections, brand-new repository
        repo2 = pg()
        assert repo2 is not repo and repo2.session is not repo.session

        opens = repo2.list_strategy_groups(slug, "open")
        assert [x.id for x in opens] == [view.id]
        again = opens[0]
        assert again.strategy_type == StrategyType.BULL_PUT_CREDIT_SPREAD.value and again.status == "open"
        assert sorted((l.occ_symbol, l.entry_price, l.quantity, l.multiplier) for l in again.legs) == legs_before
        assert again.collateral_locked_usd == pytest.approx(view.collateral_locked_usd)
        assert again.max_loss_usd == pytest.approx(float(cand.max_loss_usd), abs=0.01)
        assert again.expiration_date == EXP_21 and again.net_entry_cash_flow == pytest.approx(view.net_entry_cash_flow)

        after = repo2.get_account_summary(slug)
        for field in ("cash_balance", "cash_reserved", "free_cash", "equity", "premium_collected_gross",
                      "fees_paid", "short_options_liability", "long_options_market_value",
                      "open_strategies_count", "closed_strategies_count"):
            assert getattr(after, field) == pytest.approx(getattr(before, field)), field
        assert after.wheel_states == {PLTR: "short_call"} and repo2.get_wheel_state(slug, PLTR) == "short_call"
        lot = repo2.get_stock_lot(slug, PLTR)
        assert lot["shares"] == 100 and lot["encumbered_shares"] == 100 and lot["free_shares"] == 0
        assert Decimal(str(lot["avg_cost_basis"])) == Decimal("41.5000")
        events = repo2.list_journal_events(slug)
        assert any(e.event_type == "OPEN" for e in events), "ledger survived the restart too"

    def test_replaying_the_same_idempotency_key_does_not_double_book(self, pg):
        repo = pg()
        slug = _new_qa_slug("idem")
        pg_insert_qa_account(repo.session, slug, 10_000)
        cand = candidate_for(StrategyType.LONG_CALL, build_chain(SPY))
        key = f"optqa:{slug}:once"
        first = repo.create_strategy_with_legs_and_fill(slug, cand, idempotency_key=key)
        cash_after_first = repo.get_account_summary(slug).cash_balance
        second = repo.create_strategy_with_legs_and_fill(slug, cand, idempotency_key=key)
        assert second.id == first.id
        summary = repo.get_account_summary(slug)
        assert summary.cash_balance == pytest.approx(cash_after_first) and summary.open_strategies_count == 1

    def test_closed_history_and_open_book_stay_separate_after_restart(self, pg, broker_test_database):
        repo = pg()
        slug = _new_qa_slug("hist")
        pg_insert_qa_account(repo.session, slug, 10_000)
        chain = build_chain(SPY)
        keep = repo.create_strategy_with_legs_and_fill(
            slug, candidate_for(StrategyType.LONG_CALL, chain), idempotency_key=f"optqa:{slug}:a")
        gone = repo.create_strategy_with_legs_and_fill(
            slug, candidate_for(StrategyType.LONG_PUT, chain), idempotency_key=f"optqa:{slug}:b")
        closed = repo.close_strategy_record(gone.id, status="closed", close_reason="manual_close")
        assert closed.status == "closed"
        summary_before = repo.get_account_summary(slug)
        _restart(broker_test_database)
        repo2 = pg()
        open_ids = {x.id for x in repo2.list_strategy_groups(slug, "open")}
        closed_ids = {x.id for x in repo2.list_strategy_groups(slug, "closed")}
        assert open_ids == {keep.id} and closed_ids == {gone.id}
        summary = repo2.get_account_summary(slug)
        assert (summary.open_strategies_count, summary.closed_strategies_count) == (1, 1)
        assert summary.realized_pnl == pytest.approx(summary_before.realized_pnl)
        assert repo2.close_strategy_record(gone.id).status == "closed", "re-closing is an idempotent no-op"

    def test_options_cohorts_5k_10k_25k_are_isolated_in_postgres(self, pg):
        repo = pg()
        repo.ensure_lab_seeded()  # idempotent; never modifies existing balances
        lab_before = {a.slug: a.model_dump(exclude={"updated_at", "created_at"}) for a in repo.list_accounts()}
        assert set(LAB_ACCOUNT_SLUGS) <= set(lab_before)
        slugs = [_new_qa_slug(t) for t in ("c5k", "c10k", "c25k")]
        for s, cap in zip(slugs, (5_000, 10_000, 25_000)):
            pg_insert_qa_account(repo.session, s, cap)
        snap = {s: repo.get_account_summary(s).model_dump(exclude={"updated_at", "created_at"}) for s in slugs}
        chain = build_chain(SPY)
        repo.create_strategy_with_legs_and_fill(
            slugs[2], candidate_for(StrategyType.BULL_PUT_CREDIT_SPREAD, chain), idempotency_key=f"optqa:{slugs[2]}:x")
        for s in slugs[:2]:
            assert repo.get_account_summary(s).model_dump(exclude={"updated_at", "created_at"}) == snap[s]
            assert repo.list_strategy_groups(s) == []
        assert repo.get_account_summary(slugs[2]).open_strategies_count == 1
        lab_after = {a.slug: a.model_dump(exclude={"updated_at", "created_at"}) for a in repo.list_accounts()
                     if a.slug in LAB_ACCOUNT_SLUGS}
        assert {k: lab_before[k] for k in LAB_ACCOUNT_SLUGS} == lab_after, "lab cohorts untouched by QA accounts"