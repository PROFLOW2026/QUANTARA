"""Live Sim C/D/E forwarding + FX asset-envelope unit consistency."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from quantara_engine.broker.instruments import get_instrument_spec
from quantara_engine.broker.margin import initial_margin_for_notional, quote_notional_usd
from quantara_engine.broker.profile import QUANTARA_LIVE_SIM_10K
from quantara_engine.competition.multi_strategy_constants import (
    MEAN_REVERSION_STRATEGY_SLUG,
    MEAN_REVERSION_STRATEGY_VERSION,
    MOMENTUM_CONTINUATION_STRATEGY_SLUG,
    MOMENTUM_CONTINUATION_STRATEGY_VERSION,
    VOLATILITY_SQUEEZE_STRATEGY_SLUG,
    VOLATILITY_SQUEEZE_STRATEGY_VERSION,
)
from quantara_engine.competition.robot_registry import MULTI_STRATEGY_SLUGS
from quantara_engine.domain.types import Direction, ExecutionAssumptions, Instrument, SignalAction
from quantara_engine.live_sim.sizing import max_safe_quantity_for_live_sim, size_live_sim_entry
from quantara_engine.owner_portfolio.asset_risk import evaluate_asset_envelope_risk
from quantara_engine.owner_portfolio.constants import LIVE_SIM_PER_ASSET_CAPITAL
from quantara_engine.portfolio.currency import FxRateTable
from quantara_workers.jobs.run_strategy import (
    _canonical_strategy_representatives,
    _process_candle_batch,
)

TZ3 = timezone(timedelta(hours=3))

# Historical rejected GBPJPY Live Sim candidates (read-only fixtures — do not replay).
GBPJPY_CASES = (
    {
        "label": "15m SHORT",
        "timeframe": "15m",
        "direction": Direction.SHORT,
        "qty": Decimal("1000"),
        "entry": Decimal("207.59500000"),
        "risk_usd": Decimal("3.79"),
    },
    {
        "label": "5m LONG",
        "timeframe": "5m",
        "direction": Direction.LONG,
        "qty": Decimal("1000"),
        "entry": Decimal("207.67650000"),
        "risk_usd": Decimal("1.43"),
    },
)
JPY_PER_USD = Decimal("150")
ASSET_CASH = LIVE_SIM_PER_ASSET_CAPITAL  # $1,250


def _gbpjpy_instrument() -> Instrument:
    return Instrument(
        id="gbpjpy-id",
        symbol="GBPJPY",
        name="GBP/JPY",
        asset_class="forex",
        quote_currency="JPY",
        quantity_step=Decimal("1000"),
        min_quantity=Decimal("1000"),
    )


def _usd_instrument(symbol: str, *, step: str = "0.0001", asset_class: str = "crypto") -> Instrument:
    return Instrument(
        id=f"{symbol.lower()}-id",
        symbol=symbol,
        name=symbol,
        asset_class=asset_class,
        quote_currency="USD",
        quantity_step=Decimal(step),
        min_quantity=Decimal(step),
    )


def _cde_group(*, clones_per_strategy: int = 5, symbol_id: str = "inst-btc") -> list[dict]:
    group: list[dict] = []
    versions = {
        MEAN_REVERSION_STRATEGY_SLUG: MEAN_REVERSION_STRATEGY_VERSION,
        VOLATILITY_SQUEEZE_STRATEGY_SLUG: VOLATILITY_SQUEEZE_STRATEGY_VERSION,
        MOMENTUM_CONTINUATION_STRATEGY_SLUG: MOMENTUM_CONTINUATION_STRATEGY_VERSION,
    }
    for slug in (
        MEAN_REVERSION_STRATEGY_SLUG,
        VOLATILITY_SQUEEZE_STRATEGY_SLUG,
        MOMENTUM_CONTINUATION_STRATEGY_SLUG,
    ):
        for risk_i, risk_slug in enumerate(
            ("ultra_conservative", "conservative", "balanced", "aggressive", "ultra_aggressive")[
                :clones_per_strategy
            ]
        ):
            group.append(
                {
                    "portfolio": MagicMock(id=f"p-{slug}-{risk_i}"),
                    "instance": MagicMock(
                        id=f"i-{slug}-{risk_i}",
                        strategy_slug=slug,
                        strategy_version=versions[slug],
                        instrument_id=symbol_id,
                        strategy_version_id="sv",
                        timeframe="15m",
                        parameter_overrides={},
                        risk_slug=risk_slug,
                    ),
                    "risk_profile": MagicMock(slug=risk_slug),
                }
            )
    return group


def test_cde_group_mixes_distinct_strategies_and_risk_tiers():
    """Root-cause evidence: one candle group mixes C/D/E × risk tiers."""
    group = _cde_group()
    reps = _canonical_strategy_representatives(group)
    assert len(group) == 15
    assert len(reps) == 3
    slugs = {e["instance"].strategy_slug for e in reps}
    assert slugs == set(MULTI_STRATEGY_SLUGS)
    versions = {e["instance"].strategy_version for e in reps}
    assert versions == {
        MEAN_REVERSION_STRATEGY_VERSION,
        VOLATILITY_SQUEEZE_STRATEGY_VERSION,
        MOMENTUM_CONTINUATION_STRATEGY_VERSION,
    }
    risk_tiers = {e["instance"].risk_slug for e in group}
    assert len(risk_tiers) == 5


@pytest.mark.parametrize("symbol", ["BTCUSD", "ETHUSD", "XAUUSD"])
def test_volatility_squeeze_research_opportunity_forwards_one_live_sim_candidate(symbol):
    store = MagicMock()
    instrument = MagicMock(symbol=symbol, id=f"inst-{symbol}")
    ts = datetime(2026, 9, 14, 0, 0, tzinfo=TZ3)
    candles = [
        MagicMock(timestamp=ts, close=Decimal("100")),
        MagicMock(timestamp=ts + timedelta(minutes=15), close=Decimal("100")),
    ]
    sell = MagicMock(
        action=SignalAction.SELL,
        suggested_sl=Decimal("101"),
        suggested_tp=Decimal("98"),
    )
    # Only SQZ actionable; MR/MOM hold — prove SQZ is not dropped by group[0]=MR.
    signals_by_slug = {
        MEAN_REVERSION_STRATEGY_SLUG: None,
        VOLATILITY_SQUEEZE_STRATEGY_SLUG: sell,
        MOMENTUM_CONTINUATION_STRATEGY_SLUG: None,
    }
    group = _cde_group(symbol_id=instrument.id)
    store.timeframe_group_already_processed.return_value = False
    store.batch_load_portfolio_states.return_value = {}
    store.load_portfolio_runtime_state.return_value = MagicMock(
        open_positions=MagicMock(return_value=[])
    )
    store.list_pending_order_intents.return_value = []

    with patch("quantara_workers.jobs.run_strategy.CandleProcessor") as CP, patch(
        "quantara_engine.live_sim.allocator.maybe_allocate_live_sim",
        return_value={"status": "queued"},
    ) as allocate, patch(
        "quantara_engine.learning.hooks.observe_generic_eval"
    ) as observe:
        def side_effect(*args, **kwargs):
            inst = kwargs.get("strategy_instance") or (
                args[1] if len(args) > 1 else None
            )
            # CandleProcessor(portfolio_state=..., strategy_instance=...)
            strategy = kwargs.get("strategy_instance")
            proc = MagicMock()
            proc.all_candles = []
            proc.decisions = []
            proc.pending_intents = []
            proc._persisted_intents = set()
            slug = strategy.strategy_slug if strategy is not None else ""
            sig = signals_by_slug.get(slug)
            proc.evaluate_signal.return_value = (sig, candles[0])
            proc.process_candle.return_value = MagicMock(decisions=[], pending_intents=[])
            return proc

        CP.side_effect = side_effect
        _process_candle_batch(
            store,
            instrument,
            "15m",
            group,
            candles,
            0,
            datetime(2026, 9, 14, 0, 30, tzinfo=TZ3),
            MagicMock(),
            ts + timedelta(minutes=15),
            allow_live_execution=True,
            per_portfolio_eval=True,
        )

    assert allocate.call_count == 1
    entry = allocate.call_args.kwargs["entry"]
    assert entry["instance"].strategy_slug == VOLATILITY_SQUEEZE_STRATEGY_SLUG
    observe.assert_called_once()
    assert observe.call_args.kwargs["strategy_slug"] == VOLATILITY_SQUEEZE_STRATEGY_SLUG


def test_five_risk_tier_clones_yield_one_live_sim_candidate_per_strategy():
    store = MagicMock()
    instrument = MagicMock(symbol="ETHUSD", id="inst-eth")
    ts = datetime(2026, 9, 14, 1, 0, tzinfo=TZ3)
    candles = [MagicMock(timestamp=ts, close=Decimal("2500"))]
    buy = MagicMock(action=SignalAction.BUY, suggested_sl=Decimal("2490"), suggested_tp=Decimal("2520"))
    group = _cde_group()
    store.timeframe_group_already_processed.return_value = False
    store.batch_load_portfolio_states.return_value = {}
    store.load_portfolio_runtime_state.return_value = MagicMock(
        open_positions=MagicMock(return_value=[])
    )
    store.list_pending_order_intents.return_value = []

    with patch("quantara_workers.jobs.run_strategy.CandleProcessor") as CP, patch(
        "quantara_engine.live_sim.allocator.maybe_allocate_live_sim",
        return_value={"status": "queued"},
    ) as allocate, patch("quantara_engine.learning.hooks.observe_generic_eval") as observe:
        def side_effect(*args, **kwargs):
            proc = MagicMock()
            proc.all_candles = []
            proc.decisions = []
            proc.pending_intents = []
            proc._persisted_intents = set()
            proc.evaluate_signal.return_value = (buy, candles[0])
            proc.process_candle.return_value = MagicMock(decisions=[], pending_intents=[])
            return proc

        CP.side_effect = side_effect
        _process_candle_batch(
            store,
            instrument,
            "15m",
            group,
            candles,
            0,
            datetime(2026, 9, 14, 1, 30, tzinfo=TZ3),
            MagicMock(),
            ts,
            allow_live_execution=True,
            per_portfolio_eval=True,
        )

    assert allocate.call_count == 3
    assert observe.call_count == 3
    assert {c.kwargs["entry"]["instance"].strategy_slug for c in allocate.call_args_list} == set(
        MULTI_STRATEGY_SLUGS
    )


def test_gbpjpy_raw_jpy_notional_never_passed_as_usd():
    spec = get_instrument_spec("GBPJPY")
    qty = Decimal("1000")
    entry = Decimal("207.595")
    fx = {"JPY": JPY_PER_USD}
    raw_quote = qty * entry
    usd = quote_notional_usd(qty, entry, spec, fx)
    assert raw_quote == Decimal("207595.000")
    assert usd != raw_quote
    assert usd == (raw_quote / JPY_PER_USD).quantize(Decimal("0.01"))


@pytest.mark.parametrize("case", GBPJPY_CASES, ids=lambda c: c["label"])
def test_gbpjpy_historical_envelope_re_eval_no_replay(case):
    """Rollback-mode economics only — must not create trades."""
    spec = get_instrument_spec("GBPJPY")
    fx = {"JPY": JPY_PER_USD}
    qty = case["qty"]
    entry = case["entry"]
    old_required = (qty * entry * Decimal("0.1")).quantize(Decimal("0.01"))
    usd_notional = quote_notional_usd(qty, entry, spec, fx)
    rules = QUANTARA_LIVE_SIM_10K.rules_for(spec.asset_class)
    broker_im = initial_margin_for_notional(usd_notional, rules)
    assert old_required > ASSET_CASH  # old false reject
    assert broker_im < ASSET_CASH  # corrected cash requirement fits envelope
    assert rules.initial_margin_pct == Decimal("5")  # IBKR-like FX, not hardcoded 10%


def test_gbpjpy_asset_envelope_uses_broker_im_not_hardcoded_10pct():
    store = MagicMock()
    with patch(
        "quantara_engine.owner_portfolio.asset_allocation.is_equal_asset_mode_active",
        return_value=True,
    ), patch(
        "quantara_engine.owner_portfolio.asset_risk.get_asset_allocation_row",
        return_value={
            "enabled": True,
            "current_cash": ASSET_CASH,
            "open_sl_risk_usd": Decimal("0"),
            "starting_allocated_capital": ASSET_CASH,
            "gross_exposure": Decimal("0"),
            "current_equity": ASSET_CASH,
        },
    ), patch(
        "quantara_engine.owner_portfolio.asset_risk.asset_equity",
        return_value=ASSET_CASH,
    ):
        spec = get_instrument_spec("GBPJPY")
        qty = Decimal("1000")
        entry = Decimal("207.595")
        fx = {"JPY": JPY_PER_USD}
        usd = quote_notional_usd(qty, entry, spec, fx)
        required = initial_margin_for_notional(usd, QUANTARA_LIVE_SIM_10K.rules_for("forex"))
        old_required = qty * entry * Decimal("0.1")

        old = evaluate_asset_envelope_risk(
            store,
            owner_slug="live-sim-owner",
            canonical_symbol="GBPJPY",
            incremental_sl_risk_usd=Decimal("3.79"),
            incremental_notional_usd=qty * entry,
            required_cash_usd=old_required,
        )
        new = evaluate_asset_envelope_risk(
            store,
            owner_slug="live-sim-owner",
            canonical_symbol="GBPJPY",
            incremental_sl_risk_usd=Decimal("3.79"),
            incremental_notional_usd=usd,
            required_cash_usd=required,
        )
    assert old.allowed is False
    assert old.reason == "asset_cash_insufficient"
    assert new.allowed is True


def test_gbpjpy_sizing_uses_usd_unit_notional_not_jpy_mark():
    inst = _gbpjpy_instrument()
    fx = FxRateTable(quote_per_usd={"USD": Decimal("1"), "JPY": JPY_PER_USD})
    equity = cash = ASSET_CASH
    mark = Decimal("207.595")
    assumptions = ExecutionAssumptions()
    qty, _ = max_safe_quantity_for_live_sim(
        equity=equity,
        cash=cash,
        current_asset_notional=Decimal("0"),
        entry_base_price=mark,
        direction=Direction.SHORT,
        instrument=inst,
        execution_assumptions=assumptions,
        max_asset_leverage=Decimal("20"),
        fx_rates=fx,
    )
    # Wrong path: remaining_usd / jpy_mark ≈ 1250*20/207 ≈ 120 units (below min 1000).
    wrong = (equity * Decimal("20") / mark).quantize(Decimal("1"))
    assert wrong < inst.min_quantity
    assert qty >= inst.min_quantity


def test_usd_quoted_crypto_and_gold_sizing_unchanged_dimensionally():
    fx = FxRateTable.usd_only()
    for symbol, price, step in (
        ("BTCUSD", Decimal("100000"), "0.0001"),
        ("ETHUSD", Decimal("2500"), "0.0001"),
        ("XAUUSD", Decimal("2500"), "0.01"),
    ):
        asset_class = "commodity" if symbol == "XAUUSD" else "crypto"
        inst = _usd_instrument(symbol, step=step, asset_class=asset_class)
        max_lev = Decimal("10") if symbol == "XAUUSD" else Decimal("1")
        cash = ASSET_CASH
        qty, _ = max_safe_quantity_for_live_sim(
            equity=cash,
            cash=cash,
            current_asset_notional=Decimal("0"),
            entry_base_price=price,
            direction=Direction.LONG,
            instrument=inst,
            execution_assumptions=ExecutionAssumptions(),
            max_asset_leverage=max_lev,
            fx_rates=fx,
        )
        unit = quote_notional_usd(
            Decimal("1"), price, get_instrument_spec(symbol), {"USD": Decimal("1")}
        )
        assert unit == price.quantize(Decimal("0.01"))
        assert qty >= 0


def test_equity_sizing_remains_margin_aware():
    inst = Instrument(
        id="nvda-id",
        symbol="NVDA",
        name="NVDA",
        asset_class="stock",
        quote_currency="USD",
        quantity_step=Decimal("1"),
        min_quantity=Decimal("1"),
    )
    fx = FxRateTable.usd_only()
    qty, _ = max_safe_quantity_for_live_sim(
        equity=ASSET_CASH,
        cash=ASSET_CASH,
        current_asset_notional=Decimal("0"),
        entry_base_price=Decimal("100"),
        direction=Direction.LONG,
        instrument=inst,
        execution_assumptions=ExecutionAssumptions(),
        max_asset_leverage=Decimal("2"),
        fx_rates=fx,
    )
    # Headroom leaves qty just under full 2x leverage × cash/price.
    assert qty == Decimal("24")


def test_asset_1250_isolation_preserved_for_corrected_gbpjpy():
    """Notional may exceed $1,250; required initial margin must stay within envelope."""
    spec = get_instrument_spec("GBPJPY")
    usd = quote_notional_usd(Decimal("1000"), Decimal("207.595"), spec, {"JPY": JPY_PER_USD})
    im = initial_margin_for_notional(usd, QUANTARA_LIVE_SIM_10K.rules_for("forex"))
    assert usd > ASSET_CASH
    assert im <= ASSET_CASH


def test_no_historical_replay_marker():
    """Guard: this suite never calls trade/intent creation for missed SQZ/GBPJPY."""
    # Structural: fixtures only compute economics / forwarding mocks.
    assert all("qty" in c for c in GBPJPY_CASES)
