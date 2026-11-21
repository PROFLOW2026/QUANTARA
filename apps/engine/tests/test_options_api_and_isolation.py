"""QUANTARA Options - HTTP API, Alpaca read-only adapter parsing and System A/B isolation.

HONESTY NOTE
------------
* ``FixtureOptionsProvider`` / ``StaticChainProvider`` data is SYNTHETIC (``feed_tier=fixture``,
  ``is_indicative=False``).  Tests that pass with it prove API / sim / guard / accounting logic only -
  they NEVER prove that live Alpaca OPRA data is available.
* The Alpaca adapter is exercised against an in-process mock transport (no network, no real keys).
  A mocked ``200`` for ``feed=opra`` proves the adapter's tier-labelling logic, not OPRA entitlement.
* Isolation: options code must never touch System B (equity broker tables) nor System A
  (``QUANTARA-AI-Trader/trader/paper-state.json``) and must never call any trading/order endpoint.
"""

from __future__ import annotations

import ast
import json
import re
import urllib.parse
from datetime import timezone
from decimal import Decimal
from pathlib import Path

import pytest

from quantara_options.contracts import (
    DataFeedTier,
    OptionChainResponse,
    OptionRight,
    OptionSide,
    StrategyType,
)
from quantara_options.market_data import alpaca_options as alpaca_mod
from quantara_options.market_data.alpaca_options import AlpacaOptionsProvider, _assert_read_only_path
from quantara_options.market_data.fixture_provider import FixtureOptionsProvider
from quantara_options.market_data.provider_base import (
    WARN_CREDENTIALS_MISSING,
    WARN_INDICATIVE_FEED,
    WARN_LOCAL_GREEKS,
    WARN_NONSTANDARD_MULTIPLIER,
    WARN_SYNTHETIC_FIXTURE,
    TokenBucket,
)
from quantara_options.strategies.scanner import OptionsOpportunityScanner, ScanContext
from tests.fixtures.options_fixtures import (
    AS_OF,
    EXP_21,
    FIXTURE_WARNING,
    OPTQA_PREFIX,
    build_chain,
    candidate_for,
    f,
    options_lab,  # noqa: F401  (pytest fixture)
    pg_insert_qa_account,
    pg_purge_qa_rows,
    pg_table_exists,
    occ_symbol,
    rule_profile,
)

C, P = OptionRight.CALL, OptionRight.PUT
SPY = "SPY"
ENGINE_ROOT = Path(__file__).resolve().parents[1]
QUANTARA_OPTIONS_DIR = ENGINE_ROOT / "quantara_options"
REPO_ROOT = ENGINE_ROOT.parents[1]
SYSTEM_A_PAPER_STATE = REPO_ROOT.parent / "QUANTARA-AI-Trader" / "trader" / "paper-state.json"
FAKE_ALPACA_KEY, FAKE_ALPACA_SECRET = "PKUNITTESTKEYID123456", "unit-test-secret-never-real-0000000000"


# ======================================================================================
# FixtureOptionsProvider (synthetic - phrased so it can never be mistaken for live data)
# ======================================================================================


class TestFixtureProviderIsClearlySynthetic:
    def _provider(self, **kw):
        return FixtureOptionsProvider(as_of=AS_OF.astimezone(timezone.utc), market_open=True, **kw)

    def test_default_construction_and_tagging(self):
        prov = FixtureOptionsProvider()  # default construction must work with no keys / network
        chain = prov.get_option_chain(SPY, as_of=AS_OF.astimezone(timezone.utc))
        assert chain.provider_name == "fixture"
        assert chain.feed_tier == DataFeedTier.FIXTURE and chain.is_indicative is False
        assert WARN_SYNTHETIC_FIXTURE == FIXTURE_WARNING in chain.warnings
        assert chain.contracts and all(q.feed_tier == DataFeedTier.FIXTURE and not q.is_indicative
                                       for q in chain.contracts)
        health = prov.get_provider_health()
        assert health["feed_tier"] == "fixture" and health["credentials_configured"] is False
        assert WARN_SYNTHETIC_FIXTURE in health["warnings"]
        assert health["feed_tier"] != DataFeedTier.ALPACA_OPRA.value

    def test_chain_is_deterministic_and_has_full_ladder_quotes(self):
        a = self._provider().get_option_chain(SPY)
        b = self._provider().get_option_chain(SPY)
        assert [q.model_dump(exclude={"snapshot_timestamp"}) for q in a.contracts] == \
               [q.model_dump(exclude={"snapshot_timestamp"}) for q in b.contracts]
        assert len(a.expirations) >= 2 and len(a.contracts) > 40
        sample = a.contracts[len(a.contracts) // 2]
        assert sample.bid is not None and sample.ask is not None and sample.ask >= sample.bid
        assert sample.implied_volatility and sample.greeks.delta is not None
        assert sample.open_interest >= 0 and sample.contract.multiplier == 100

    def test_non_100_multiplier_override(self):
        chain = self._provider(multiplier_overrides={SPY: 10}).get_option_chain(SPY)
        assert chain.contracts and {q.contract.multiplier for q in chain.contracts} == {10}

    def test_closed_market_flag_and_crash_scenario(self):
        calm = self._provider().get_option_chain(SPY)
        closed = FixtureOptionsProvider(as_of=AS_OF.astimezone(timezone.utc), market_open=False).get_option_chain(SPY)
        assert calm.market_open is True and closed.market_open is False
        crash = self._provider().apply_scenario("crash").get_option_chain(SPY)
        assert float(crash.underlying_price) < float(calm.underlying_price) * 0.9

        def put_mid(chain):
            q = min((q for q in chain.contracts if q.contract.right == P),
                    key=lambda q: abs(float(q.contract.strike) - 0.95 * float(calm.underlying_price)))
            return float(q.mid)

        assert put_mid(crash) > put_mid(calm), "puts reprice up on a gap-down + vol spike"

    def test_scanner_only_proposes_contracts_that_exist_in_the_chain(self):
        chain = self._provider(spots={SPY: 695.0}).get_option_chain(SPY)
        cands = OptionsOpportunityScanner().scan(chain, context=ScanContext(account_equity=25_000))
        assert cands, "scanner finds candidates on a liquid fixture chain"
        index = {q.contract.occ_symbol: q for q in chain.contracts}
        for c in cands:
            assert c.feed_tier == DataFeedTier.FIXTURE and c.is_indicative is False
            for leg in c.legs:
                if leg.occ_symbol:
                    q = index[leg.occ_symbol]  # KeyError => invented contract
                    assert q.contract.strike == leg.strike and q.contract.right == leg.right
                    assert q.contract.multiplier == leg.multiplier

    def test_fixture_chain_helper_matches_provider_contract(self):
        chain = build_chain(SPY)
        assert chain.feed_tier == DataFeedTier.FIXTURE and FIXTURE_WARNING in chain.warnings


# ======================================================================================
# Alpaca adapter parsing (mock HTTP) - read-only, indicative tagging, local Greeks fallback
# ======================================================================================


def _iso_utc(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class MockAlpaca:
    """Mock ``transport(url, headers, timeout) -> (status, json, headers)`` that serves options data."""

    def __init__(self, *, size: int = 100, strip_greeks_for: set[str] | None = None,
                 opra_status: int = 200, fail_status: int | None = None, spot: float = 695.0,
                 ask_only: set[str] | None = None):
        self.size = size
        self.strip = strip_greeks_for or set()
        self.opra_status = opra_status
        self.fail_status = fail_status
        self.ask_only = ask_only or set()
        self.spot = spot
        self.source = build_chain(SPY)
        self.exp = EXP_21
        self.requests: list[dict] = []
        self.contracts_for_exp = [q for q in self.source.contracts if q.contract.expiration_date == self.exp
                                  and 0.85 * spot <= float(q.contract.strike) <= 1.15 * spot]

    # --- payload builders -------------------------------------------------------------
    def contract_json(self, q):
        c = q.contract
        return {"symbol": c.occ_symbol, "underlying_symbol": SPY, "type": c.right.value,
                "strike_price": str(c.strike), "expiration_date": c.expiration_date.isoformat(),
                "style": "american", "size": str(self.size), "open_interest": str(q.open_interest),
                "tradable": True, "root_symbol": SPY, "close_price": str(q.mid)}

    def snapshot_json(self, q):
        ts = _iso_utc(AS_OF)
        occ = q.contract.occ_symbol
        quote = {"bp": float(q.bid), "ap": float(q.ask), "t": ts}
        if occ in self.ask_only:
            quote = {"bp": 0, "ap": float(q.ask), "t": ts}
        snap = {"latestQuote": quote, "latestTrade": {"p": float(q.mid), "t": ts},
                "dailyBar": {"v": int(q.volume)}}
        if occ in self.strip:
            snap["greeks"] = None
            snap["impliedVolatility"] = None
        else:
            g = q.greeks
            snap["greeks"] = {"delta": g.delta, "gamma": g.gamma, "theta": g.theta, "vega": g.vega, "rho": g.rho}
            snap["impliedVolatility"] = q.implied_volatility
        return snap

    # --- transport --------------------------------------------------------------------
    def __call__(self, url, headers, timeout):
        parsed = urllib.parse.urlparse(url)
        query = urllib.parse.parse_qs(parsed.query)
        self.requests.append({"url": url, "path": parsed.path, "query": query, "headers": dict(headers),
                              "host": parsed.netloc})
        if self.fail_status:
            return self.fail_status, {"message": "synthetic failure"}, {}
        path = parsed.path
        if path.startswith("/v2/stocks/") and path.endswith("/snapshot"):
            return 200, {"latestTrade": {"p": self.spot, "t": _iso_utc(AS_OF)}}, {}
        if path == "/v2/options/contracts":
            return 200, {"option_contracts": [self.contract_json(q) for q in self.contracts_for_exp]}, {}
        if path.startswith("/v1beta1/options/snapshots"):
            if query.get("feed") == ["opra"] and self.opra_status != 200:
                return self.opra_status, {"message": "OPRA not permitted"}, {}
            return 200, {"snapshots": {q.contract.occ_symbol: self.snapshot_json(q)
                                       for q in self.contracts_for_exp}}, {}
        return 404, {"message": f"unexpected path {path}"}, {}


def make_provider(mock, *, feed="indicative", creds=True, sleeps=None):
    return AlpacaOptionsProvider(
        api_key_id=FAKE_ALPACA_KEY if creds else "", api_secret_key=FAKE_ALPACA_SECRET if creds else "",
        paper_base_url="https://paper-api.alpaca.markets", data_base_url="https://data.alpaca.markets",
        stock_feed="iex", feed=feed, transport=mock, now_fn=lambda: AS_OF.astimezone(timezone.utc),
        rate_limiter=TokenBucket(rate_per_sec=10_000, capacity=10_000),
        sleep=(sleeps.append if sleeps is not None else (lambda _s: None)),
    )


def _chain(provider):
    return provider.get_option_chain(SPY, min_dte=3, max_dte=60, as_of=AS_OF.astimezone(timezone.utc))


class TestAlpacaAdapterParsing:
    def test_indicative_feed_is_tagged_never_presented_as_opra(self):
        mock = MockAlpaca()
        chain = _chain(make_provider(mock))
        assert chain.contracts and chain.underlying_price == Decimal("695.0")
        assert chain.feed_tier == DataFeedTier.ALPACA_INDICATIVE
        assert chain.is_indicative is True and chain.is_delayed_trades is True
        assert WARN_INDICATIVE_FEED in chain.warnings
        assert chain.market_open is True and chain.provider_name == "alpaca"
        assert all(q.feed_tier == DataFeedTier.ALPACA_INDICATIVE and q.is_indicative and q.is_delayed_trades
                   for q in chain.contracts)
        feeds = {tuple(r["query"].get("feed", [])) for r in mock.requests if "snapshots" in r["path"]}
        assert feeds == {("indicative",)}

    def test_quote_fields_are_parsed_from_alpaca_shapes(self):
        mock = MockAlpaca()
        chain = _chain(make_provider(mock))
        src = {q.contract.occ_symbol: q for q in mock.contracts_for_exp}
        for q in chain.contracts:
            s = src[q.contract.occ_symbol]
            assert q.bid == s.bid and q.ask == s.ask
            assert q.mid == ((s.bid + s.ask) / 2).quantize(Decimal("0.0001"))
            assert q.contract.strike == s.contract.strike and q.contract.expiration_date == EXP_21
            assert q.volume == s.volume and q.open_interest == s.open_interest
            assert q.greeks.computed_locally is False and q.greeks.delta == pytest.approx(s.greeks.delta)
            assert q.implied_volatility == pytest.approx(s.implied_volatility)
        assert chain.expirations == [EXP_21]

    def test_missing_greeks_fall_back_to_local_bsm_and_are_flagged(self):
        probe = MockAlpaca()
        atm_calls = sorted((q for q in probe.contracts_for_exp if q.contract.right == C),
                           key=lambda q: abs(float(q.contract.strike) - 695.0))[:3]
        stripped = {q.contract.occ_symbol for q in atm_calls}
        mock = MockAlpaca(strip_greeks_for=stripped)
        chain = _chain(make_provider(mock))
        by_occ = {q.contract.occ_symbol: q for q in chain.contracts}
        src = {q.contract.occ_symbol: q for q in mock.contracts_for_exp}
        for occ in stripped:
            q = by_occ[occ]
            assert q.greeks.computed_locally is True and WARN_LOCAL_GREEKS in q.data_warnings
            assert q.implied_volatility is not None and q.greeks.delta is not None
            assert q.greeks.delta == pytest.approx(src[occ].greeks.delta, abs=0.03)
            assert q.implied_volatility == pytest.approx(src[occ].implied_volatility, abs=0.03)
        provider_quote = next(q for q in chain.contracts if q.contract.occ_symbol not in stripped)
        assert provider_quote.greeks.computed_locally is False
        assert any(w.startswith(WARN_LOCAL_GREEKS) for w in chain.warnings)

    def test_contract_size_is_the_multiplier_never_assumed_100(self):
        chain = _chain(make_provider(MockAlpaca(size=10)))
        assert chain.contracts and {q.contract.multiplier for q in chain.contracts} == {10}
        assert all(any(w.startswith(f"{WARN_NONSTANDARD_MULTIPLIER}:10") for w in q.data_warnings)
                   for q in chain.contracts)

    def test_zero_bid_is_flagged_and_gets_no_mid(self):
        probe = MockAlpaca()
        victim = next(q for q in probe.contracts_for_exp if q.contract.right == P).contract.occ_symbol
        chain = _chain(make_provider(MockAlpaca(ask_only={victim})))
        q = next(x for x in chain.contracts if x.contract.occ_symbol == victim)
        assert "no_bid" in q.data_warnings and (q.bid is None or q.bid == 0)

    def test_opra_request_is_labelled_opra_only_when_the_request_succeeds(self):
        ok = make_provider(MockAlpaca(opra_status=200), feed="opra")
        chain_ok = _chain(ok)
        assert chain_ok.feed_tier == DataFeedTier.ALPACA_OPRA and chain_ok.is_indicative is False
        denied_mock = MockAlpaca(opra_status=403)
        chain_denied = _chain(make_provider(denied_mock, feed="opra"))
        assert chain_denied.feed_tier == DataFeedTier.ALPACA_INDICATIVE and chain_denied.is_indicative is True
        assert "opra_unavailable_fell_back_to_indicative" in chain_denied.warnings

    def test_no_credentials_means_no_http_and_an_honest_empty_chain(self):
        mock = MockAlpaca()
        chain = _chain(make_provider(mock, creds=False))
        assert mock.requests == []
        assert chain.contracts == [] and chain.feed_tier == DataFeedTier.UNAVAILABLE
        assert WARN_CREDENTIALS_MISSING in chain.warnings

    def test_http_failures_degrade_gracefully_without_raising(self):
        sleeps: list[float] = []
        mock = MockAlpaca(fail_status=500)
        chain = _chain(make_provider(mock, sleeps=sleeps))
        assert chain.contracts == [] and chain.feed_tier == DataFeedTier.UNAVAILABLE
        assert sleeps, "5xx responses are retried with backoff before giving up"

    def test_health_never_leaks_credentials(self):
        provider = make_provider(MockAlpaca())
        _chain(provider)
        blob = json.dumps(provider.get_provider_health(), default=str)
        assert FAKE_ALPACA_KEY not in blob and FAKE_ALPACA_SECRET not in blob


class TestAlpacaAdapterIsReadOnly:
    ALLOWED = ("/v2/options/contracts", "/v1beta1/options/snapshots", "/v2/stocks/")
    FORBIDDEN = ("/orders", "/positions", "/account", "/watchlists", "/activities")

    def _exercise_everything(self, mock):
        provider = make_provider(mock)
        chain = _chain(provider)
        occs = [q.contract.occ_symbol for q in chain.contracts[:5]]
        provider.get_quotes_for_occ(occs, underlying_hint=SPY)
        provider.get_underlying_price(SPY)
        provider.get_provider_health()
        return provider

    def test_only_market_data_and_contract_metadata_paths_are_requested(self):
        mock = MockAlpaca()
        self._exercise_everything(mock)
        assert mock.requests
        for r in mock.requests:
            assert r["path"].startswith(self.ALLOWED), r["path"]
            assert not any(frag in r["path"] for frag in self.FORBIDDEN), r["path"]
            assert r["host"] in ("data.alpaca.markets", "paper-api.alpaca.markets")
        # contract metadata is read from the paper host with GET only (transport has no body / verb)
        assert {r["host"] for r in mock.requests if r["path"].startswith("/v2/options/contracts")} == {
            "paper-api.alpaca.markets"}

    @pytest.mark.parametrize("path", [
        "/v2/orders", "/v2/orders/abc", "/v2/positions", "/v2/positions/SPY", "/v2/account",
        "/v2/account/activities", "/v2/watchlists", "/v2/options/contracts/../orders",
        "/v1beta1/options/snapshots/../../v2/account", "/v2/options/exercise",
    ])
    def test_trading_and_account_paths_are_structurally_unreachable(self, path):
        with pytest.raises(ValueError):
            _assert_read_only_path(path)

    def test_provider_get_refuses_trading_paths_before_any_http(self):
        mock = MockAlpaca()
        provider = make_provider(mock)
        for path in ("/v2/orders", "/v2/account", "/v2/positions"):
            with pytest.raises(ValueError):
                provider._get("https://paper-api.alpaca.markets", path)
        assert mock.requests == []

    def test_no_order_or_trade_method_exists_on_the_adapter(self):
        names = {n.lower() for n in dir(AlpacaOptionsProvider) if not n.startswith("__")}
        assert not any(k in n for n in names for k in ("order", "submit", "place", "exercise", "cancel", "buy", "sell"))

    def test_adapter_source_has_no_http_verbs_other_than_get(self):
        src = Path(alpaca_mod.__file__).read_text(encoding="utf-8")
        assert 'method="GET"' in src
        assert not re.search(r'method\s*=\s*["\'](POST|PUT|PATCH|DELETE)["\']', src)


# ======================================================================================
# FastAPI routes: /api/v1/options/...  (skipped until the router module exists)
# ======================================================================================


@pytest.fixture(scope="module")
def options_router_module():
    return pytest.importorskip("quantara_options.api.router", reason="Options API router not merged yet")


@pytest.fixture
def api(options_router_module, broker_test_database, monkeypatch):
    """TestClient over a minimal app that mounts ONLY the options router behind ``verify_api_key``.

    Alpaca credentials are blanked for the duration so no route can reach a real market-data host.
    """
    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient

    from quantara_engine.api.deps import verify_api_key
    from quantara_engine.core.config import settings
    from quantara_engine.db import session as dbs
    from quantara_options.db.repository import OptionsRepository

    monkeypatch.setattr(settings, "alpaca_api_key_id", "")
    monkeypatch.setattr(settings, "alpaca_api_secret_key", "")
    router = options_router_module.router
    paths = [getattr(r, "path", "") for r in router.routes]
    overview = next((p for p in paths if p.endswith("/overview")), None)
    assert overview, f"router must expose .../overview, has {paths}"
    prefix = "" if overview.startswith("/api/v1/options") else "/api/v1"
    app = FastAPI()
    app.include_router(router, prefix=prefix, dependencies=[Depends(verify_api_key)])
    base = f"{prefix}{overview[: -len('/overview')]}"

    s = dbs.SessionLocal()
    try:
        if not pg_table_exists(s, "opt_accounts"):
            pytest.skip("opt_* tables not present in the test database")
        pg_purge_qa_rows(s)
        OptionsRepository(s, autocommit=True).ensure_lab_seeded()
    finally:
        s.close()

    class Api:
        client = TestClient(app, raise_server_exceptions=False)
        url = base
        headers = {"X-API-Key": settings.quantara_api_key}

        def get(self, path, **kw):
            return self.client.get(self.url + path, headers=kw.pop("headers", self.headers), **kw)

        def post(self, path, **kw):
            return self.client.post(self.url + path, headers=kw.pop("headers", self.headers), **kw)

    try:
        yield Api()
    finally:
        s = dbs.SessionLocal()
        try:
            pg_purge_qa_rows(s)
        finally:
            s.close()


def _fixture_long_call(api_obj):
    """Build a candidate that is grounded in the chain the API itself serves with ``use_fixtures=true``."""
    from tests.fixtures.options_fixtures import make_candidate, opt_leg

    resp = api_obj.get("/chain", params={"symbol": SPY, "use_fixtures": "true", "min_dte": 3, "max_dte": 60})
    assert resp.status_code == 200, resp.text
    chain = OptionChainResponse.model_validate(resp.json())
    exp = next(e for e in sorted(chain.expirations) if (e - chain.as_of.date()).days >= 5)
    strikes = sorted({q.contract.strike for q in chain.contracts
                      if q.contract.expiration_date == exp and q.contract.right == C})
    atm = min(strikes, key=lambda k: abs(k - chain.underlying_price))
    leg = opt_leg(chain, exp, C, OptionSide.LONG, atm)
    return chain, make_candidate(StrategyType.LONG_CALL, chain, [leg], expiration=exp)


class TestOptionsApi:
    ENDPOINTS = [
        ("get", "/overview", None),
        ("get", "/chain?symbol=SPY&use_fixtures=true", None),
        ("post", "/control", {"action": "stop"}),
        ("post", "/simulate/open", {"account_slug": "options-10k", "candidate": {}}),
        ("post", "/positions/does-not-exist/close", None),
        ("post", "/positions/does-not-exist/roll", None),
    ]

    @pytest.mark.parametrize("verb, path, body", ENDPOINTS)
    def test_x_api_key_is_required_on_every_endpoint(self, api, verb, path, body):
        call = getattr(api.client, verb)
        kwargs = {"json": body} if body is not None else {}
        missing = call(api.url + path, **kwargs)
        wrong = call(api.url + path, headers={"X-API-Key": "definitely-wrong"}, **kwargs)
        assert missing.status_code == 401 and wrong.status_code == 401

    def test_overview_returns_all_lab_accounts_and_honest_engine_status(self, api):
        from quantara_options.api_schemas import OptionsLabOverviewResponse
        from quantara_options.db.seed_lab import LAB_ACCOUNT_SLUGS

        resp = api.get("/overview", params={"account_slug": "options-10k", "symbol": SPY})
        assert resp.status_code == 200, resp.text
        ov = OptionsLabOverviewResponse.model_validate(resp.json())
        assert set(LAB_ACCOUNT_SLUGS) <= {a.slug for a in ov.accounts}
        assert ov.selected_account is not None and ov.selected_account.slug == "options-10k"
        assert ov.engine_status.groq_model == "openai/gpt-oss-120b"
        # no Alpaca credentials in this test => the status must NOT claim live OPRA readiness
        assert ov.engine_status.feed_tier != DataFeedTier.ALPACA_OPRA
        if ov.engine_status.readiness_market_data:
            assert ov.engine_status.feed_tier in (DataFeedTier.FIXTURE, DataFeedTier.ALPACA_INDICATIVE)

    def test_overview_for_unknown_account_is_a_clean_client_error(self, api):
        resp = api.get("/overview", params={"account_slug": "no-such-account"})
        assert resp.status_code in (400, 404, 422)

    def test_chain_with_fixtures_is_tagged_synthetic(self, api):
        resp = api.get("/chain", params={"symbol": SPY, "use_fixtures": "true"})
        assert resp.status_code == 200, resp.text
        chain = OptionChainResponse.model_validate(resp.json())
        assert chain.feed_tier == DataFeedTier.FIXTURE and chain.is_indicative is False
        assert WARN_SYNTHETIC_FIXTURE in chain.warnings and chain.contracts

    def test_chain_without_fixtures_and_without_credentials_never_fabricates_data(self, api):
        resp = api.get("/chain", params={"symbol": SPY, "use_fixtures": "false"})
        assert resp.status_code in (200, 502, 503)
        if resp.status_code == 200:
            chain = OptionChainResponse.model_validate(resp.json())
            assert chain.contracts == [] and chain.feed_tier == DataFeedTier.UNAVAILABLE

    def test_chain_rejects_malformed_symbols(self, api):
        resp = api.get("/chain", params={"symbol": "SPY; DROP TABLE opt_accounts", "use_fixtures": "true"})
        assert resp.status_code in (200, 400, 422)
        if resp.status_code == 200:
            assert OptionChainResponse.model_validate(resp.json()).contracts == []

    def test_control_rejects_unknown_actions_and_stop_is_idempotent(self, api):
        assert api.post("/control", json={"action": "detonate"}).status_code in (400, 422)
        for _ in range(2):
            r = api.post("/control", json={"action": "stop"})
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["ok"] is True and body["engine_status"]["running_cycle"] in (False, None)

    def test_control_run_once_with_fixtures_is_tagged_as_fixture(self, api):
        r = api.post("/control", json={"action": "run_once", "use_fixtures": True, "symbols": [SPY]})
        assert r.status_code == 200, r.text
        status = r.json()["engine_status"]
        assert status["feed_tier"] != "alpaca_opra", "a fixture cycle must never be reported as live OPRA"
        if status["feed_tier"] == "fixture":
            assert status["is_indicative"] is False

    def test_simulate_open_close_roundtrip_on_a_qa_account(self, api, broker_test_database):
        from quantara_engine.db import session as dbs

        slug = f"{OPTQA_PREFIX}api_{abs(hash(object())) % 10**8}"
        s = dbs.SessionLocal()
        try:
            pg_insert_qa_account(s, slug, 25_000)
        finally:
            s.close()
        chain, cand = _fixture_long_call(api)
        opened = api.post("/simulate/open", json={"account_slug": slug, "candidate": cand.model_dump(mode="json")})
        assert opened.status_code == 200, opened.text
        view = opened.json()
        assert view["status"] == "open" and view["strategy_type"] == "long_call" and view["account_slug"] == slug
        assert view["legs"][0]["occ_symbol"] == cand.legs[0].occ_symbol and view["legs"][0]["multiplier"] == 100
        assert view["max_loss_usd"] == pytest.approx(float(cand.max_loss_usd), rel=0.05)
        closed = api.post(f"/positions/{view['id']}/close")
        assert closed.status_code == 200, closed.text
        assert closed.json()["status"] in ("closed", "expired", "assigned", "rolled")
        assert closed.json()["id"] == view["id"]
        again = api.post(f"/positions/{view['id']}/close")
        assert again.status_code in (200, 400, 404, 409), "closing twice is idempotent or a clean client error"

    def test_simulate_open_rejects_an_invented_occ_and_creates_nothing(self, api, broker_test_database):
        from quantara_engine.db import session as dbs
        from quantara_options.db.repository import OptionsRepository

        slug = f"{OPTQA_PREFIX}api_bad_{abs(hash(object())) % 10**8}"
        s = dbs.SessionLocal()
        try:
            pg_insert_qa_account(s, slug, 25_000)
        finally:
            s.close()
        _chain_obj, cand = _fixture_long_call(api)
        legs = [cand.legs[0].model_copy(update={"occ_symbol": "SPY991231C00001000"})]
        bad = cand.model_copy(update={"legs": legs})
        resp = api.post("/simulate/open", json={"account_slug": slug, "candidate": bad.model_dump(mode="json")})
        assert resp.status_code in (400, 409, 422), resp.text
        s = dbs.SessionLocal()
        try:
            assert OptionsRepository(s).list_strategy_groups(slug) == []
            assert OptionsRepository(s).get_account_summary(slug).cash_balance == pytest.approx(25_000)
        finally:
            s.close()

    def test_close_and_roll_of_unknown_strategy_are_404_not_500(self, api):
        missing = "00000000-0000-0000-0000-000000000000"
        assert api.post(f"/positions/{missing}/close").status_code in (400, 404)
        assert api.post(f"/positions/{missing}/roll").status_code in (400, 404, 422)

    def test_simulate_open_requires_a_known_account(self, api):
        _chain_obj, cand = _fixture_long_call(api)
        r = api.post("/simulate/open", json={"account_slug": "no-such-account",
                                             "candidate": cand.model_dump(mode="json")})
        assert r.status_code in (400, 404, 422)


# ======================================================================================
# Isolation: System B equity tables, System A paper-state.json, no trading imports
# ======================================================================================


def _system_b_fingerprint(session):
    from sqlalchemy import text

    out = {}
    for table in ("broker_accounts", "live_sim_positions"):
        if not pg_table_exists(session, table):
            out[table] = None
            continue
        out[table] = tuple(session.execute(text(
            f"SELECT COUNT(*), COALESCE(md5(string_agg(x::text, '|' ORDER BY x::text)), '') FROM {table} x"
        )).one())
    return out


class _WriteSentinel:
    """Records (and blocks) any attempt to modify files that belong to System A."""

    def __init__(self, monkeypatch):
        import builtins
        import io
        import os

        self.attempts: list[tuple[str, str]] = []
        real_open, real_io_open = builtins.open, io.open

        def is_a(p) -> bool:
            s = str(p).replace("\\", "/").lower()
            return "quantara-ai-trader" in s or s.endswith("paper-state.json")

        def make_open(real):
            def guarded(file, mode="r", *a, **k):
                if isinstance(file, (str, bytes, os.PathLike)) and is_a(file) and set(str(mode)) & set("wax+"):
                    self.attempts.append(("open:" + str(mode), str(file)))
                    raise PermissionError("System A files are read-only for QUANTARA Options tests")
                return real(file, mode, *a, **k)
            return guarded

        monkeypatch.setattr(builtins, "open", make_open(real_open))
        monkeypatch.setattr(io, "open", make_open(real_io_open))
        for name in ("replace", "rename", "remove", "unlink"):
            real = getattr(os, name)

            def make_guard(real=real, name=name):
                def guarded(*args, **kw):
                    if any(is_a(a) for a in args if isinstance(a, (str, bytes, os.PathLike))):
                        self.attempts.append((name, str(args[0])))
                        raise PermissionError("System A files are read-only for QUANTARA Options tests")
                    return real(*args, **kw)
                return guarded

            monkeypatch.setattr(os, name, make_guard())


def _run_options_workload(lab):
    """A representative options session: scan -> decide (rules) -> guard -> open -> mark -> close -> roll."""
    from quantara_options.ai import GroqOptionsAdvisor, OptionsRiskGuard
    from quantara_options.contracts import DecisionMode

    chain = build_chain(SPY)
    slug = lab.new_account(25_000, mode="rules")
    advisor = GroqOptionsAdvisor(env={}, secrets_path=Path("__no_such_file__.json"), transport=lambda *a, **k: (500, {}, {}))
    cands = OptionsOpportunityScanner().scan(chain, context=ScanContext(account_equity=25_000))
    res = advisor.decide(account=lab.acct(slug), candidates=cands[:6], mode=DecisionMode.RULES)
    guard = OptionsRiskGuard()
    opened = []
    for d in res.decisions:
        cand = next(c for c in cands if c.candidate_id == d.candidate_id)
        if guard.validate_open(cand, account=lab.acct(slug), chain=chain, open_strategies=lab.groups(slug, "open")).passed:
            opened.append(lab.open(slug, cand, chain=chain))
    lab.mtm(slug, chain)
    for gid in opened:
        lab.close(gid, chain)
    return slug, opened


class TestSystemIsolation:
    def test_options_workload_never_writes_system_a_files(self, options_lab, monkeypatch):
        sentinel = _WriteSentinel(monkeypatch)
        slug, opened = _run_options_workload(options_lab)
        assert opened, "workload must actually trade for this test to mean anything"
        assert sentinel.attempts == []
        assert f(options_lab.acct(slug).cash_reserved) == pytest.approx(0, abs=0.01)

    def test_sentinel_really_blocks_a_write_to_paper_state(self, monkeypatch):
        sentinel = _WriteSentinel(monkeypatch)
        with pytest.raises(PermissionError):
            open(SYSTEM_A_PAPER_STATE, "a")
        assert sentinel.attempts, "(self-test) the guard would have caught a System A write"

    def test_options_source_never_references_system_a_paper_state_or_system_b_tables(self):
        banned = ("paper-state", "paper_state", "broker_accounts", "live_sim_positions", "live_sim_accounts",
                  "TradingStore", "simulated_broker", "execution_bridge", "live_execution_gate")
        offenders = []
        for py in QUANTARA_OPTIONS_DIR.rglob("*.py"):
            text = py.read_text(encoding="utf-8")
            offenders += [f"{py.relative_to(ENGINE_ROOT)}: {b}" for b in banned if b in text]
        assert offenders == []

    def test_options_package_imports_no_trading_modules_from_the_engine(self):
        forbidden = ("broker", "execution", "live_sim", "persistence", "trading", "competition",
                     "portfolio", "pipeline", "owner_portfolio", "learning")
        bad = []
        for py in QUANTARA_OPTIONS_DIR.rglob("*.py"):
            for node in ast.walk(ast.parse(py.read_text(encoding="utf-8"))):
                mods = []
                if isinstance(node, ast.Import):
                    mods = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    mods = [node.module]
                for m in mods:
                    parts = m.split(".")
                    if parts[0] == "quantara_engine" and len(parts) > 1 and parts[1] in forbidden:
                        bad.append(f"{py.name}: {m}")
        assert bad == []

    def test_options_source_has_no_write_access_to_the_filesystem_outside_its_own_db(self):
        pattern = re.compile(r"""open\([^)]*,\s*['"][wax][+bt]*['"]|\.write_text\(|\.write_bytes\(|os\.(replace|rename|remove|unlink)\(""")
        hits = [f"{py.name}:{i + 1}" for py in QUANTARA_OPTIONS_DIR.rglob("*.py")
                for i, line in enumerate(py.read_text(encoding="utf-8").splitlines()) if pattern.search(line)]
        assert hits == []

    def test_options_migration_is_independent_of_equity_tables(self):
        sql = (REPO_ROOT / "packages" / "db" / "migrations" / "0025_quantara_options_lab.sql").read_text(encoding="utf-8")
        for table in re.findall(r"CREATE TABLE IF NOT EXISTS (\w+)", sql):
            assert table.startswith("opt_"), table
        refs = re.findall(r"REFERENCES\s+(\w+)", sql)
        assert refs and all(r.startswith("opt_") for r in refs), "no foreign keys into the equity broker tables"
        for banned in ("broker_accounts", "live_sim", "accounts_equity"):
            assert banned not in sql

    def test_postgres_options_activity_leaves_system_b_tables_untouched(self, broker_test_database):
        from quantara_engine.db import session as dbs
        from quantara_options.db.repository import OptionsRepository

        session = dbs.SessionLocal()
        try:
            if not pg_table_exists(session, "opt_accounts"):
                pytest.skip("opt_* tables not present in the test database")
            pg_purge_qa_rows(session)
            before = _system_b_fingerprint(session)
            assert any(v is not None for v in before.values()), "need at least one System B table to compare"
            repo = OptionsRepository(session, autocommit=True)
            repo.ensure_lab_seeded()
            slug = f"{OPTQA_PREFIX}iso_{abs(hash(object())) % 10**8}"
            pg_insert_qa_account(session, slug, 10_000)
            chain = build_chain(SPY)
            cand = candidate_for(StrategyType.BULL_PUT_CREDIT_SPREAD, chain)
            view = repo.create_strategy_with_legs_and_fill(slug, cand, idempotency_key=f"optqa:{slug}:iso")
            repo.upsert_stock_lot(slug, "PLTR", shares=100, avg_cost_basis=Decimal("42"), encumbered_shares=0)
            repo.set_wheel_state(slug, "PLTR", "holding_shares")
            repo.close_strategy_record(view.id, status="closed", close_reason="manual_close")
            assert repo.get_account_summary(slug).closed_strategies_count == 1
            session.commit()
            after = _system_b_fingerprint(session)
            assert before == after, "System B (equity) tables must be byte-for-byte unchanged"
        finally:
            try:
                pg_purge_qa_rows(session)
            finally:
                session.close()

    def test_system_a_paper_state_is_only_referenced_read_only_if_at_all(self):
        # System A's file exists in the developer checkout; options code must not even know its name.
        for py in QUANTARA_OPTIONS_DIR.rglob("*.py"):
            assert "paper-state" not in py.read_text(encoding="utf-8"), py.name
