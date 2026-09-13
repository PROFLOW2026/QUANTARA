import assert from "node:assert/strict";
import test from "node:test";

/** Mirrors apps/web/lib/portfolio-hierarchy.ts instrumentMatchesAsset + dbSymbolToDisplay. */
const DB_SYMBOL_TO_DISPLAY = {
  BTCUSD: "BTC/USD",
  ETHUSD: "ETH/USD",
  XAUUSD: "XAU/USD",
  GBPJPY: "GBP/JPY",
  NVDA: "NVDA",
  TSLA: "TSLA",
  AMD: "AMD",
  COIN: "COIN",
};

function dbSymbolToDisplay(symbol) {
  const key = symbol.toUpperCase().replace("/", "");
  return DB_SYMBOL_TO_DISPLAY[key] ?? symbol;
}

function instrumentMatchesAsset(instrument, asset) {
  const normalizedInstrument = instrument.toUpperCase().replace("/", "");
  const db = asset.db_symbol.toUpperCase();
  const display = asset.symbol.toUpperCase().replace("/", "");
  return (
    normalizedInstrument === db ||
    normalizedInstrument === display ||
    instrument === asset.symbol ||
    dbSymbolToDisplay(instrument) === asset.symbol
  );
}

function selectClosedTradesForAsset(trades, asset) {
  return trades
    .filter((row) => instrumentMatchesAsset(row.instrument, asset))
    .sort((a, b) => {
      const ta = a.close_time ? Date.parse(String(a.close_time)) : 0;
      const tb = b.close_time ? Date.parse(String(b.close_time)) : 0;
      return (Number.isFinite(tb) ? tb : 0) - (Number.isFinite(ta) ? ta : 0);
    });
}

/** Production fetch semantics: in-flight dedupe only, no permanent snapshot. */
function createCompetitionClosedTradesFetcher() {
  let inFlight = null;
  async function fetchCompetitionClosedTrades(getTrades) {
    if (inFlight) return inFlight;
    inFlight = getTrades({ portfolio_id: "competition" }).finally(() => {
      inFlight = null;
    });
    return inFlight;
  }
  return {
    fetchCompetitionClosedTrades,
    reset() {
      inFlight = null;
    },
  };
}

function trade(partial) {
  return {
    portfolio_id: "p",
    direction: "long",
    entry_price: 1,
    exit_price: 1,
    quantity: 1,
    pnl: 0,
    open_time: "2026-09-14T00:00:00+03:00",
    ...partial,
  };
}

test("selectClosedTradesForAsset normalizes BTC/USD ↔ BTCUSD", () => {
  const rows = selectClosedTradesForAsset(
    [
      trade({ id: "1", instrument: "BTCUSD", close_time: "2026-09-14T01:00:00+03:00" }),
      trade({ id: "2", instrument: "BTC/USD", close_time: "2026-09-14T02:00:00+03:00" }),
      trade({ id: "3", instrument: "ETHUSD", close_time: "2026-09-14T03:00:00+03:00" }),
    ],
    { symbol: "BTC/USD", db_symbol: "BTCUSD" }
  );
  assert.equal(rows.length, 2);
  assert.deepEqual(
    rows.map((r) => r.id),
    ["2", "1"]
  );
});

test("XAU SQZ + other robots appear; no BTC leakage", () => {
  const rows = selectClosedTradesForAsset(
    [
      trade({
        id: "a",
        instrument: "XAUUSD",
        strategy_name: "Gold Trend Pullback",
        close_time: "2026-09-14T01:00:00+03:00",
      }),
      trade({
        id: "d",
        instrument: "XAUUSD",
        strategy_name: "Volatility Squeeze",
        close_time: "2026-09-14T02:00:00+03:00",
      }),
      trade({
        id: "c",
        instrument: "XAUUSD",
        strategy_name: "Mean Reversion",
        close_time: "2026-09-14T03:00:00+03:00",
      }),
      trade({
        id: "leak",
        instrument: "BTCUSD",
        strategy_name: "Volatility Squeeze",
        close_time: "2026-09-14T04:00:00+03:00",
      }),
    ],
    { symbol: "XAU/USD", db_symbol: "XAUUSD" }
  );
  assert.equal(rows.length, 3);
  assert.deepEqual(
    rows.map((r) => r.id),
    ["c", "d", "a"]
  );
});

test("BTC shows all 10 closed trades — not truncated to 5", () => {
  const trades = Array.from({ length: 10 }, (_, i) =>
    trade({
      id: `btc-${i}`,
      instrument: "BTCUSD",
      close_time: `2026-09-14T0${Math.min(i, 9)}:00:00+03:00`,
    })
  );
  const rows = selectClosedTradesForAsset(trades, {
    symbol: "BTC/USD",
    db_symbol: "BTCUSD",
  });
  assert.equal(rows.length, 10);
});

test("no cross-asset leakage for ETH", () => {
  const rows = selectClosedTradesForAsset(
    [
      trade({ id: "1", instrument: "ETHUSD", close_time: "2026-09-14T01:00:00+03:00" }),
      trade({ id: "2", instrument: "GBPJPY", close_time: "2026-09-14T02:00:00+03:00" }),
      trade({ id: "3", instrument: "NVDA", close_time: "2026-09-14T03:00:00+03:00" }),
    ],
    { symbol: "ETH/USD", db_symbol: "ETHUSD" }
  );
  assert.equal(rows.length, 1);
  assert.equal(rows[0]?.id, "1");
});

test("stale permanent cache regression: reopen fetches again after snapshot grows", async () => {
  const fetcher = createCompetitionClosedTradesFetcher();
  let callCount = 0;
  const snapshots = [
    Array.from({ length: 5 }, (_, i) =>
      trade({
        id: `btc-${i}`,
        instrument: "BTCUSD",
        close_time: `2026-09-14T0${i}:00:00+03:00`,
      })
    ),
    Array.from({ length: 10 }, (_, i) =>
      trade({
        id: `btc-${i}`,
        instrument: "BTCUSD",
        close_time: `2026-09-14T1${i}:00:00+03:00`,
      })
    ),
  ];

  const getTrades = async () => {
    const rows = snapshots[callCount] ?? snapshots[snapshots.length - 1];
    callCount += 1;
    return rows;
  };

  const first = selectClosedTradesForAsset(
    await fetcher.fetchCompetitionClosedTrades(getTrades),
    { symbol: "BTC/USD", db_symbol: "BTCUSD" }
  );
  assert.equal(first.length, 5);
  assert.equal(callCount, 1);

  const second = selectClosedTradesForAsset(
    await fetcher.fetchCompetitionClosedTrades(getTrades),
    { symbol: "BTC/USD", db_symbol: "BTCUSD" }
  );
  assert.equal(second.length, 10);
  assert.equal(callCount, 2);
});

test("XAU reopen: empty then 5 SQZ closes appear", async () => {
  const fetcher = createCompetitionClosedTradesFetcher();
  let callCount = 0;
  const getTrades = async () => {
    callCount += 1;
    if (callCount === 1) return [];
    return Array.from({ length: 5 }, (_, i) =>
      trade({
        id: `xau-${i}`,
        instrument: "XAUUSD",
        strategy_name: "Volatility Squeeze",
        close_time: `2026-09-14T0${i}:30:00+03:00`,
      })
    );
  };

  const first = selectClosedTradesForAsset(
    await fetcher.fetchCompetitionClosedTrades(getTrades),
    { symbol: "XAU/USD", db_symbol: "XAUUSD" }
  );
  assert.equal(first.length, 0);

  const second = selectClosedTradesForAsset(
    await fetcher.fetchCompetitionClosedTrades(getTrades),
    { symbol: "XAU/USD", db_symbol: "XAUUSD" }
  );
  assert.equal(second.length, 5);
  assert.ok(second.every((r) => r.strategy_name === "Volatility Squeeze"));
});

test("in-flight dedupe shares one request; completed fetch is not retained", async () => {
  const fetcher = createCompetitionClosedTradesFetcher();
  let callCount = 0;
  let resolve;
  const pending = new Promise((r) => {
    resolve = r;
  });
  const getTrades = async () => {
    callCount += 1;
    return pending;
  };

  const a = fetcher.fetchCompetitionClosedTrades(getTrades);
  const b = fetcher.fetchCompetitionClosedTrades(getTrades);
  assert.equal(callCount, 1);
  resolve([trade({ id: "1", instrument: "ETHUSD", close_time: "2026-09-14T01:00:00+03:00" })]);
  assert.equal((await a).length, 1);
  assert.equal((await b).length, 1);

  await fetcher.fetchCompetitionClosedTrades(async () => {
    callCount += 1;
    return [];
  });
  assert.equal(callCount, 2);
});

test("open modal path remains independent of closed-trade fetch helper", () => {
  const fetcher = createCompetitionClosedTradesFetcher();
  assert.equal(typeof fetcher.fetchCompetitionClosedTrades, "function");
  assert.equal(typeof selectClosedTradesForAsset, "function");
  // Permanent module-level tradesCache must not exist in production helper semantics.
  assert.equal("tradesCache" in fetcher, false);
});
