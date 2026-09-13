/** Closed-trade drilldown helpers — always reflect current Research competition trades. */

import type { Trade } from "@/lib/api-client";
import { instrumentMatchesAsset } from "@/lib/portfolio-hierarchy";

export type DrilldownAssetRef = { symbol: string; db_symbol: string };

/** Filter to one asset and sort newest closed first. No artificial row limit. */
export function selectClosedTradesForAsset(
  trades: Trade[],
  asset: DrilldownAssetRef
): Trade[] {
  return trades
    .filter((row) => instrumentMatchesAsset(row.instrument, asset))
    .sort((a, b) => {
      const ta = a.close_time ? Date.parse(String(a.close_time)) : 0;
      const tb = b.close_time ? Date.parse(String(b.close_time)) : 0;
      return (Number.isFinite(tb) ? tb : 0) - (Number.isFinite(ta) ? ta : 0);
    });
}

type GetTradesFn = (params?: Record<string, string>) => Promise<Trade[]>;

/** In-flight dedupe only — never retain a completed snapshot across modal opens. */
let tradesInFlight: Promise<Trade[]> | null = null;

export async function fetchCompetitionClosedTrades(
  getTrades: GetTradesFn
): Promise<Trade[]> {
  if (tradesInFlight) return tradesInFlight;
  tradesInFlight = getTrades({ portfolio_id: "competition" }).finally(() => {
    tradesInFlight = null;
  });
  return tradesInFlight;
}

/** Test hook — clears in-flight promise between scenarios. */
export function resetCompetitionClosedTradesFetchForTests(): void {
  tradesInFlight = null;
}
