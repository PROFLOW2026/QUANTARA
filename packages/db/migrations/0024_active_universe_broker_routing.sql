-- Global broker routing for Phase 2 active-universe symbols (dynamic slot rotation).

BEGIN;

INSERT INTO broker_routing_rules (canonical_symbol, direction, execution_product, broker_vendor, priority)
SELECT v.sym, v.dir::direction, v.product::execution_product, v.vendor::broker_vendor, 10
FROM (
  VALUES
    ('AAPL', 'long', 'equity_cash', 'IBKR'),
    ('AAPL', 'short', 'equity_margin_short', 'IBKR'),
    ('MSFT', 'long', 'equity_cash', 'IBKR'),
    ('MSFT', 'short', 'equity_margin_short', 'IBKR'),
    ('META', 'long', 'equity_cash', 'IBKR'),
    ('META', 'short', 'equity_margin_short', 'IBKR'),
    ('AMZN', 'long', 'equity_cash', 'IBKR'),
    ('AMZN', 'short', 'equity_margin_short', 'IBKR'),
    ('GOOGL', 'long', 'equity_cash', 'IBKR'),
    ('GOOGL', 'short', 'equity_margin_short', 'IBKR'),
    ('PLTR', 'long', 'equity_cash', 'IBKR'),
    ('PLTR', 'short', 'equity_margin_short', 'IBKR'),
    ('MSTR', 'long', 'equity_cash', 'IBKR'),
    ('MSTR', 'short', 'equity_margin_short', 'IBKR'),
    ('SPY', 'long', 'equity_cash', 'IBKR'),
    ('SPY', 'short', 'equity_margin_short', 'IBKR'),
    ('QQQ', 'long', 'equity_cash', 'IBKR'),
    ('QQQ', 'short', 'equity_margin_short', 'IBKR'),
    ('DIA', 'long', 'equity_cash', 'IBKR'),
    ('DIA', 'short', 'equity_margin_short', 'IBKR'),
    ('IWM', 'long', 'equity_cash', 'IBKR'),
    ('IWM', 'short', 'equity_margin_short', 'IBKR'),
    ('EURUSD', 'long', 'margin_fx', 'IBKR'),
    ('EURUSD', 'short', 'margin_fx', 'IBKR'),
    ('USDJPY', 'long', 'margin_fx', 'IBKR'),
    ('USDJPY', 'short', 'margin_fx', 'IBKR'),
    ('GBPUSD', 'long', 'margin_fx', 'IBKR'),
    ('GBPUSD', 'short', 'margin_fx', 'IBKR'),
    ('AUDUSD', 'long', 'margin_fx', 'IBKR'),
    ('AUDUSD', 'short', 'margin_fx', 'IBKR'),
    ('SOLUSD', 'long', 'crypto_derivative', 'KRAKEN'),
    ('SOLUSD', 'short', 'crypto_derivative', 'KRAKEN'),
    ('XRPUSD', 'long', 'crypto_derivative', 'KRAKEN'),
    ('XRPUSD', 'short', 'crypto_derivative', 'KRAKEN')
) AS v(sym, dir, product, vendor)
WHERE NOT EXISTS (
  SELECT 1
  FROM broker_routing_rules b
  WHERE b.owner_portfolio_id IS NULL
    AND b.canonical_symbol = v.sym
    AND b.direction::text = v.dir
);

COMMIT;
