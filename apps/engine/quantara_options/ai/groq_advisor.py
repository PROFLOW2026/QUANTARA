"""Groq-backed options decision advisor with a deterministic Rules mode and clean fallback.

Mirrors System A (`QUANTARA-AI-Trader/ai_trader/ai_engine.py`): OpenAI-compatible
chat completions on Groq, model `openai/gpt-oss-120b`, `response_format=json_object`,
`reasoning_effort="low"`, `reasoning_format="hidden"`.

Key resolution (first wins, never logged, never returned):
  1. AI_BASE_URL + AI_API_KEY (+ AI_MODEL)
  2. GROQ_API_KEY (+ AI_MODEL)
  3. read-only `../QUANTARA-AI-Trader/trader/ai-secrets.json` (same names, or provider/api_key/model/base_url)

The model can only pick from pre-filtered, pre-priced candidates by `candidate_id` and
manage existing groups by `position_group_id`; anything else is discarded (grounding).
Groq free/dev tier is ~8K tokens/minute and is shared with System A, so prompts are kept
small (top 4-6 compact candidates) and a sliding-window token budget gates every call.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
import urllib.error
import urllib.request
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

from quantara_options.contracts import (
    AIAction,
    AIDecisionRecord,
    DecisionMode,
    OptionAccountSummary,
    StrategyCandidate,
    StrategyType,
)
from quantara_options.strategies.catalog import (
    STRATEGY_CATALOG,
    StrategyActionRecommendation,
)

log = logging.getLogger("quantara_options.groq_advisor")

GROQ_DEFAULT_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_DEFAULT_MODEL = "openai/gpt-oss-120b"
DECIDED_BY_AI = "groq_openai_gpt_oss_120b"
DECIDED_BY_RULES = "rules_engine"
DECIDED_BY_FALLBACK = "skills_rules_fallback"

_KEY_PATTERNS = [re.compile(r"gsk_[A-Za-z0-9]{8,}"), re.compile(r"sk-[A-Za-z0-9_\-]{8,}"),
                 re.compile(r"Bearer\s+[A-Za-z0-9_\-\.]{8,}")]

Transport = Callable[[str, dict[str, str], bytes, float], tuple[int, dict[str, Any], dict[str, str]]]


def _default_secrets_path() -> Path:
    # apps/engine/quantara_options/ai/groq_advisor.py -> FINAL-WEB/QUANTARA-AI-Trader/trader/ai-secrets.json
    root = os.environ.get("QUANTARA_AI_TRADER_ROOT")
    base = Path(root) if root else Path(__file__).resolve().parents[5] / "QUANTARA-AI-Trader"
    return base / "trader" / "ai-secrets.json"


def _chat_url(base: str) -> str:
    base = base.rstrip("/")
    return base if base.endswith("/chat/completions") else base + "/chat/completions"


@dataclass
class AdvisorConfig:
    model: str = GROQ_DEFAULT_MODEL
    endpoint: str = GROQ_DEFAULT_URL
    timeout_s: float = 30.0
    retries: int = 1
    backoff_s: float = 1.5
    temperature: float = 0.2
    max_output_tokens: int = 900
    reasoning_effort: str = "low"
    reasoning_format: str = "hidden"
    max_calls_per_day: int = 60
    max_calls_per_cycle: int = 3
    max_candidates_in_prompt: int = 5
    min_candidates_in_prompt: int = 4
    tpm_limit: int = 8000
    tpm_safety: float = 0.85
    max_prompt_tokens: int = 2600
    min_confidence_open: float = 0.55
    max_new_per_cycle: int = 2
    min_rules_score: float = 55.0
    max_open_per_underlying: int = 2
    rate_limit_cooldown_s: float = 60.0
    count_system_a_usage: bool = True
    emit_hold_records: bool = False


@dataclass
class _Credentials:
    url: str
    key: str
    model: str
    provider: str
    source: str


@dataclass
class AdvisorResult:
    decisions: list[AIDecisionRecord] = field(default_factory=list)
    decided_by: str = DECIDED_BY_RULES
    mode: DecisionMode = DecisionMode.RULES
    ai_called: bool = False
    fallback_reason: Optional[str] = None
    tokens_in: Optional[int] = None
    tokens_out: Optional[int] = None
    latency_s: Optional[float] = None
    prompt_tokens_est: Optional[int] = None
    candidate_ids_sent: list[str] = field(default_factory=list)
    discarded: list[str] = field(default_factory=list)
    market_view_he: str = ""
    market_view_en: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "decided_by": self.decided_by,
            "mode": self.mode.value,
            "ai_called": self.ai_called,
            "fallback_reason": self.fallback_reason,
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "latency_s": self.latency_s,
            "prompt_tokens_est": self.prompt_tokens_est,
            "candidate_ids_sent": self.candidate_ids_sent,
            "discarded": self.discarded,
            "decisions": [d.model_dump(mode="json") for d in self.decisions],
        }


class AdvisorError(Exception):
    def __init__(self, kind: str, msg: str, retryable: bool = False, retry_after: Optional[float] = None):
        super().__init__(f"{kind}: {msg}")
        self.kind, self.msg, self.retryable, self.retry_after = kind, msg, retryable, retry_after


def extract_json(text: Optional[str]) -> dict[str, Any]:
    """Strict JSON from a model reply: whole text, ```json fence, or the first balanced {...}."""
    if not text:
        raise ValueError("empty reply")
    t = text.strip()
    try:
        obj = json.loads(t)
        if isinstance(obj, dict):
            return obj
    except ValueError:
        pass
    if "```" in t:
        for part in t.split("```")[1::2]:
            p = part.strip()
            if p[:4].lower() == "json":
                p = p[4:]
            try:
                obj = json.loads(p.strip())
                if isinstance(obj, dict):
                    return obj
            except ValueError:
                continue
    start = t.find("{")
    while start != -1:
        depth, in_str, esc = 0, False, False
        for i in range(start, len(t)):
            ch = t[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
            elif ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        obj = json.loads(t[start:i + 1])
                        if isinstance(obj, dict):
                            return obj
                    except ValueError:
                        break
                    break
        start = t.find("{", start + 1)
    raise ValueError("no JSON object in reply")


def _urllib_transport(url: str, headers: dict[str, str], body: bytes,
                      timeout: float) -> tuple[int, dict[str, Any], dict[str, str]]:
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode("utf-8", "replace")), dict(r.headers.items())
    except urllib.error.HTTPError as exc:
        detail: dict[str, Any] = {}
        try:
            detail = json.loads(exc.read().decode("utf-8", "replace")[:2000])
        except Exception:
            detail = {}
        return exc.code, detail, dict(exc.headers.items()) if exc.headers else {}


SYSTEM_PROMPT = """You are the decision engine of a PRIVATE OPTIONS PAPER-TRADING SIMULATION (no real money, no broker).
Each cycle you get: account state and limits, open option strategy groups with deterministic rule signals,
pre-screened candidates fully priced by a rules engine (per-contract multiplier already applied, USD totals),
and compact news/macro context from analysis skills. Decide what the simulated account should do now.

HARD RULES
- OPEN only a candidate by its exact "id" from the list. Never invent symbols, strikes, expirations or OCC codes.
- CLOSE / ROLL / HOLD must reference an open group "gid". Groups with "mand":true are closed by risk authority anyway.
- At most {max_new} OPEN decisions. qty must be <= the candidate "qty" (you may size down, never up).
- Prefer: higher score, PoP and liquidity; fit with news/macro bias; diversification across underlyings.
- Avoid: short premium into earnings ("earn":true), stacking opposite directions on one underlying,
  spending most free cash in one trade. SKIP when nothing is attractive.
- Rationales are short (<=160 chars) and factual: Hebrew in rationale_he, English in rationale_en.

Reply with ONE JSON object only:
{{"decisions":[{{"action":"OPEN|HOLD|CLOSE|ROLL|SKIP","id":"<candidate id or null>","gid":"<group id or null>",
"qty":1,"confidence":0.0,"rationale_he":"...","rationale_en":"..."}}],"view_he":"...","view_en":"..."}}"""


def _direction_sign(st: StrategyType) -> int:
    d = STRATEGY_CATALOG[st].direction
    if d in {"bullish", "neutral_bullish"}:
        return 1
    if d in {"bearish", "neutral_bearish"}:
        return -1
    return 0


def _attr(obj: Any, key: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _enum_val(v: Any) -> Any:
    return getattr(v, "value", v)


class GroqOptionsAdvisor:
    def __init__(
        self,
        config: Optional[AdvisorConfig] = None,
        *,
        env: Optional[dict[str, str]] = None,
        secrets_path: Optional[Path | str] = None,
        transport: Optional[Transport] = None,
        system_a_status_reader: Optional[Callable[[], dict[str, Any]]] = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.config = config or AdvisorConfig()
        self._env = env if env is not None else os.environ
        self._secrets_path = Path(secrets_path) if secrets_path else _default_secrets_path()
        self._transport = transport or _urllib_transport
        self._system_a_status = system_a_status_reader
        self._sleep = sleep
        self._lock = threading.Lock()
        self._creds: Optional[_Credentials] = None
        self._creds_reason = "not resolved"
        self._token_window: deque[tuple[float, int]] = deque()
        self.calls_today = 0
        self.calls_date: date = datetime.now(timezone.utc).date()
        self.calls_this_cycle = 0
        self.last_error: Optional[str] = None
        self.last_ok_at: Optional[datetime] = None
        self.cooldown_until: float = 0.0
        self.fallback_count = 0
        self._resolve_credentials()

    # ------------------------------------------------------------ credentials / status

    def _resolve_credentials(self) -> None:
        cfg = self.config

        def g(src: dict[str, Any], k: str) -> str:
            v = src.get(k)
            return v.strip() if isinstance(v, str) else ""

        def pick(src: dict[str, Any], label: str) -> Optional[_Credentials]:
            if g(src, "AI_BASE_URL") and g(src, "AI_API_KEY"):
                url = _chat_url(g(src, "AI_BASE_URL"))
                provider = "groq" if "groq.com" in url else "generic"
                return _Credentials(url, g(src, "AI_API_KEY"), g(src, "AI_MODEL") or cfg.model, provider, label)
            if g(src, "GROQ_API_KEY"):
                return _Credentials(cfg.endpoint, g(src, "GROQ_API_KEY"), g(src, "AI_MODEL") or cfg.model,
                                    "groq", label)
            return None

        creds = pick(dict(self._env), "env")
        if creds is None:
            try:
                with open(self._secrets_path, encoding="utf-8") as fh:
                    secrets = json.load(fh)
                if isinstance(secrets, dict):
                    flat = {k: v for k, v in secrets.items() if isinstance(k, str) and k.isupper()}
                    if secrets.get("api_key"):
                        name = str(secrets.get("provider") or "groq").lower()
                        if name == "groq":
                            flat.setdefault("GROQ_API_KEY", secrets["api_key"])
                        else:
                            flat.setdefault("AI_API_KEY", secrets["api_key"])
                            if secrets.get("base_url"):
                                flat["AI_BASE_URL"] = secrets["base_url"]
                        if secrets.get("model"):
                            flat["AI_MODEL"] = secrets["model"]
                    creds = pick(flat, "ai-secrets.json")
            except (OSError, ValueError):
                creds = None
        if self._env.get("QUANTARA_OPTIONS_AI_DISABLED", "").strip().lower() in {"1", "true", "yes"}:
            self._creds, self._creds_reason = None, "disabled by QUANTARA_OPTIONS_AI_DISABLED"
            return
        self._creds = creds
        self._creds_reason = "ok" if creds else "no AI key (AI_BASE_URL+AI_API_KEY, GROQ_API_KEY, ai-secrets.json)"

    @property
    def configured(self) -> bool:
        return self._creds is not None

    @property
    def model_name(self) -> str:
        return self._creds.model if self._creds else self.config.model

    @property
    def groq_available(self) -> bool:
        self._roll_day()
        return (self.configured and time.time() >= self.cooldown_until
                and self.calls_today < self.config.max_calls_per_day)

    def _redact(self, text: Any) -> str:
        s = str(text)
        if self._creds and self._creds.key:
            s = s.replace(self._creds.key, "***")
        for rx in _KEY_PATTERNS:
            s = rx.sub("***", s)
        return s[:300]

    def _roll_day(self) -> None:
        today = datetime.now(timezone.utc).date()
        if today != self.calls_date:
            self.calls_date, self.calls_today = today, 0

    def begin_cycle(self) -> None:
        self.calls_this_cycle = 0

    def status(self) -> dict[str, Any]:
        self._roll_day()
        return {
            "groq_available": self.groq_available,
            "configured": self.configured,
            "config_reason": self._creds_reason,
            "provider": self._creds.provider if self._creds else None,
            "key_source": self._creds.source if self._creds else None,
            "model_name": self.model_name,
            "calls_today": self.calls_today,
            "calls_date": self.calls_date.isoformat(),
            "max_calls_per_day": self.config.max_calls_per_day,
            "fallback_count": self.fallback_count,
            "last_error": self.last_error,
            "last_ok_at": self.last_ok_at.isoformat() if self.last_ok_at else None,
            "cooldown_remaining_s": max(0.0, round(self.cooldown_until - time.time(), 1)),
            "tokens_last_minute": self._tokens_last_minute(),
        }

    # ------------------------------------------------------------ token budget

    def _tokens_last_minute(self) -> int:
        now = time.time()
        while self._token_window and now - self._token_window[0][0] > 60.0:
            self._token_window.popleft()
        own = sum(t for _, t in self._token_window)
        shared = 0
        if self.config.count_system_a_usage and self._system_a_status is not None:
            try:
                st = self._system_a_status() or {}
                ts = st.get("last_ok_utc")
                if ts:
                    when = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
                    if (datetime.now(timezone.utc) - when).total_seconds() <= 60:
                        shared = int(st.get("tokens_in") or 0) + int(st.get("tokens_out") or 0)
            except Exception:
                shared = 0
        return own + shared

    @staticmethod
    def estimate_tokens(text: str) -> int:
        return int(len(text) / 3.6) + 8

    # ------------------------------------------------------------ public decide

    def decide(
        self,
        *,
        account: OptionAccountSummary,
        candidates: list[StrategyCandidate],
        open_strategies: Iterable[Any] = (),
        open_recommendations: Iterable[StrategyActionRecommendation] = (),
        skills_context: Optional[dict[str, Any]] = None,
        mode: Optional[DecisionMode] = None,
        market_context: Optional[dict[str, Any]] = None,
        now: Optional[datetime] = None,
    ) -> AdvisorResult:
        mode = mode or account.mode
        open_list = list(open_strategies)
        recs = list(open_recommendations)
        ctx = dict(market_context or {})
        pool = self._eligible_pool(account, candidates, open_list)

        if mode == DecisionMode.RULES:
            res = self._rules_result(account, pool, recs, DECIDED_BY_RULES, ctx)
            res.mode = DecisionMode.RULES
            return res

        needs_ai = bool(pool) or any(r.action != AIAction.HOLD and not r.mandatory for r in recs)
        if not needs_ai:
            res = self._rules_result(account, pool, recs, DECIDED_BY_RULES, ctx)
            res.mode = DecisionMode.AI
            res.fallback_reason = "nothing_to_decide"
            return res

        reason = self._ai_blocker()
        if reason:
            return self._fallback(account, pool, recs, ctx, reason)

        prompt_pool = self._prompt_pool(pool)
        system = SYSTEM_PROMPT.format(max_new=self._max_new(account, open_list))
        user, sent_ids = self._build_user_prompt(account, prompt_pool, open_list, recs, skills_context or {}, ctx)
        est = self.estimate_tokens(system) + self.estimate_tokens(user)
        while est > self.config.max_prompt_tokens and len(prompt_pool) > self.config.min_candidates_in_prompt - 2:
            prompt_pool = prompt_pool[:-1]
            user, sent_ids = self._build_user_prompt(account, prompt_pool, open_list, recs, skills_context or {}, ctx)
            est = self.estimate_tokens(system) + self.estimate_tokens(user)
        budget = int(self.config.tpm_limit * self.config.tpm_safety)
        if self._tokens_last_minute() + est + self.config.max_output_tokens > budget:
            return self._fallback(account, pool, recs, ctx, "tpm_budget")

        try:
            t0 = time.time()
            text, tin, tout = self._complete(system, user)
            latency = round(time.time() - t0, 2)
            parsed = extract_json(text)
        except AdvisorError as exc:
            self.last_error = self._redact(f"{exc.kind}: {exc.msg}")
            if exc.kind == "http_429":
                self.cooldown_until = time.time() + (exc.retry_after or self.config.rate_limit_cooldown_s)
            return self._fallback(account, pool, recs, ctx, exc.kind, prompt_est=est)
        except ValueError as exc:
            self.last_error = self._redact(f"bad_json: {exc}")
            return self._fallback(account, pool, recs, ctx, "bad_json", prompt_est=est)

        self.last_ok_at = datetime.now(timezone.utc)
        self.last_error = None
        res = self._ground(account, parsed, prompt_pool, open_list, recs, ctx)
        res.mode = DecisionMode.AI
        res.ai_called = True
        res.tokens_in, res.tokens_out, res.latency_s = tin, tout, latency
        res.prompt_tokens_est = est
        res.candidate_ids_sent = sent_ids
        return res

    # ------------------------------------------------------------ selection helpers

    def _max_new(self, account: OptionAccountSummary, open_list: list[Any]) -> int:
        slots = max(0, int(account.rule_profile.max_open_strategies) - len(open_list))
        return max(0, min(self.config.max_new_per_cycle, slots))

    def _eligible_pool(self, account: OptionAccountSummary, candidates: list[StrategyCandidate],
                       open_list: list[Any]) -> list[StrategyCandidate]:
        """Deterministic pre-filter: score floor, approval level, duplicates, direction conflicts."""
        level = int(account.rule_profile.options_approval_level)
        open_keys: set[tuple[str, str, str]] = set()
        per_und: dict[str, list[int]] = {}
        open_types: set[tuple[str, str]] = set()
        for g in open_list:
            und = str(_attr(g, "underlying_symbol", "") or "")
            st_raw = _enum_val(_attr(g, "strategy_type"))
            exp = _attr(g, "expiration_date")
            exp_s = exp.isoformat() if hasattr(exp, "isoformat") else str(exp or "")
            open_keys.add((und, str(st_raw), exp_s))
            open_types.add((und, str(st_raw)))
            try:
                per_und.setdefault(und, []).append(_direction_sign(StrategyType(st_raw)))
            except ValueError:
                per_und.setdefault(und, []).append(0)
        out: list[StrategyCandidate] = []
        for c in sorted(candidates, key=lambda c: (-c.rules_score, c.candidate_id)):
            spec = STRATEGY_CATALOG[c.strategy_type]
            if spec.min_approval_level > level:
                continue
            if c.rules_score < self.config.min_rules_score:
                continue
            if (c.underlying_symbol, c.strategy_type.value, c.expiration_date.isoformat()) in open_keys:
                continue
            signs = per_und.get(c.underlying_symbol, [])
            is_wheel_call = (c.strategy_type == StrategyType.WHEEL
                             and c.skills_evidence.get("wheel_next_state") == "short_call")
            is_cc_on_shares = (c.strategy_type == StrategyType.COVERED_CALL
                               and c.skills_evidence.get("covered_by_existing_shares"))
            if not (is_wheel_call or is_cc_on_shares):
                if len(signs) >= self.config.max_open_per_underlying:
                    continue
                s = _direction_sign(c.strategy_type)
                if s != 0 and any(x == -s for x in signs):
                    continue
                if (c.underlying_symbol, c.strategy_type.value) in open_types:
                    continue
            out.append(c)
        return out

    def _prompt_pool(self, pool: list[StrategyCandidate]) -> list[StrategyCandidate]:
        """Top 4-6 diverse candidates: <=2 per strategy type and <=2 per underlying."""
        per_type: dict[str, int] = {}
        per_sym: dict[str, int] = {}
        out: list[StrategyCandidate] = []
        for c in pool:
            if per_type.get(c.strategy_type.value, 0) >= 2 or per_sym.get(c.underlying_symbol, 0) >= 2:
                continue
            out.append(c)
            per_type[c.strategy_type.value] = per_type.get(c.strategy_type.value, 0) + 1
            per_sym[c.underlying_symbol] = per_sym.get(c.underlying_symbol, 0) + 1
            if len(out) >= self.config.max_candidates_in_prompt:
                break
        return out

    def _cash_need(self, c: StrategyCandidate) -> float:
        debit = -float(c.estimated_net_premium_total) if float(c.estimated_net_premium_total) < 0 else 0.0
        return debit + float(c.collateral_required_usd)

    def _rules_pick(self, account: OptionAccountSummary, pool: list[StrategyCandidate],
                    open_list_len: int) -> list[StrategyCandidate]:
        max_new = max(0, min(self.config.max_new_per_cycle,
                             int(account.rule_profile.max_open_strategies) - open_list_len))
        cash_left = float(account.free_cash) * 0.95
        chosen: list[StrategyCandidate] = []
        used_syms: set[str] = set()
        for c in pool:
            if len(chosen) >= max_new:
                break
            if c.underlying_symbol in used_syms:
                continue
            need = self._cash_need(c)
            if need > cash_left:
                continue
            chosen.append(c)
            used_syms.add(c.underlying_symbol)
            cash_left -= need
        return chosen

    # ------------------------------------------------------------ record builders

    def _record(self, account: OptionAccountSummary, *, action: AIAction, decided_by: str,
                ctx: dict[str, Any], candidate: Optional[StrategyCandidate] = None,
                rec: Optional[StrategyActionRecommendation] = None, qty: Optional[int] = None,
                confidence: float = 0.0, he: str = "", en: str = "", mode: DecisionMode) -> AIDecisionRecord:
        st = candidate.strategy_type if candidate else (rec.strategy_type if rec else None)
        und = candidate.underlying_symbol if candidate else (rec.underlying_symbol if rec else "")
        mc = dict(ctx)
        if rec is not None:
            mc["rule_signal"] = {"reason_code": rec.reason_code, "mandatory": rec.mandatory,
                                 "urgency": rec.urgency, "metrics": rec.metrics, "roll_hint": rec.roll_hint}
        if candidate is not None:
            mc["candidate"] = {"rules_score": candidate.rules_score, "pop": candidate.probability_of_profit,
                               "max_loss_usd": float(candidate.max_loss_usd),
                               "collateral_usd": float(candidate.collateral_required_usd)}
        return AIDecisionRecord(
            decision_id=f"dec_{uuid.uuid4().hex[:16]}",
            account_slug=account.slug,
            mode=mode,
            action=action,
            strategy_type=st,
            candidate_id=candidate.candidate_id if candidate else None,
            position_group_id=rec.position_group_id if rec else None,
            underlying_symbol=und or "",
            contracts_qty=int(qty or (candidate.contracts_qty if candidate else 1)),
            confidence=round(max(0.0, min(1.0, confidence)), 3),
            decided_by=decided_by,
            model_name=self.model_name if decided_by == DECIDED_BY_AI else None,
            rationale_he=he[:600],
            rationale_en=en[:600],
            market_context=mc,
        )

    def _rules_result(self, account: OptionAccountSummary, pool: list[StrategyCandidate],
                      recs: list[StrategyActionRecommendation], decided_by: str,
                      ctx: dict[str, Any], prefix_he: str = "", prefix_en: str = "") -> AdvisorResult:
        mode = account.mode
        res = AdvisorResult(decided_by=decided_by, mode=mode)
        for r in recs:
            if r.action == AIAction.HOLD and not self.config.emit_hold_records:
                continue
            res.decisions.append(self._record(
                account, action=r.action, decided_by=decided_by, ctx=ctx, rec=r,
                confidence=0.9 if r.mandatory else 0.75,
                he=prefix_he + r.rationale_he, en=prefix_en + r.rationale_en, mode=mode))
        n_open_after = len([r for r in recs if r.action not in (AIAction.CLOSE,)])
        for c in self._rules_pick(account, pool, n_open_after):
            spec = STRATEGY_CATALOG[c.strategy_type]
            en = (f"{spec.label_en} on {c.underlying_symbol} exp {c.expiration_date} ({c.dte} DTE): "
                  f"score {c.rules_score:.0f}, PoP {(c.probability_of_profit or 0):.0%}, "
                  f"max loss ${float(c.max_loss_usd):,.0f}, collateral ${float(c.collateral_required_usd):,.0f}.")
            res.decisions.append(self._record(
                account, action=AIAction.OPEN, decided_by=decided_by, ctx=ctx, candidate=c,
                confidence=c.rules_score / 100.0, he=prefix_he + c.rationale_summary, en=prefix_en + en, mode=mode))
        return res

    def _fallback(self, account: OptionAccountSummary, pool: list[StrategyCandidate],
                  recs: list[StrategyActionRecommendation], ctx: dict[str, Any], reason: str,
                  prompt_est: Optional[int] = None) -> AdvisorResult:
        self.fallback_count += 1
        res = self._rules_result(account, pool, recs, DECIDED_BY_FALLBACK, {**ctx, "fallback_reason": reason},
                                 prefix_he=f"[גיבוי כללים: {reason}] ", prefix_en=f"[rules fallback: {reason}] ")
        res.mode = DecisionMode.AI
        res.fallback_reason = reason
        res.prompt_tokens_est = prompt_est
        return res

    def _ai_blocker(self) -> Optional[str]:
        self._roll_day()
        if not self.configured:
            return "ai_not_configured"
        if time.time() < self.cooldown_until:
            return "rate_limited_cooldown"
        if self.calls_today >= self.config.max_calls_per_day:
            return "daily_call_cap"
        if self.calls_this_cycle >= self.config.max_calls_per_cycle:
            return "cycle_call_cap"
        return None

    # ------------------------------------------------------------ prompt

    @staticmethod
    def _compact_candidate(c: StrategyCandidate) -> dict[str, Any]:
        legs = []
        for l in c.legs:
            if l.right is None:
                legs.append("L STK")
            else:
                legs.append(f"{'L' if l.side.value == 'long' else 'S'} {l.right.value[0].upper()} {float(l.strike):g}")
        sk = (c.skills_evidence.get("skills") or {})
        out: dict[str, Any] = {
            "id": c.candidate_id, "t": c.strategy_type.value, "u": c.underlying_symbol,
            "px": round(float(c.underlying_price), 2), "exp": c.expiration_date.isoformat(), "dte": c.dte,
            "qty": c.contracts_qty, "legs": legs, "prem": round(float(c.estimated_net_premium_total), 0),
            "maxP": None if c.max_profit_usd is None else round(float(c.max_profit_usd), 0),
            "maxL": round(float(c.max_loss_usd), 0), "coll": round(float(c.collateral_required_usd), 0),
            "be": [round(float(b), 2) for b in c.break_even_prices],
            "pop": round(c.probability_of_profit or 0.0, 2), "d": round(c.net_delta, 1),
            "th": round(c.net_theta, 1), "liq": round(c.liquidity_score, 2), "score": round(c.rules_score),
        }
        if sk.get("earnings_flag"):
            out["earn"] = True
        if sk.get("news_score") is not None:
            out["news"] = sk.get("news_score")
        warn = [w for w in c.warnings if w not in ("indicative_quotes", "fixture_data")]
        if warn:
            out["warn"] = warn[:3]
        if c.skills_evidence.get("wheel_state"):
            out["wheel"] = c.skills_evidence.get("wheel_state")
        return out

    def _build_user_prompt(self, account: OptionAccountSummary, pool: list[StrategyCandidate],
                           open_list: list[Any], recs: list[StrategyActionRecommendation],
                           skills: dict[str, Any], ctx: dict[str, Any]) -> tuple[str, list[str]]:
        prof = account.rule_profile
        equity = float(account.equity or 0.0) or 1.0
        acct = {
            "slug": account.slug, "eq": round(account.equity, 0), "free": round(account.free_cash, 0),
            "coll_pct": round(100.0 * float(account.cash_reserved) / equity, 1),
            "open": len(open_list), "max_open": prof.max_open_strategies,
            "risk_pct": float(prof.max_risk_per_trade_pct), "max_coll_pct": float(prof.max_collateral_pct),
            "scope": account.strategy_scope or "multi", "dd_pct": round(account.max_drawdown_pct, 1),
        }
        recs_by_gid = {r.position_group_id: r for r in recs}
        opens = []
        for g in open_list[:8]:
            gid = _attr(g, "id")
            r = recs_by_gid.get(gid)
            opens.append({
                "gid": gid, "t": _enum_val(_attr(g, "strategy_type")), "u": _attr(g, "underlying_symbol"),
                "dte": _attr(g, "dte"), "upnl": round(float(_attr(g, "unrealized_pnl", 0.0) or 0.0), 0),
                "maxL": round(float(_attr(g, "max_loss_usd", 0.0) or 0.0), 0),
                "rule": f"{r.action.value}:{r.reason_code}" if r else "HOLD",
                **({"mand": True} if r and r.mandatory else {}),
            })
        cands = [self._compact_candidate(c) for c in pool]
        payload = {
            "now": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
            "market": {k: ctx[k] for k in ("market_open", "feed_tier", "is_indicative") if k in ctx},
            "account": acct, "open": opens, "candidates": cands, "skills": skills,
        }
        return json.dumps(payload, separators=(",", ":"), ensure_ascii=False, default=str), [c["id"] for c in cands]

    # ------------------------------------------------------------ HTTP

    def _complete(self, system: str, user: str) -> tuple[str, Optional[int], Optional[int]]:
        assert self._creds is not None
        cfg = self.config
        json_mode, reasoning = True, "gpt-oss" in (self._creds.model or "")
        attempt = 0
        last: Optional[AdvisorError] = None
        headers = {"Content-Type": "application/json", "User-Agent": "quantara-options/0.1",
                   "Authorization": "Bearer " + self._creds.key}
        while attempt <= cfg.retries:
            body: dict[str, Any] = {
                "model": self._creds.model, "temperature": cfg.temperature, "max_tokens": cfg.max_output_tokens,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            }
            if json_mode:
                body["response_format"] = {"type": "json_object"}
            if reasoning:
                body["reasoning_effort"] = cfg.reasoning_effort
                body["reasoning_format"] = cfg.reasoning_format
            with self._lock:
                self.calls_today += 1
                self.calls_this_cycle += 1
            try:
                status, raw, resp_headers = self._transport(
                    self._creds.url, headers, json.dumps(body).encode("utf-8"), cfg.timeout_s)
            except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as exc:
                last = AdvisorError("network", self._redact(getattr(exc, "reason", exc)), retryable=True)
            else:
                if status == 200:
                    try:
                        msg = raw["choices"][0]["message"] or {}
                        text = msg.get("content") or ""
                        usage = raw.get("usage") or {}
                        tin, tout = usage.get("prompt_tokens"), usage.get("completion_tokens")
                    except (KeyError, IndexError, TypeError, AttributeError):
                        raise AdvisorError("bad_response", "unexpected response shape")
                    self._token_window.append((time.time(), int(tin or 0) + int(tout or 0)
                                               or self.estimate_tokens(system + user)))
                    return text, tin, tout
                detail = self._redact(json.dumps(raw)[:200]) if raw else ""
                if status == 400 and (json_mode or reasoning):
                    if json_mode:
                        json_mode = False
                    else:
                        reasoning = False
                    last = AdvisorError("http_400", detail or "bad request")
                    continue
                retry_after = None
                try:
                    ra = {k.lower(): v for k, v in (resp_headers or {}).items()}.get("retry-after")
                    retry_after = float(ra) if ra else None
                except (TypeError, ValueError):
                    retry_after = None
                if status == 429:
                    raise AdvisorError("http_429", detail or "rate limited", retry_after=retry_after)
                last = AdvisorError(f"http_{status}", detail or "error", retryable=status >= 500 or status == 408)
            if not last.retryable or attempt >= cfg.retries:
                break
            self._sleep(cfg.backoff_s * (2 ** attempt))
            attempt += 1
        raise last or AdvisorError("unknown", "no response")

    # ------------------------------------------------------------ grounding

    def _ground(self, account: OptionAccountSummary, parsed: dict[str, Any], pool: list[StrategyCandidate],
                open_list: list[Any], recs: list[StrategyActionRecommendation],
                ctx: dict[str, Any]) -> AdvisorResult:
        res = AdvisorResult(decided_by=DECIDED_BY_AI, mode=DecisionMode.AI)
        res.market_view_he = str(parsed.get("view_he") or "")[:300]
        res.market_view_en = str(parsed.get("view_en") or "")[:300]
        by_id = {c.candidate_id: c for c in pool}
        recs_by_gid = {r.position_group_id: r for r in recs}
        open_gids = {str(_attr(g, "id")) for g in open_list if _attr(g, "id")}
        max_new = self._max_new(account, open_list)
        handled_gids: set[str] = set()
        opened_syms: set[str] = set()
        n_open = 0
        cash_left = float(account.free_cash) * 0.95
        raw_decisions = parsed.get("decisions")
        if not isinstance(raw_decisions, list):
            raw_decisions = []

        for d in raw_decisions[:12]:
            if not isinstance(d, dict):
                continue
            try:
                action = AIAction(str(d.get("action", "")).upper())
            except ValueError:
                res.discarded.append(f"bad_action:{str(d.get('action'))[:20]}")
                continue
            conf = d.get("confidence")
            try:
                conf_f = float(conf)
            except (TypeError, ValueError):
                conf_f = 0.0
            he = str(d.get("rationale_he") or "")[:400]
            en = str(d.get("rationale_en") or "")[:400]
            cid = d.get("id") or d.get("candidate_id")
            gid = d.get("gid") or d.get("position_group_id")

            if action == AIAction.OPEN:
                c = by_id.get(str(cid)) if cid else None
                if c is None:
                    res.discarded.append(f"ungrounded_candidate:{str(cid)[:40]}")
                    continue
                if n_open >= max_new:
                    res.discarded.append(f"max_new_exceeded:{c.candidate_id}")
                    continue
                if conf_f < self.config.min_confidence_open:
                    res.discarded.append(f"low_confidence:{c.candidate_id}")
                    continue
                if c.underlying_symbol in opened_syms:
                    res.discarded.append(f"same_underlying_twice:{c.candidate_id}")
                    continue
                need = self._cash_need(c)
                if need > cash_left:
                    res.discarded.append(f"cash_budget:{c.candidate_id}")
                    continue
                try:
                    qty = int(d.get("qty") or c.contracts_qty)
                except (TypeError, ValueError):
                    qty = c.contracts_qty
                qty = max(1, min(qty, c.contracts_qty))
                res.decisions.append(self._record(
                    account, action=action, decided_by=DECIDED_BY_AI, ctx=ctx, candidate=c, qty=qty,
                    confidence=conf_f, he=he or c.rationale_summary, en=en, mode=DecisionMode.AI))
                n_open += 1
                opened_syms.add(c.underlying_symbol)
                cash_left -= need
            elif action in (AIAction.CLOSE, AIAction.ROLL, AIAction.HOLD):
                if not gid or str(gid) not in open_gids or str(gid) in handled_gids:
                    res.discarded.append(f"ungrounded_group:{str(gid)[:40]}")
                    continue
                r = recs_by_gid.get(str(gid))
                if r is None:
                    continue
                if r.mandatory:
                    continue
                if action == AIAction.ROLL and r.action != AIAction.ROLL:
                    action = AIAction.CLOSE if r.action == AIAction.CLOSE else AIAction.HOLD
                    en = f"[ROLL not rule-supported -> {action.value}] " + en
                if action == AIAction.HOLD and not self.config.emit_hold_records:
                    handled_gids.add(str(gid))
                    continue
                handled_gids.add(str(gid))
                res.decisions.append(self._record(
                    account, action=action, decided_by=DECIDED_BY_AI, ctx=ctx, rec=r,
                    confidence=conf_f, he=he or r.rationale_he, en=en or r.rationale_en, mode=DecisionMode.AI))
            elif action == AIAction.SKIP:
                c = by_id.get(str(cid)) if cid else None
                res.decisions.append(self._record(
                    account, action=AIAction.SKIP, decided_by=DECIDED_BY_AI, ctx=ctx, candidate=c,
                    confidence=conf_f, he=he or "דילוג — אין הזדמנות מספיק טובה.",
                    en=en or "Skip — nothing attractive enough.", mode=DecisionMode.AI))

        for r in recs:
            if r.mandatory and r.action in (AIAction.CLOSE, AIAction.ROLL):
                res.decisions.append(self._record(
                    account, action=r.action, decided_by=DECIDED_BY_RULES, ctx={**ctx, "risk_authority": True},
                    rec=r, confidence=0.95, he="[סמכות סיכון] " + r.rationale_he,
                    en="[risk authority] " + r.rationale_en, mode=DecisionMode.AI))
        return res
