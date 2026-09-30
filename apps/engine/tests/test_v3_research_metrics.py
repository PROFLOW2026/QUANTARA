"""V3 research metrics helpers."""

from quantara_engine.research.v3.metrics import profit_factor, trade_metrics


def test_trade_metrics_expectancy_and_pf():
    pnls = [10.0, -5.0, 10.0, -5.0]
    m = trade_metrics(pnls, risk_usd=[10, 10, 10, 10])
    assert m["trades"] == 4
    assert m["pf"] == 2.0
    assert m["expectancy"] == 2.5
    assert m["expectancy_r"] == 0.25


def test_profit_factor_no_losses():
    assert profit_factor([1.0, 2.0]) is None
