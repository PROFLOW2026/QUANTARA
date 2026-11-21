"""End-to-end QUANTARA Options cycle: data -> mark/lifecycle -> close/roll -> scan -> decide -> guard -> fill.

Collaborators (all owned by other modules, used through their public APIs):
  - `repo`        : `quantara_options.db.repository.OptionsRepository` (accounts, snapshots,
                    opportunities, AI decisions, engine state). Any object exposing a subset of
                    those methods works; missing methods are skipped, never fatal.
  - `provider`    : `quantara_options.interfaces.OptionsDataProvider` (Alpaca or fixtures).
  - `sim_engine`  : `quantara_options.sim.OptionsSimulationEngine`. Resolved from `sim_engine`,
                    else `OptionsSimulationEngine(sim_repo)`, else `OptionsSimulationEngine(repo)`
                    when `repo` implements the `OptionsSimRepository` port. Without one the cycle
                    still scans, decides and audits, but executes nothing (`execution_unbound`).
"""

from __future__ import annotations

import logging
import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Callable, Iterable, Optional

from quantara_options.contracts import (
    AIAction,
    AIDecisionRecord,
    DataFeedTier,
    DecisionMode,
    LegRole,
    OptionAccountSummary,
    OptionChainResponse,
    OptionQuoteSnapshot,
    OptionSide,
    StrategyCandidate,
    StrategyType,
)
from quantara_options.ai.groq_advisor import (
    DECIDED_BY_AI,
    DECIDED_BY_RULES,
    AdvisorResult,
    GroqOptionsAdvisor,
)
from quantara_options.ai.risk_guard import OptionsRiskGuard, RiskCheckResult
from quantara_options.ai.skills_context import SkillsContext, SkillsContextProvider
from quantara_options.strategies.catalog import (
    STRATEGY_CATALOG,
    StrategyActionRecommendation,
    evaluate_open_strategy_actions,
    parse_strategy_scope,
)
from quantara_options.strategies.scanner import (
    OptionsOpportunityScanner,
    ScanContext,
    resize_candidate,
)

log = logging.getLogger("quantara_options.orchestrator")

DEFAULT_UNIVERSE: tuple[str, ...] = ("SPY", "QQQ", "IWM", "AAPL", "AMD", "TSLA", "F", "SOFI")
GLOBAL_SCAN_EQUITY = 10_000.0


def _env_universe() -> tuple[str, ...]:
    raw = os.environ.get("QUANTARA_OPTIONS_UNIVERSE", "").strip()
    if not raw:
        return DEFAULT_UNIVERSE
    syms = tuple(dict.fromkeys(s.strip().upper() for s in raw.split(",") if s.strip()))
    return syms or DEFAULT_UNIVERSE


@dataclass
class OrchestratorConfig:
    universe: tuple[str, ...] = field(default_factory=_env_universe)
    cycle_interval_s: float = 300.0
    min_dte: int = 5
    max_dte: int = 50
    strike_range_pct: float = 0.20
    trade_when_market_closed: bool = False
    save_top_opportunities: int = 40
    candidates_per_account: int = 30
    max_auto_qty: int = 5
    stale_after_s: float = 900.0
    skills_enabled: bool = True
    record_fixture_snapshots: bool = False


@dataclass
class _Market:
    chains: dict[str, OptionChainResponse] = field(default_factory=dict)
    quotes: dict[str, OptionQuoteSnapshot] = field(default_factory=dict)
    spots: dict[str, Decimal] = field(default_factory=dict)
    market_open: bool = False
    feed_tier: DataFeedTier = DataFeedTier.UNAVAILABLE
    is_indicative: bool = True
    is_delayed_trades: bool = True
    newest_as_of: Optional[datetime] = None
    errors: dict[str, str] = field(default_factory=dict)
    snapshots_saved: int = 0

    def context(self) -> dict[str, Any]:
        return {"market_open": self.market_open, "feed_tier": self.feed_tier.value,
                "is_indicative": self.is_indicative}


@dataclass
class CycleReport:
    cycle_id: str
    started_at: datetime
    finished_at: Optional[datetime] = None
    use_fixtures: bool = False
    symbols: list[str] = field(default_factory=list)
    market_open: bool = False
    feed_tier: str = DataFeedTier.UNAVAILABLE.value
    chains_ok: int = 0
    chains_failed: dict[str, str] = field(default_factory=dict)
    snapshots_saved: int = 0
    opportunities_saved: int = 0
    accounts: dict[str, dict[str, Any]] = field(default_factory=dict)
    decisions: int = 0
    executed: list[dict[str, Any]] = field(default_factory=list)
    blocked: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    skills: dict[str, Any] = field(default_factory=dict)
    groq: dict[str, Any] = field(default_factory=dict)
    execution_bound: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "cycle_id": self.cycle_id,
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "use_fixtures": self.use_fixtures,
            "symbols": self.symbols,
            "market_open": self.market_open,
            "feed_tier": self.feed_tier,
            "chains_ok": self.chains_ok,
            "chains_failed": self.chains_failed,
            "snapshots_saved": self.snapshots_saved,
            "opportunities_saved": self.opportunities_saved,
            "accounts": self.accounts,
            "decisions": self.decisions,
            "executed": self.executed,
            "blocked": self.blocked,
            "errors": self.errors,
            "skills": self.skills,
            "groq": self.groq,
            "execution_bound": self.execution_bound,
        }


def _ev(v: Any) -> Any:
    return getattr(v, "value", v)


def _attr(obj: Any, key: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


class OptionsOrchestrator:
    def __init__(
        self,
        repo: Any,
        provider: Any,
        advisor: Optional[GroqOptionsAdvisor] = None,
        risk_guard: Optional[OptionsRiskGuard] = None,
        *,
        sim_engine: Any = None,
        sim_repo: Any = None,
        scanner: Optional[OptionsOpportunityScanner] = None,
        skills: Optional[SkillsContextProvider] = None,
        fixture_provider: Any = None,
        config: Optional[OrchestratorConfig] = None,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self.repo = repo
        self.provider = provider
        self.config = config or OrchestratorConfig()
        self.skills = skills or SkillsContextProvider(enabled=None if self.config.skills_enabled else False)
        self.advisor = advisor or GroqOptionsAdvisor(system_a_status_reader=self.skills.system_a_ai_status)
        self.risk_guard = risk_guard or OptionsRiskGuard()
        self.scanner = scanner or OptionsOpportunityScanner()
        self.fixture_provider = fixture_provider
        self.clock = clock
        self.sim_repo = sim_repo
        self.sim = self._resolve_sim_engine(sim_engine, sim_repo)
        self._cycle_lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self.enabled = False
        self.running_cycle = False
        self.last_report: Optional[CycleReport] = None
        self.last_error: Optional[str] = None
        self.last_cycle_at: Optional[datetime] = None
        self._last_market: Optional[_Market] = None
        self._last_skills: Optional[SkillsContext] = None

    # ------------------------------------------------------------ wiring

    def _resolve_sim_engine(self, sim_engine: Any, sim_repo: Any) -> Any:
        if sim_engine is not None:
            if self.sim_repo is None:
                self.sim_repo = getattr(sim_engine, "repo", None)
            return sim_engine
        try:
            from quantara_options.sim import OptionsSimulationEngine, OptionsSimRepository
        except Exception as exc:
            log.warning("options simulation engine unavailable: %s", type(exc).__name__)
            return None
        target = sim_repo
        if target is None and isinstance(self.repo, OptionsSimRepository):
            target = self.repo
        if target is None and self.repo is not None and hasattr(self.repo, "session"):
            try:
                from quantara_options.db.repository import OptionsRepository
                from quantara_options.db.sim_adapter import PostgresOptionsSimAdapter

                if isinstance(self.repo, OptionsRepository):
                    target = PostgresOptionsSimAdapter(self.repo)
            except Exception:
                target = None
        if target is None:
            return None
        self.sim_repo = target
        return OptionsSimulationEngine(target)

    @property
    def execution_bound(self) -> bool:
        return self.sim is not None

    def _commit(self) -> None:
        session = getattr(self.repo, "session", None)
        if session is not None and not getattr(self.repo, "autocommit", False):
            try:
                session.commit()
            except Exception as exc:
                log.warning("options repo commit failed: %s", type(exc).__name__)
                self._rollback()

    def _rollback(self) -> None:
        session = getattr(self.repo, "session", None)
        if session is not None:
            try:
                session.rollback()
            except Exception:
                pass

    def _repo_call(self, name: str, *args: Any, **kwargs: Any) -> Any:
        fn = getattr(self.repo, name, None)
        if fn is None:
            return None
        try:
            return fn(*args, **kwargs)
        except Exception as exc:
            self._rollback()
            log.warning("options repo.%s failed: %s: %s", name, type(exc).__name__, str(exc)[:200])
            return None

    # ------------------------------------------------------------ lifecycle API

    def start_engine(self, *, interval_s: Optional[float] = None, use_fixtures: bool = False) -> dict[str, Any]:
        if self._thread is not None and self._thread.is_alive():
            return self.status()
        self.enabled = True
        self._stop.clear()
        interval = float(interval_s or self.config.cycle_interval_s)
        self._repo_call("set_engine_state", {"enabled": True})
        self._commit()

        def loop() -> None:
            while not self._stop.is_set():
                try:
                    self.run_single_cycle(use_fixtures=use_fixtures)
                except Exception as exc:
                    self.last_error = f"{type(exc).__name__}: {str(exc)[:200]}"
                    log.exception("options cycle crashed")
                self._stop.wait(interval)

        self._thread = threading.Thread(target=loop, name="quantara-options-engine", daemon=True)
        self._thread.start()
        return self.status()

    def stop_engine(self, *, wait_s: float = 5.0) -> dict[str, Any]:
        self.enabled = False
        self._stop.set()
        t = self._thread
        if t is not None and t.is_alive() and threading.current_thread() is not t:
            t.join(timeout=wait_s)
        self._thread = None
        self._repo_call("set_engine_state", {"enabled": False, "running_cycle": False})
        self._commit()
        return self.status()

    def status(self) -> dict[str, Any]:
        m = self._last_market
        fresh = None
        if m is not None and m.newest_as_of is not None:
            fresh = round((self.clock() - m.newest_as_of).total_seconds(), 1)
        groq = self.advisor.status()
        market_ready = bool(m and m.chains and m.feed_tier != DataFeedTier.UNAVAILABLE
                            and (fresh is None or fresh <= self.config.stale_after_s))
        return {
            "enabled": self.enabled,
            "running_cycle": self.running_cycle,
            "mode": DecisionMode.AI.value if groq["groq_available"] else DecisionMode.RULES.value,
            "feed_tier": (m.feed_tier.value if m else DataFeedTier.UNAVAILABLE.value),
            "is_indicative": m.is_indicative if m else True,
            "is_delayed_trades": m.is_delayed_trades if m else True,
            "market_open": m.market_open if m else False,
            "data_freshness_seconds": fresh,
            "last_cycle_at": self.last_cycle_at.isoformat() if self.last_cycle_at else None,
            "last_error": self.last_error,
            "groq_available": groq["groq_available"],
            "groq_model": groq["model_name"],
            "groq_calls_today": groq["calls_today"],
            "readiness_technical": self.last_error is None,
            "readiness_market_data": market_ready,
            "readiness_forward_sim": bool(market_ready and self.execution_bound
                                          and m is not None and m.feed_tier != DataFeedTier.FIXTURE),
            "execution_bound": self.execution_bound,
            "universe": list(self.config.universe),
        }

    # ------------------------------------------------------------ step 1: accounts

    def ensure_accounts(self) -> int:
        created = self._repo_call("ensure_lab_seeded") or 0
        self._commit()
        if self.sim_repo is not None and hasattr(self.sim_repo, "add_account") \
                and isinstance(getattr(self.sim_repo, "accounts", None), dict):
            try:
                from quantara_options.db.seed_lab import LAB_ACCOUNT_SEEDS
                from quantara_options.sim import new_account_state
            except Exception:
                return int(created)
            for seed in LAB_ACCOUNT_SEEDS:
                if seed.slug in self.sim_repo.accounts:
                    continue
                acct = new_account_state(seed.slug, seed.starting_capital, name=seed.name, mode=seed.mode,
                                         strategy_scope=seed.strategy_scope, rule_profile=seed.rule_profile)
                acct.experiment_group = seed.experiment_group
                self.sim_repo.add_account(acct)
                created += 1
        return int(created)

    def _active_accounts(self, only_slug: Optional[str] = None) -> list[OptionAccountSummary]:
        accounts: list[OptionAccountSummary] = []
        listed = self._repo_call("list_accounts", active_only=True)
        if listed:
            accounts = [a for a in listed if isinstance(a, OptionAccountSummary)]
        elif self.sim_repo is not None and isinstance(getattr(self.sim_repo, "accounts", None), dict):
            for slug in list(self.sim_repo.accounts):
                summ = self._sim_summary(slug)
                if summ is not None and summ.is_active:
                    accounts.append(summ)
        if only_slug:
            accounts = [a for a in accounts if a.slug == only_slug]
        if self.sim is not None:
            accounts = [self._sim_summary(a.slug) or a for a in accounts]
        return accounts

    def _sim_summary(self, slug: str) -> Optional[OptionAccountSummary]:
        if self.sim is None:
            return None
        try:
            return self.sim.get_account_summary(slug)
        except Exception:
            return None

    def _sim_state(self, slug: str) -> Any:
        if self.sim_repo is None:
            return None
        try:
            return self.sim_repo.load_account_state(slug)
        except Exception:
            return None

    # ------------------------------------------------------------ step 2: market data

    def _fetch_market(self, provider: Any, symbols: list[str], report: CycleReport) -> _Market:
        m = _Market()
        recorder = None
        if hasattr(self.repo, "save_chain_snapshots"):
            try:
                from quantara_options.market_data.snapshot_recorder import SnapshotRecorder
                recorder = SnapshotRecorder(self.repo, record_fixture=self.config.record_fixture_snapshots)
            except Exception:
                recorder = None
        tiers: list[DataFeedTier] = []
        fetched: dict[str, tuple[Optional[OptionChainResponse], Optional[str]]] = {}
        if len(symbols) > 1 and getattr(provider, "provider_name", "") == "alpaca":
            from concurrent.futures import ThreadPoolExecutor

            def _pull(sym: str) -> tuple[str, Optional[OptionChainResponse], Optional[str]]:
                try:
                    ch = provider.get_option_chain(
                        sym, min_dte=self.config.min_dte, max_dte=self.config.max_dte,
                        strike_range_pct=self.config.strike_range_pct,
                    )
                    if ch.underlying_price is None:
                        try:
                            spot, _open, _w = provider.get_underlying_price(sym)
                            if spot is not None:
                                ch = ch.model_copy(update={"underlying_price": spot})
                        except Exception:
                            pass
                    return sym, ch, None
                except Exception as exc:
                    return sym, None, f"{type(exc).__name__}: {str(exc)[:160]}"

            with ThreadPoolExecutor(max_workers=min(4, len(symbols))) as pool:
                for sym, ch, err in pool.map(_pull, symbols):
                    fetched[sym] = (ch, err)

        for sym in symbols:
            if sym in fetched:
                chain, err = fetched[sym]
                if err is not None or chain is None:
                    m.errors[sym] = err or "fetch_failed"
                    continue
            else:
                try:
                    chain = provider.get_option_chain(sym, min_dte=self.config.min_dte, max_dte=self.config.max_dte,
                                                      strike_range_pct=self.config.strike_range_pct)
                except Exception as exc:
                    m.errors[sym] = f"{type(exc).__name__}: {str(exc)[:160]}"
                    continue
                if chain.underlying_price is None:
                    try:
                        spot, is_open, _warn = provider.get_underlying_price(sym)
                        if spot is not None:
                            chain = chain.model_copy(update={"underlying_price": spot})
                    except Exception:
                        pass
            if chain.underlying_price is None or not chain.contracts:
                m.errors[sym] = "; ".join(chain.warnings[:2]) or "empty chain"
                if chain.underlying_price is not None:
                    m.spots[sym] = Decimal(chain.underlying_price)
                continue
            m.chains[sym] = chain
            m.spots[sym] = Decimal(chain.underlying_price)
            for q in chain.contracts:
                m.quotes[q.contract.occ_symbol] = q
            m.market_open = m.market_open or bool(chain.market_open)
            tiers.append(chain.feed_tier)
            if m.newest_as_of is None or chain.as_of > m.newest_as_of:
                m.newest_as_of = chain.as_of
            if recorder is not None:
                try:
                    m.snapshots_saved += recorder.record_chain(chain)
                except Exception:
                    pass
        if tiers:
            m.feed_tier = DataFeedTier.ALPACA_OPRA if all(t == DataFeedTier.ALPACA_OPRA for t in tiers) \
                else (DataFeedTier.FIXTURE if all(t == DataFeedTier.FIXTURE for t in tiers) else tiers[0])
            m.is_indicative = any(c.is_indicative for c in m.chains.values())
            m.is_delayed_trades = any(c.is_delayed_trades for c in m.chains.values())
        self._commit()
        report.chains_ok = len(m.chains)
        report.chains_failed = dict(m.errors)
        report.snapshots_saved = m.snapshots_saved
        report.market_open = m.market_open
        report.feed_tier = m.feed_tier.value
        return m

    def _fill_missing_quotes(self, provider: Any, m: _Market, open_views: Iterable[Any]) -> None:
        missing: dict[str, list[str]] = {}
        for v in open_views:
            for leg in _attr(v, "legs", []) or []:
                occ = _attr(leg, "occ_symbol")
                if occ and occ not in m.quotes and str(_attr(leg, "status", "open")).lower() == "open":
                    missing.setdefault(str(_attr(v, "underlying_symbol", "")), []).append(occ)
        for und, occs in missing.items():
            try:
                got = provider.get_quotes_for_occ(sorted(set(occs)), underlying_hint=und or None)
            except Exception:
                continue
            for occ, q in (got or {}).items():
                m.quotes[occ] = q
                if und and und not in m.spots and q.underlying_price:
                    m.spots[und] = Decimal(q.underlying_price)

    # ------------------------------------------------------------ open strategies

    def _open_views(self, slug: str) -> list[Any]:
        now = self.clock()
        if self.sim is not None and self.sim_repo is not None:
            try:
                state = self.sim_repo.load_account_state(slug)
                opens = self.sim_repo.list_open_strategies(slug)
                return [self.sim.strategy_to_view(st, state, now, with_payoff=False) for st in opens]
            except Exception as exc:
                log.warning("sim open views failed for %s: %s", slug, type(exc).__name__)
        views = self._repo_call("list_strategy_groups", slug, "open")
        return list(views or [])

    def _mark_and_lifecycle(self, slug: str, m: _Market, acct_report: dict[str, Any]) -> None:
        if self.sim is None:
            return
        now = self.clock()
        try:
            res = self.sim.mark_to_market(slug, m.quotes, m.spots, now)
            acct_report["auto_closed_breaches"] = [_attr(s, "strategy_id") for s in (res.affected_strategies or [])]
            if res.warnings:
                acct_report.setdefault("warnings", []).extend(res.warnings[:10])
        except Exception as exc:
            acct_report.setdefault("errors", []).append(f"mark_to_market: {type(exc).__name__}: {str(exc)[:160]}")
        try:
            res = self.sim.process_expirations_and_assignments(slug, m.quotes, m.spots, now)
            acct_report["lifecycle_affected"] = [_attr(s, "strategy_id") for s in (res.affected_strategies or [])]
        except Exception as exc:
            acct_report.setdefault("errors", []).append(f"lifecycle: {type(exc).__name__}: {str(exc)[:160]}")

    # ------------------------------------------------------------ account scan context

    def _scan_context(self, account: OptionAccountSummary, skills: Optional[SkillsContext]) -> ScanContext:
        shares: dict[str, int] = {}
        basis: dict[str, float] = {}
        state = self._sim_state(account.slug)
        if state is not None:
            for lot in getattr(state, "stock_lots", []) or []:
                if lot.shares <= 0:
                    continue
                shares[lot.symbol] = shares.get(lot.symbol, 0) + int(lot.unencumbered_shares)
                basis[lot.symbol] = float(lot.adjusted_cost_basis_per_share)
        else:
            for lot in self._repo_call("get_stock_lots", account.slug) or []:
                sym = str(lot.get("underlying_symbol") or lot.get("symbol") or "")
                n = int(lot.get("shares") or lot.get("quantity") or 0) - int(lot.get("encumbered_shares") or 0)
                if sym and n > 0:
                    shares[sym] = shares.get(sym, 0) + n
                    b = lot.get("adjusted_cost_basis") or lot.get("avg_cost_basis")
                    if b is not None:
                        basis[sym] = float(b)
        wheel = dict(getattr(state, "wheel_states", None) or account.wheel_states or {})
        bias: dict[str, float] = {}
        evidence: dict[str, dict[str, Any]] = {}
        if skills is not None:
            for sym in self.config.universe:
                bias[sym] = skills.bias_for(sym)
                ev = skills.evidence_for(sym)
                if ev:
                    evidence[sym] = ev
        return ScanContext(
            account_equity=float(account.equity or account.starting_capital),
            free_cash=float(account.free_cash),
            approval_level=int(account.rule_profile.options_approval_level),
            shares_held=shares, share_cost_basis=basis, wheel_states=wheel,
            market_bias=bias, skills_evidence=evidence,
        )

    def _allowed_for(self, account: OptionAccountSummary) -> list[StrategyType]:
        try:
            from quantara_options.db.seed_lab import strategy_types_for_scope
            allowed = strategy_types_for_scope(account.strategy_scope)
            if allowed:
                return [StrategyType(t) for t in sorted(allowed)]
        except Exception:
            pass
        return parse_strategy_scope(account.strategy_scope)

    def _size(self, cands: list[StrategyCandidate], account: OptionAccountSummary) -> list[StrategyCandidate]:
        out = []
        for c in cands:
            qty = self.risk_guard.suggest_contracts_qty(c, account, max_qty=self.config.max_auto_qty)
            if qty < 1:
                continue
            out.append(c if qty == c.contracts_qty else resize_candidate(c, qty))
        return out

    # ------------------------------------------------------------ roll search

    def _find_roll_candidate(self, view: Any, rec: StrategyActionRecommendation, m: _Market,
                             account: OptionAccountSummary, ctx: ScanContext) -> Optional[StrategyCandidate]:
        sym = str(_attr(view, "underlying_symbol", ""))
        chain = m.chains.get(sym)
        if chain is None:
            return None
        st = StrategyType(_ev(_attr(view, "strategy_type")))
        hint = rec.roll_hint or {}
        old_exp = _attr(view, "expiration_date")
        legs = [l for l in (_attr(view, "legs", []) or []) if _attr(l, "occ_symbol")
                and str(_attr(l, "status", "open")).lower() == "open"]
        old_short = {str(_ev(_attr(l, "right"))): float(_attr(l, "strike") or 0) for l in legs
                     if str(_ev(_attr(l, "side"))) == "short"}
        qty = int(_attr(view, "contracts_qty", 1) or 1)
        close_cash = 0.0
        for l in legs:
            q = m.quotes.get(_attr(l, "occ_symbol"))
            mark = float(q.mid) if q is not None and q.mid is not None else float(_attr(l, "current_mark", 0) or 0)
            sign = 1.0 if str(_ev(_attr(l, "side"))) == "long" else -1.0
            close_cash += sign * mark * int(_attr(l, "multiplier", 100) or 100) * int(_attr(l, "quantity", 1) or 1)

        roll_ctx = ScanContext(**{**ctx.__dict__})
        if st == StrategyType.WHEEL:
            roll_ctx.wheel_states = dict(ctx.wheel_states)
            roll_ctx.wheel_states[sym] = "holding_shares" if "call" in old_short else "idle"
            if "call" in old_short:
                roll_ctx.shares_held = dict(ctx.shares_held)
                roll_ctx.shares_held[sym] = max(ctx.shares_held.get(sym, 0), qty * 100)
        if st == StrategyType.COVERED_CALL:
            roll_ctx.shares_held = dict(ctx.shares_held)
            roll_ctx.shares_held[sym] = max(ctx.shares_held.get(sym, 0), qty * 100)
            roll_ctx.allow_buy_write = False
        cands = self.scanner.scan(chain, allowed_strategies=[st], context=roll_ctx)
        best: Optional[StrategyCandidate] = None
        for c in cands:
            if old_exp is not None:
                if hint.get("keep_expiration"):
                    if c.expiration_date < old_exp:
                        continue
                elif c.expiration_date <= old_exp:
                    continue
            new_short = {l.right.value: float(l.strike) for l in c.legs
                         if l.side == OptionSide.SHORT and l.right is not None and l.strike is not None}
            style = hint.get("style")
            if style == "up_and_out" and "call" in old_short and new_short.get("call", 0) < old_short["call"]:
                continue
            if style == "down_and_out" and "put" in old_short and new_short.get("put", 1e12) > old_short["put"]:
                continue
            c2 = resize_candidate(c, qty) if c.contracts_qty != qty else c
            if hint.get("require_net_credit") and float(c2.estimated_net_premium_total) + close_cash <= 0:
                stock_leg = any(l.leg_role == LegRole.UNDERLYING_STOCK for l in c2.legs)
                if not stock_leg:
                    continue
            if best is None or c2.rules_score > best.rules_score:
                best = c2
        return best

    # ------------------------------------------------------------ decision execution

    def _record(self, decision: AIDecisionRecord, guard: Optional[RiskCheckResult],
                extra_reasons: list[str], execution: dict[str, Any]) -> AIDecisionRecord:
        rec = decision.model_copy(deep=True)
        if guard is not None:
            rec.risk_guard_passed = guard.passed and not any(r.startswith("sim:") for r in extra_reasons)
            rec.risk_guard_reasons = list(guard.reasons) + extra_reasons
            rec.market_context = {**rec.market_context, "risk_guard": guard.as_dict()}
        else:
            rec.risk_guard_passed = not extra_reasons
            rec.risk_guard_reasons = list(extra_reasons)
        rec.market_context = {**rec.market_context, "execution": execution}
        self._repo_call("record_ai_decision", rec)
        if self.sim_repo is not None and self.sim_repo is not self.repo and hasattr(self.sim_repo, "record_ai_decision"):
            try:
                self.sim_repo.record_ai_decision(rec)
            except Exception:
                pass
        return rec

    def _execute(self, account: OptionAccountSummary, decision: AIDecisionRecord, *,
                 cand_by_id: dict[str, StrategyCandidate], views_by_id: dict[str, Any],
                 recs_by_id: dict[str, StrategyActionRecommendation], m: _Market, ctx: ScanContext,
                 known_ids: set[str], report: CycleReport, allow_open: bool) -> AIDecisionRecord:
        now = self.clock()
        slug = account.slug
        state = self._sim_state(slug)
        daily_pnl = None
        peak = None
        if state is not None:
            if getattr(state, "day_start_equity", None):
                daily_pnl = float(account.equity) - float(state.day_start_equity)
            peak = float(getattr(state, "peak_equity", 0) or 0) or None
        open_views = list(views_by_id.values())
        execution: dict[str, Any] = {"executed": False}

        if decision.action == AIAction.OPEN:
            cand = cand_by_id.get(decision.candidate_id or "")
            if cand is None:
                return self._record(decision, None, ["ungrounded_candidate: not in this cycle's scan"], execution)
            if decision.contracts_qty and decision.contracts_qty != cand.contracts_qty:
                cand = resize_candidate(cand, decision.contracts_qty)
            fit = self.risk_guard.suggest_contracts_qty(cand, account, max_qty=self.config.max_auto_qty)
            if 1 <= fit < cand.contracts_qty:
                cand = resize_candidate(cand, fit)
            guard = self.risk_guard.validate_open(
                cand, account=account, chain=m.chains.get(cand.underlying_symbol), quotes=m.quotes,
                known_candidate_ids=known_ids, open_strategies=open_views, daily_pnl_usd=daily_pnl,
                peak_equity_usd=peak, now=now)
            extra: list[str] = []
            if guard.passed and not allow_open:
                extra.append("market_closed: new opens disabled while market is closed")
            elif guard.passed and self.sim is None:
                extra.append("execution_unbound: no simulation engine configured")
            elif guard.passed:
                res = self.sim.open_strategy(
                    slug, cand, ai_rationale=decision.rationale_he or decision.rationale_en, now=now,
                    quotes_by_occ=m.quotes,
                    metadata={"decision_id": decision.decision_id, "decided_by": decision.decided_by})
                if res.ok:
                    sid = _attr(res.strategy, "strategy_id")
                    execution = {"executed": True, "strategy_id": sid, "warnings": res.warnings[:8]}
                    report.executed.append({"account": slug, "action": "OPEN", "strategy_id": sid,
                                            "candidate_id": cand.candidate_id,
                                            "strategy_type": cand.strategy_type.value})
                else:
                    extra.extend(f"sim: {r}" for r in res.reasons[:6])
            if not guard.passed or extra:
                report.blocked.append({"account": slug, "action": "OPEN", "candidate_id": cand.candidate_id,
                                       "reasons": (guard.reasons + extra)[:6]})
            return self._record(decision, guard, extra, execution)

        if decision.action in (AIAction.CLOSE, AIAction.ROLL):
            gid = decision.position_group_id or ""
            view = views_by_id.get(gid)
            rec = recs_by_id.get(gid)
            if view is None:
                return self._record(decision, None, [f"unknown_group: {gid}"], execution)
            mandatory = bool(rec and rec.mandatory)
            day_trades = 0
            if state is not None:
                try:
                    from quantara_options.sim.margin_and_collateral import count_recent_day_trades
                    from quantara_options.sim.lifecycle import market_date
                    day_trades = count_recent_day_trades(state.day_trade_dates, market_date(now))
                except Exception:
                    day_trades = len(getattr(state, "day_trade_dates", []) or [])
            close_guard = self.risk_guard.validate_close(view, account=account, quotes=m.quotes,
                                                         day_trades_last_5d=day_trades, mandatory=mandatory,
                                                         now=now)
            if not close_guard.passed:
                report.blocked.append({"account": slug, "action": decision.action.value, "group": gid,
                                       "reasons": close_guard.reasons[:6]})
                return self._record(decision, close_guard, [], execution)
            if self.sim is None:
                return self._record(decision, close_guard, ["execution_unbound: no simulation engine configured"],
                                    execution)
            reason = f"{decision.decided_by}:{rec.reason_code if rec else decision.action.value.lower()}"

            if decision.action == AIAction.ROLL:
                new = self._find_roll_candidate(view, rec, m, account, ctx) if rec else None
                if new is None:
                    fallback = (rec.roll_hint or {}).get("fallback") if rec else None
                    if fallback == "accept_assignment":
                        execution = {"executed": False, "roll_fallback": "accept_assignment"}
                        return self._record(decision, close_guard, ["roll_unavailable: holding for assignment"],
                                            execution)
                    res = self.sim.close_strategy(gid, m.quotes, m.spots, reason=reason + ":no_roll_target",
                                                  now=now)
                    if res.ok:
                        execution = {"executed": True, "action": "CLOSE", "roll_fallback": "close"}
                        report.executed.append({"account": slug, "action": "CLOSE", "group": gid,
                                                "note": "roll target unavailable"})
                        return self._record(decision, close_guard, [], execution)
                    return self._record(decision, close_guard, [f"sim: {r}" for r in res.reasons[:6]], execution)
                released = float(_attr(view, "collateral_locked_usd", 0.0) or 0.0)
                guard = self.risk_guard.validate_open(
                    new, account=account, chain=m.chains.get(new.underlying_symbol), quotes=m.quotes,
                    open_strategies=list(views_by_id.values()), daily_pnl_usd=daily_pnl, peak_equity_usd=peak,
                    is_roll=True, rolling_group_id=gid, released_collateral_usd=released, now=now)
                if not guard.passed:
                    report.blocked.append({"account": slug, "action": "ROLL", "group": gid,
                                           "reasons": guard.reasons[:6]})
                    return self._record(decision, guard, [], {"executed": False, "roll_candidate": new.candidate_id})
                res = self.sim.roll_strategy(gid, new, m.quotes, m.spots, reason=reason, now=now)
                if res.ok:
                    new_id = _attr(res.new_strategy, "strategy_id")
                    execution = {"executed": True, "rolled_to": new_id, "roll_candidate": new.candidate_id}
                    report.executed.append({"account": slug, "action": "ROLL", "group": gid, "new_strategy": new_id})
                    return self._record(decision, guard, [], execution)
                return self._record(decision, guard, [f"sim: {r}" for r in res.reasons[:6]],
                                    {"executed": False, "roll_candidate": new.candidate_id})

            res = self.sim.close_strategy(gid, m.quotes, m.spots, reason=reason, now=now)
            if res.ok:
                execution = {"executed": True, "action": "CLOSE"}
                report.executed.append({"account": slug, "action": "CLOSE", "group": gid})
                return self._record(decision, close_guard, [], execution)
            report.blocked.append({"account": slug, "action": "CLOSE", "group": gid, "reasons": res.reasons[:6]})
            return self._record(decision, close_guard, [f"sim: {r}" for r in res.reasons[:6]], execution)

        return self._record(decision, None, [], execution)

    # ------------------------------------------------------------ the cycle

    def run_single_cycle(
        self,
        *,
        symbols: Optional[list[str]] = None,
        account_slug: Optional[str] = None,
        use_fixtures: bool = False,
    ) -> dict[str, Any]:
        if not self._cycle_lock.acquire(blocking=False):
            return {"skipped": True, "reason": "cycle_already_running", "status": self.status()}
        report = CycleReport(cycle_id=f"cyc_{uuid.uuid4().hex[:12]}", started_at=self.clock(),
                             use_fixtures=use_fixtures, execution_bound=self.execution_bound)
        self.running_cycle = True
        try:
            self._repo_call("set_engine_state", {"running_cycle": True})
            self._commit()
            self._run(report, symbols=symbols, account_slug=account_slug, use_fixtures=use_fixtures)
            self.last_error = report.errors[0] if report.errors and report.chains_ok == 0 else None
        except Exception as exc:
            self._rollback()
            self.last_error = f"{type(exc).__name__}: {str(exc)[:200]}"
            report.errors.append(self.last_error)
            log.exception("options cycle failed")
        finally:
            report.finished_at = self.clock()
            self.last_cycle_at = report.finished_at
            self.running_cycle = False
            report.groq = self.advisor.status()
            self.last_report = report
            self._persist_engine_state(report)
            self._cycle_lock.release()
        return report.as_dict()

    def _provider_for(self, use_fixtures: bool) -> Any:
        if not use_fixtures:
            return self.provider
        if self.fixture_provider is None and getattr(self.provider, "provider_name", "") == "fixture":
            self.fixture_provider = self.provider
        if self.fixture_provider is None:
            try:
                from quantara_options.market_data import FixtureOptionsProvider
                self.fixture_provider = FixtureOptionsProvider()
            except Exception:
                return self.provider
        return self.fixture_provider

    def _run(self, report: CycleReport, *, symbols: Optional[list[str]], account_slug: Optional[str],
             use_fixtures: bool) -> None:
        self.advisor.begin_cycle()
        provider = self._provider_for(use_fixtures)
        syms = [s.upper() for s in (symbols or list(self.config.universe))]
        report.symbols = syms

        # 1) accounts
        self.ensure_accounts()
        accounts = self._active_accounts(account_slug)

        # 2) market data (+ snapshots)
        m = self._fetch_market(provider, syms, report)
        self._last_market = m
        if not m.chains:
            report.errors.append("no_market_data: " + "; ".join(f"{k}: {v}" for k, v in list(m.errors.items())[:3]))

        # skills context (time-boxed, never fatal)
        skills: Optional[SkillsContext] = None
        try:
            skills = self.skills.build(syms) if self.config.skills_enabled else None
        except Exception as exc:
            report.errors.append(f"skills: {type(exc).__name__}")
        self._last_skills = skills
        if skills is not None:
            report.skills = {"ok": skills.sources_ok, "source": skills.source, "macro": skills.macro_sentiment,
                             "notes": skills.notes[:3]}

        # 3) + 4) mark-to-market / lifecycle, then rule triggers, per account
        per_account: dict[str, dict[str, Any]] = {}
        for acct in accounts:
            ar: dict[str, Any] = {"mode": acct.mode.value, "scope": acct.strategy_scope}
            report.accounts[acct.slug] = ar
            try:
                views = self._open_views(acct.slug)
                self._fill_missing_quotes(provider, m, views)
                self._mark_and_lifecycle(acct.slug, m, ar)
                self._commit()
                views = self._open_views(acct.slug)
                recs = []
                for v in views:
                    sym = str(_attr(v, "underlying_symbol", "")).upper()
                    px = m.spots.get(sym)
                    recs.append(evaluate_open_strategy_actions(
                        v, m.quotes, self.clock(), spot=float(px) if px is not None else None))
                ar["open"] = len(views)
                ar["signals"] = {r.position_group_id: f"{r.action.value}:{r.reason_code}" for r in recs
                                 if r.action != AIAction.HOLD}
                per_account[acct.slug] = {"views": views, "recs": recs}
            except Exception as exc:
                self._rollback()
                ar.setdefault("errors", []).append(f"prepare: {type(exc).__name__}: {str(exc)[:160]}")

        # 5) global opportunity scan + persist
        if m.chains:
            global_ctx = ScanContext(account_equity=GLOBAL_SCAN_EQUITY,
                                     market_bias={s: skills.bias_for(s) for s in syms} if skills else {},
                                     skills_evidence={s: skills.evidence_for(s) for s in syms
                                                      if skills and skills.evidence_for(s)},
                                     allow_buy_write=True)
            opps = self.scanner.scan_universe(m.chains, context=global_ctx,
                                              max_candidates=self.config.save_top_opportunities)
            if not opps and not m.market_open:
                # Weekend/after-hours: allow previewing Monday opportunities without enabling opens
                from quantara_options.strategies.scanner import OptionsOpportunityScanner, ScannerConfig
                preview_cfg = ScannerConfig(allow_stale_quotes=True)
                opps = OptionsOpportunityScanner(preview_cfg).scan_universe(
                    m.chains, context=global_ctx, max_candidates=self.config.save_top_opportunities
                )
            saved = self._repo_call("save_opportunities", opps)
            report.opportunities_saved = int(saved if isinstance(saved, int) else len(opps))
            self._commit()

        allow_open = bool(m.chains) and (m.market_open or use_fixtures or self.config.trade_when_market_closed
                                         or m.feed_tier == DataFeedTier.FIXTURE)
        skills_prompt = skills.compact_for_prompt(syms) if skills else {}

        # 6) decide -> guard -> execute, per account
        for acct in accounts:
            ar = report.accounts.get(acct.slug, {})
            prep = per_account.get(acct.slug)
            if prep is None:
                continue
            try:
                account = self._sim_summary(acct.slug) or acct
                ctx = self._scan_context(account, skills)
                allowed = self._allowed_for(account)
                cands = self.scanner.scan_universe(m.chains, allowed_strategies=allowed, context=ctx,
                                                   max_candidates=self.config.candidates_per_account) \
                    if (m.chains and allow_open) else []
                cands = self._size(cands, account)
                cand_by_id = {c.candidate_id: c for c in cands}
                views_by_id = {str(_attr(v, "id")): v for v in prep["views"]}
                recs_by_id = {r.position_group_id: r for r in prep["recs"]}
                result: AdvisorResult = self.advisor.decide(
                    account=account, candidates=cands, open_strategies=prep["views"],
                    open_recommendations=prep["recs"], skills_context=skills_prompt, mode=account.mode,
                    market_context={**m.context(), "cycle_id": report.cycle_id})
                ar.update({"candidates": len(cands), "decided_by": result.decided_by, "ai_called": result.ai_called,
                           "fallback_reason": result.fallback_reason, "discarded": result.discarded[:6]})
                ordered = sorted(result.decisions, key=lambda d: {AIAction.CLOSE: 0, AIAction.ROLL: 1,
                                                                  AIAction.OPEN: 2}.get(d.action, 3))
                outcomes = []
                for d in ordered:
                    account = self._sim_summary(acct.slug) or account
                    views_by_id = {str(_attr(v, "id")): v for v in self._open_views(acct.slug)} \
                        if self.sim is not None else views_by_id
                    if d.action in (AIAction.CLOSE, AIAction.ROLL) and d.position_group_id not in views_by_id:
                        continue
                    saved = self._execute(account, d, cand_by_id=cand_by_id, views_by_id=views_by_id,
                                          recs_by_id=recs_by_id, m=m, ctx=ctx, known_ids=set(cand_by_id),
                                          report=report, allow_open=allow_open)
                    outcomes.append({"action": saved.action.value, "candidate_id": saved.candidate_id,
                                     "group": saved.position_group_id, "passed": saved.risk_guard_passed,
                                     "executed": saved.market_context.get("execution", {}).get("executed", False)})
                    self._commit()
                report.decisions += len(outcomes)
                ar["decisions"] = outcomes
                if result.ai_called:
                    self._repo_call("set_engine_state", {"groq_calls_increment": 1})
                    self._commit()
            except Exception as exc:
                self._rollback()
                ar.setdefault("errors", []).append(f"decide: {type(exc).__name__}: {str(exc)[:160]}")
                log.exception("options decision step failed for %s", acct.slug)

    # ------------------------------------------------------------ step 7: engine state

    def _persist_engine_state(self, report: CycleReport) -> None:
        st = self.status()
        patch = {
            "enabled": self.enabled,
            "running_cycle": False,
            "mode": st["mode"],
            "feed_tier": st["feed_tier"],
            "is_indicative": st["is_indicative"],
            "is_delayed_trades": st["is_delayed_trades"],
            "market_open": st["market_open"],
            "data_freshness_seconds": st["data_freshness_seconds"],
            "last_cycle_at": report.finished_at,
            "last_error": self.last_error,
            "groq_available": st["groq_available"],
            "groq_model": st["groq_model"],
            "universe": report.symbols or list(self.config.universe),
        }
        self._repo_call("set_engine_state", patch)
        self._commit()
