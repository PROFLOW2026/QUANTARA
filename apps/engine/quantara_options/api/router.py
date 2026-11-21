"""QUANTARA Options FastAPI router (`/api/v1/options/...`).

Strictly isolated from equity System B routes/tables and System A's paper runner.
"""

from __future__ import annotations

import logging
import re
import threading
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from quantara_engine.api.deps import verify_api_key
from quantara_engine.core.config import settings
from quantara_engine.db.session import SessionLocal, get_session
from quantara_options.ai.groq_advisor import GroqOptionsAdvisor
from quantara_options.ai.risk_guard import OptionsRiskGuard
from quantara_options.api_schemas import (
    OptionsControlRequest,
    OptionsEngineStatusView,
    OptionsLabOverviewResponse,
    OptionsSimulateOrderRequest,
    OptionsStrategyGroupView,
)
from quantara_options.contracts import (
    DataFeedTier,
    DecisionMode,
    OptionChainResponse,
    OptionQuoteSnapshot,
)
from quantara_options.db.repository import OptionsRepository
from quantara_options.db.sim_adapter import PostgresOptionsSimAdapter
from quantara_options.market_data.alpaca_options import AlpacaOptionsProvider
from quantara_options.market_data.fixture_provider import FixtureOptionsProvider
from quantara_options.market_data.provider_base import WARN_SYNTHETIC_FIXTURE
from quantara_options.runtime.orchestrator import OrchestratorConfig, OptionsOrchestrator
from quantara_options.sim.accounting import to_decimal
from quantara_options.sim.engine import OptionsSimulationEngine
from quantara_options.strategies.scanner import OptionsOpportunityScanner, ScanContext

log = logging.getLogger(__name__)

_SYMBOL_RE = re.compile(r"^[A-Za-z]{1,8}([.-][A-Za-z]{1,2})?$")
_ADVISORY_LOCK_KEY = 902102026

router = APIRouter(
    prefix="/api/v1/options",
    dependencies=[Depends(verify_api_key)],
    tags=["options"],
)


def _validate_symbol(symbol: str) -> str:
    clean = (symbol or "").strip().upper()
    if not clean or not _SYMBOL_RE.match(clean):
        raise HTTPException(status_code=422, detail=f"Invalid symbol: {symbol!r}")
    return clean


_provider_lock = threading.Lock()
_cached_alpaca_key: Optional[tuple[str, str, str, str, str]] = None
_cached_alpaca_provider: Optional[AlpacaOptionsProvider] = None


def _make_alpaca_provider() -> AlpacaOptionsProvider:
    global _cached_alpaca_key, _cached_alpaca_provider
    key = (
        settings.alpaca_api_key_id or "",
        settings.alpaca_api_secret_key or "",
        settings.alpaca_paper_base_url or "https://paper-api.alpaca.markets",
        settings.alpaca_data_base_url or "https://data.alpaca.markets",
        settings.alpaca_data_feed or "iex",
    )
    with _provider_lock:
        if _cached_alpaca_provider is not None and _cached_alpaca_key == key:
            return _cached_alpaca_provider
        prov = AlpacaOptionsProvider(
            api_key_id=key[0],
            api_secret_key=key[1],
            paper_base_url=key[2],
            data_base_url=key[3],
            stock_feed=key[4],
            feed="indicative",
        )
        _cached_alpaca_key = key
        _cached_alpaca_provider = prov
        return prov


def _make_fixture_provider() -> FixtureOptionsProvider:
    return FixtureOptionsProvider(market_open=False)


def _build_orchestrator(
    session: Session,
    *,
    use_fixtures: bool = False,
) -> tuple[OptionsRepository, PostgresOptionsSimAdapter, OptionsOrchestrator]:
    repo = OptionsRepository(session, autocommit=False)
    repo.ensure_lab_seeded()
    session.commit()
    db_state = repo.get_engine_state()
    sim_adapter = PostgresOptionsSimAdapter(repo)
    provider = _make_fixture_provider() if use_fixtures else _make_alpaca_provider()
    orch = OptionsOrchestrator(
        repo=repo,
        sim_repo=sim_adapter,
        provider=provider,
        fixture_provider=_make_fixture_provider(),
        config=OrchestratorConfig(),
    )
    orch.enabled = bool(db_state.enabled)
    return repo, sim_adapter, orch


def run_options_cycle_in_session(
    session: Session,
    *,
    use_fixtures: bool = False,
    symbols: Optional[list[str]] = None,
    account_slug: Optional[str] = None,
) -> dict[str, Any]:
    """Execute one cycle inside an isolated DB session under a cross-process advisory lock."""
    got_lock = bool(
        session.execute(
            text("SELECT pg_try_advisory_lock(:k)"),
            {"k": _ADVISORY_LOCK_KEY},
        ).scalar()
    )
    if not got_lock:
        return {"skipped": True, "reason": "cycle_lock_held"}
    try:
        repo, _sim_adapter, orch = _build_orchestrator(session, use_fixtures=use_fixtures)
        clean_syms = [_validate_symbol(s) for s in symbols] if symbols else None
        result = orch.run_single_cycle(
            symbols=clean_syms,
            account_slug=account_slug,
            use_fixtures=use_fixtures,
        )
        session.commit()
        return result
    finally:
        try:
            session.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": _ADVISORY_LOCK_KEY})
            session.commit()
        except Exception:
            session.rollback()


class _BackgroundOptionsRunner:
    """Thread-safe controller that opens a fresh SessionLocal per cycle."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.cycle_active = False

    def start(
        self,
        *,
        interval_s: float = 300.0,
        use_fixtures: bool = False,
        symbols: Optional[list[str]] = None,
    ) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()

            def _loop() -> None:
                while not self._stop.is_set():
                    session = SessionLocal()
                    self.cycle_active = True
                    try:
                        repo = OptionsRepository(session, autocommit=False)
                        if not repo.get_engine_state().enabled:
                            break
                        run_options_cycle_in_session(
                            session,
                            use_fixtures=use_fixtures,
                            symbols=symbols,
                        )
                    except Exception:
                        log.exception("background options engine loop iteration failed")
                        try:
                            session.rollback()
                        except Exception:
                            pass
                    finally:
                        self.cycle_active = False
                        session.close()
                    if self._stop.wait(interval_s):
                        break

            self._thread = threading.Thread(
                target=_loop,
                name="quantara-options-api-runner",
                daemon=True,
            )
            self._thread.start()

    def stop(self, timeout_s: float = 2.0) -> None:
        with self._lock:
            self._stop.set()
            t = self._thread
            self._thread = None
        if t is not None and t.is_alive() and threading.current_thread() is not t:
            t.join(timeout=timeout_s)


_runner = _BackgroundOptionsRunner()


def _enriched_engine_status(
    repo: OptionsRepository,
    *,
    last_cycle_report: Optional[dict[str, Any]] = None,
) -> OptionsEngineStatusView:
    st = repo.get_engine_state()
    advisor_status = GroqOptionsAdvisor().status()
    groq_avail = bool(advisor_status.get("groq_available", False))
    mode = DecisionMode.AI if groq_avail else DecisionMode.RULES
    feed_tier = st.feed_tier
    is_indicative = st.is_indicative
    is_delayed = st.is_delayed_trades
    market_open = st.market_open
    readiness_market = st.readiness_market_data
    readiness_forward = st.readiness_forward_sim

    if last_cycle_report is not None and not last_cycle_report.get("skipped"):
        # Ensure fixture run_once reflects honest fixture status rather than stale DB row
        used_fix = bool(last_cycle_report.get("use_fixtures", False))
        if used_fix:
            feed_tier = DataFeedTier.FIXTURE
            is_indicative = False
            readiness_market = False
            readiness_forward = False

    return st.model_copy(
        update={
            "running_cycle": bool(st.running_cycle or _runner.cycle_active),
            "mode": mode,
            "feed_tier": feed_tier,
            "is_indicative": is_indicative,
            "is_delayed_trades": is_delayed,
            "market_open": market_open,
            "groq_available": groq_avail,
            "groq_model": str(advisor_status.get("model_name") or st.groq_model),
            "readiness_market_data": readiness_market,
            "readiness_forward_sim": readiness_forward,
        }
    )


def _cached_chain_preview(repo: OptionsRepository, symbol: str) -> Optional[OptionChainResponse]:
    snaps = repo.get_latest_chain_snapshots(symbol, window_minutes=1440)
    if not snaps:
        return None
    newest = max((s.snapshot_timestamp for s in snaps), default=datetime.now(timezone.utc))
    first = snaps[0]
    spot = float(first.underlying_price or 0)
    exps_all = sorted({s.contract.expiration_date for s in snaps})
    keep_exps = set(exps_all[:4])
    if spot > 0:
        filtered = [
            s for s in snaps
            if s.contract.expiration_date in keep_exps
            and abs(float(s.contract.strike) - spot) / spot <= 0.12
        ]
        if filtered:
            snaps = filtered
    else:
        snaps = [s for s in snaps if s.contract.expiration_date in keep_exps][:200]
    exps = sorted({s.contract.expiration_date for s in snaps})
    warnings: list[str] = []
    if first.feed_tier == DataFeedTier.FIXTURE:
        warnings.append(WARN_SYNTHETIC_FIXTURE)
    return OptionChainResponse(
        underlying_symbol=symbol,
        underlying_price=first.underlying_price,
        as_of=newest,
        expirations=exps,
        contracts=snaps,
        provider_name="fixture" if first.feed_tier == DataFeedTier.FIXTURE else "alpaca",
        feed_tier=first.feed_tier,
        is_indicative=first.is_indicative,
        is_delayed_trades=first.is_delayed_trades,
        market_open=False,
        warnings=warnings,
    )


def _build_quotes_map(
    repo: OptionsRepository,
    symbol: str,
    *,
    prefer_fixture: bool = False,
) -> tuple[Optional[OptionChainResponse], dict[str, OptionQuoteSnapshot], dict[str, Any]]:
    chain: Optional[OptionChainResponse] = None
    if prefer_fixture:
        chain = _make_fixture_provider().get_option_chain(symbol, min_dte=0, max_dte=365)
    else:
        live = _make_alpaca_provider().get_option_chain(symbol, min_dte=0, max_dte=365)
        if live.contracts:
            chain = live
        else:
            chain = _cached_chain_preview(repo, symbol) or live

    quotes: dict[str, OptionQuoteSnapshot] = {}
    spots: dict[str, Any] = {}
    if chain is not None:
        for q in chain.contracts:
            quotes[q.contract.occ_symbol] = q
        if chain.underlying_price > 0:
            spots[symbol] = chain.underlying_price
    return chain, quotes, spots


# --------------------------------------------------------------------------- routes


@router.get("/overview", response_model=OptionsLabOverviewResponse)
def get_options_overview(
    account_slug: Optional[str] = Query(default=None),
    symbol: Optional[str] = Query(default="SPY"),
    session: Session = Depends(get_session),
) -> OptionsLabOverviewResponse:
    sym = _validate_symbol(symbol or "SPY")
    repo = OptionsRepository(session, autocommit=False)
    repo.ensure_lab_seeded()
    session.commit()

    accounts = repo.list_accounts(active_only=True)
    selected = None
    if account_slug:
        selected = repo.get_account_summary(account_slug)
        if selected is None:
            raise HTTPException(status_code=404, detail=f"Options account not found: {account_slug}")
    elif accounts:
        selected = next((a for a in accounts if a.slug == "options-10k"), accounts[0])

    sel_slug = selected.slug if selected else None
    open_strats = repo.list_strategy_groups(sel_slug, status="open") if sel_slug else []
    closed_strats = repo.list_strategy_groups(sel_slug, status="closed") if sel_slug else []
    journal = repo.list_journal_events(sel_slug, limit=100) if sel_slug else []
    ai_decisions = repo.list_ai_decisions(sel_slug, limit=50)
    opportunities = repo.list_opportunities(limit=30)
    expirations = repo.get_expirations(sel_slug)
    comparison = repo.get_comparison_rows()
    chain_preview = _cached_chain_preview(repo, sym)
    engine_status = _enriched_engine_status(repo)

    return OptionsLabOverviewResponse(
        engine_status=engine_status,
        accounts=accounts,
        selected_account=selected,
        open_strategies=open_strats,
        closed_strategies=closed_strats,
        opportunities=opportunities,
        ai_decisions=ai_decisions,
        journal=journal,
        expirations=expirations,
        comparison=comparison,
        chain_preview=chain_preview,
    )


@router.get("/chain", response_model=OptionChainResponse)
def get_options_chain(
    symbol: str = Query(..., min_length=1, max_length=16),
    min_dte: int = Query(default=3, ge=0, le=365),
    max_dte: int = Query(default=60, ge=1, le=730),
    strike_range_pct: float = Query(default=0.15, ge=0.02, le=1.0),
    use_fixtures: bool = Query(default=False),
    session: Session = Depends(get_session),
) -> OptionChainResponse:
    sym = _validate_symbol(symbol)
    if min_dte > max_dte:
        raise HTTPException(status_code=422, detail="min_dte must be <= max_dte")

    if use_fixtures:
        return _make_fixture_provider().get_option_chain(
            sym, min_dte=min_dte, max_dte=max_dte, strike_range_pct=strike_range_pct
        )

    chain = _make_alpaca_provider().get_option_chain(
        sym, min_dte=min_dte, max_dte=max_dte, strike_range_pct=strike_range_pct
    )
    if chain.contracts:
        try:
            repo = OptionsRepository(session, autocommit=False)
            repo.save_chain_snapshots(chain.contracts)
            session.commit()
        except Exception:
            session.rollback()
            log.warning("failed saving chain snapshot for %s", sym, exc_info=True)
    return chain


@router.post("/control")
def control_options_engine(
    body: OptionsControlRequest,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    action = (body.action or "").strip().lower()
    if action not in ("start", "stop", "run_once"):
        raise HTTPException(status_code=422, detail=f"Unknown options engine action: {body.action!r}")

    repo = OptionsRepository(session, autocommit=False)
    repo.ensure_lab_seeded()
    session.commit()

    if action == "stop":
        _runner.stop()
        repo.set_engine_state({"enabled": False, "running_cycle": False})
        session.commit()
        return {"ok": True, "action": "stop", "engine_status": _enriched_engine_status(repo).model_dump(mode="json")}

    if action == "start":
        repo.set_engine_state({"enabled": True})
        session.commit()
        clean_syms = [_validate_symbol(s) for s in body.symbols] if body.symbols else None
        _runner.start(use_fixtures=body.use_fixtures, symbols=clean_syms)
        return {"ok": True, "action": "start", "engine_status": _enriched_engine_status(repo).model_dump(mode="json")}

    # run_once
    cycle_report = run_options_cycle_in_session(
        session,
        use_fixtures=body.use_fixtures,
        symbols=body.symbols,
        account_slug=body.account_slug,
    )
    return {
        "ok": not bool(cycle_report.get("errors")) or bool(cycle_report.get("chains_ok")),
        "action": "run_once",
        "cycle": cycle_report,
        "engine_status": _enriched_engine_status(repo, last_cycle_report=cycle_report).model_dump(mode="json"),
    }


@router.post("/simulate/open", response_model=OptionsStrategyGroupView)
def simulate_open_options(
    body: OptionsSimulateOrderRequest,
    session: Session = Depends(get_session),
) -> OptionsStrategyGroupView:
    repo = OptionsRepository(session, autocommit=False)
    summary = repo.get_account_summary(body.account_slug)
    if summary is None:
        raise HTTPException(status_code=404, detail=f"Options account not found: {body.account_slug}")

    cand = body.candidate
    if not cand.legs:
        raise HTTPException(status_code=422, detail="Candidate must include at least one leg")

    sym = _validate_symbol(cand.underlying_symbol)
    prefer_fixture = cand.feed_tier == DataFeedTier.FIXTURE
    chain, quotes_by_occ, _spots = _build_quotes_map(repo, sym, prefer_fixture=prefer_fixture)
    if chain is None or not quotes_by_occ:
        raise HTTPException(
            status_code=422,
            detail=f"No {cand.feed_tier.value} chain quotes available for {sym} to validate simulated open",
        )

    # Verify every option leg exists in the chain with matching spec (prevents invented contracts)
    for leg in cand.legs:
        if leg.occ_symbol:
            q = quotes_by_occ.get(leg.occ_symbol)
            if q is None:
                raise HTTPException(
                    status_code=422,
                    detail=f"Contract {leg.occ_symbol} does not exist in {chain.feed_tier.value} chain for {sym}",
                )
            if leg.strike is not None and to_decimal(q.contract.strike) != to_decimal(leg.strike):
                raise HTTPException(
                    status_code=422,
                    detail=f"Strike mismatch for {leg.occ_symbol}",
                )
            if leg.right is not None and q.contract.right != leg.right:
                raise HTTPException(
                    status_code=422,
                    detail=f"Right mismatch for {leg.occ_symbol}",
                )
            if leg.expiration_date is not None and q.contract.expiration_date != leg.expiration_date:
                raise HTTPException(
                    status_code=422,
                    detail=f"Expiration mismatch for {leg.occ_symbol}",
                )
            if q.contract.multiplier != leg.multiplier:
                raise HTTPException(
                    status_code=422,
                    detail=f"Multiplier mismatch for {leg.occ_symbol}",
                )

    sim_adapter = PostgresOptionsSimAdapter(repo)
    sim = OptionsSimulationEngine(sim_adapter)
    acct_state = sim_adapter.load_account_state(body.account_slug)
    open_views = [
        OptionsSimulationEngine.strategy_to_view(st, acct_state, with_payoff=False)
        for st in sim_adapter.list_open_strategies(body.account_slug)
    ]
    guard = OptionsRiskGuard().validate_open(
        cand,
        account=summary,
        chain=chain,
        quotes=quotes_by_occ,
        open_strategies=open_views,
    )
    if not guard.passed:
        raise HTTPException(status_code=422, detail="; ".join(guard.reasons))

    res = sim.open_strategy(
        body.account_slug,
        cand,
        quotes_by_occ=quotes_by_occ,
        metadata={
            "feed_tier": chain.feed_tier.value,
            "is_indicative": chain.is_indicative,
            "source": "manual_api",
        },
    )
    if not res.ok or res.strategy is None:
        session.rollback()
        raise HTTPException(status_code=422, detail="; ".join(res.reasons or ["Simulation open rejected"]))

    session.commit()
    refreshed = sim_adapter.load_account_state(body.account_slug)
    return OptionsSimulationEngine.strategy_to_view(res.strategy, refreshed)


@router.post("/positions/{strategy_id}/close", response_model=OptionsStrategyGroupView)
def close_options_position(
    strategy_id: str,
    session: Session = Depends(get_session),
) -> OptionsStrategyGroupView:
    repo = OptionsRepository(session, autocommit=False)
    sim_adapter = PostgresOptionsSimAdapter(repo)
    sim_st = sim_adapter.get_strategy(strategy_id)
    if sim_st is None:
        raise HTTPException(status_code=404, detail=f"Strategy {strategy_id} not found")
    if not sim_st.is_open:
        raise HTTPException(status_code=409, detail=f"Strategy {strategy_id} is already {sim_st.status.value}")

    prefer_fixture = sim_st.metadata.get("feed_tier") == DataFeedTier.FIXTURE.value
    _chain, quotes, spots = _build_quotes_map(
        repo,
        sim_st.underlying_symbol,
        prefer_fixture=prefer_fixture,
    )
    sim = OptionsSimulationEngine(sim_adapter)
    res = sim.close_strategy(
        sim_st.strategy_id,
        quotes,
        spots,
        reason="manual_close",
        allow_estimated_fills=True,
    )
    if not res.ok or res.strategy is None:
        session.rollback()
        raise HTTPException(status_code=422, detail="; ".join(res.reasons or ["Unable to close strategy"]))

    session.commit()
    refreshed = sim_adapter.load_account_state(sim_st.account_slug)
    return OptionsSimulationEngine.strategy_to_view(res.strategy, refreshed, with_payoff=False)


@router.post("/positions/{strategy_id}/roll", response_model=OptionsStrategyGroupView)
def roll_options_position(
    strategy_id: str,
    session: Session = Depends(get_session),
) -> OptionsStrategyGroupView:
    repo = OptionsRepository(session, autocommit=False)
    sim_adapter = PostgresOptionsSimAdapter(repo)
    sim_st = sim_adapter.get_strategy(strategy_id)
    if sim_st is None:
        raise HTTPException(status_code=404, detail=f"Strategy {strategy_id} not found")
    if not sim_st.is_open:
        raise HTTPException(status_code=409, detail=f"Strategy {strategy_id} is already {sim_st.status.value}")

    summary = repo.get_account_summary(sim_st.account_slug)
    if summary is None:
        raise HTTPException(status_code=404, detail=f"Options account not found: {sim_st.account_slug}")

    prefer_fixture = sim_st.metadata.get("feed_tier") == DataFeedTier.FIXTURE.value
    chain, quotes, spots = _build_quotes_map(
        repo,
        sim_st.underlying_symbol,
        prefer_fixture=prefer_fixture,
    )
    if chain is None or not chain.contracts:
        raise HTTPException(status_code=422, detail=f"No option chain available to roll {sim_st.underlying_symbol}")

    scanner = OptionsOpportunityScanner()
    cands = scanner.scan(
        chain,
        allowed_strategies=[sim_st.strategy_type],
        context=ScanContext(account_equity=summary.equity, allow_buy_write=True),
        max_candidates=20,
    )
    roll_cand = next(
        (c for c in cands if c.expiration_date > sim_st.expiration_date),
        None,
    )
    if roll_cand is None:
        raise HTTPException(status_code=422, detail="No later expiration candidate available for roll")

    acct_state = sim_adapter.load_account_state(sim_st.account_slug)
    open_views = [
        OptionsSimulationEngine.strategy_to_view(st, acct_state, with_payoff=False)
        for st in sim_adapter.list_open_strategies(sim_st.account_slug)
    ]
    guard = OptionsRiskGuard().validate_open(
        roll_cand,
        account=summary,
        chain=chain,
        quotes=quotes,
        open_strategies=open_views,
        is_roll=True,
        rolling_group_id=sim_st.strategy_id,
        released_collateral_usd=float(sim_st.collateral_locked),
    )
    if not guard.passed:
        raise HTTPException(status_code=422, detail="; ".join(guard.reasons))

    sim = OptionsSimulationEngine(sim_adapter)
    res = sim.roll_strategy(
        sim_st.strategy_id,
        roll_cand,
        quotes,
        spots,
        reason="manual_roll",
    )
    if not res.ok or res.new_strategy is None:
        session.rollback()
        raise HTTPException(status_code=422, detail="; ".join(res.reasons or ["Roll rejected"]))

    session.commit()
    refreshed = sim_adapter.load_account_state(sim_st.account_slug)
    return OptionsSimulationEngine.strategy_to_view(res.new_strategy, refreshed)
