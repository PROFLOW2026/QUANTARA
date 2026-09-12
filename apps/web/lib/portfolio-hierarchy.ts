import type { CompetitionPortfolioSummary } from "@/lib/api-client";
import { translateTimeframe } from "@/lib/display-text";

export const ASSET_DISPLAY_ORDER = [
  "BTC/USD",
  "ETH/USD",
  "XAU/USD",
  "GBP/JPY",
  "NVDA",
  "TSLA",
  "AMD",
  "COIN",
] as const;

const DB_SYMBOL_TO_DISPLAY: Record<string, string> = {
  BTCUSD: "BTC/USD",
  ETHUSD: "ETH/USD",
  XAUUSD: "XAU/USD",
  GBPJPY: "GBP/JPY",
  NVDA: "NVDA",
  TSLA: "TSLA",
  AMD: "AMD",
  COIN: "COIN",
};

export function dbSymbolToDisplay(symbol: string): string {
  const key = symbol.toUpperCase().replace("/", "");
  return DB_SYMBOL_TO_DISPLAY[key] ?? symbol;
}

/** Extract display asset label from competition portfolio metadata. */
export function extractAssetFromPortfolio(
  portfolio: Pick<CompetitionPortfolioSummary, "name" | "robot_label">
): string {
  const name = portfolio.name ?? "";
  if (name.startsWith("ORB ")) {
    const rest = name.slice(4);
    const raw = rest.split(" — ")[0]?.trim() ?? rest.trim();
    return dbSymbolToDisplay(raw);
  }
  for (const asset of ASSET_DISPLAY_ORDER) {
    if (name.startsWith(asset)) return asset;
  }
  return name.split(" ")[0] ?? "—";
}

export function extractAssetFromPortfolioName(name: string): string {
  return extractAssetFromPortfolio({ name, robot_label: undefined });
}

export interface PortfolioTimeframeGroup {
  timeframe: string;
  timeframeLabel: string;
  portfolioCount: number;
  combinedEquity: number;
  totalPnl: number;
  openPositions: number;
  portfolios: CompetitionPortfolioSummary[];
}

export interface PortfolioRobotGroup {
  robotKey: string;
  robotLabel: string;
  strategyLabel: string;
  portfolioCount: number;
  combinedEquity: number;
  totalPnl: number;
  openPositions: number;
  timeframes: PortfolioTimeframeGroup[];
}

export interface PortfolioAssetGroup {
  asset: string;
  portfolioCount: number;
  combinedEquity: number;
  totalPnl: number;
  openPositions: number;
  robots: PortfolioRobotGroup[];
}

function summarizePortfolios(portfolios: CompetitionPortfolioSummary[]) {
  return {
    portfolioCount: portfolios.length,
    combinedEquity: portfolios.reduce((sum, p) => sum + Number(p.equity ?? 0), 0),
    totalPnl: portfolios.reduce((sum, p) => sum + Number(p.total_pnl ?? 0), 0),
    openPositions: portfolios.reduce(
      (sum, p) => sum + Number(p.open_positions_count ?? (p.open_position ? 1 : 0)),
      0
    ),
  };
}

const TIMEFRAME_SORT: Record<string, number> = { "5m": 1, "15m": 2, "1h": 3 };

export function groupPortfoliosByHierarchy(
  portfolios: CompetitionPortfolioSummary[],
  translateRobot: (robotLabel?: string, strategySlug?: string) => string
): PortfolioAssetGroup[] {
  const byAsset = new Map<string, CompetitionPortfolioSummary[]>();
  for (const portfolio of portfolios) {
    const asset = extractAssetFromPortfolio(portfolio);
    const bucket = byAsset.get(asset) ?? [];
    bucket.push(portfolio);
    byAsset.set(asset, bucket);
  }

  const assetOrder = [
    ...ASSET_DISPLAY_ORDER.filter((asset) => byAsset.has(asset)),
    ...Array.from(byAsset.keys()).filter(
      (asset) => !ASSET_DISPLAY_ORDER.includes(asset as (typeof ASSET_DISPLAY_ORDER)[number])
    ),
  ];

  return assetOrder.map((asset) => {
    const assetPortfolios = byAsset.get(asset) ?? [];
    const byRobot = new Map<string, CompetitionPortfolioSummary[]>();
    for (const portfolio of assetPortfolios) {
      const robotKey =
        portfolio.robot_label === "Robot B" || portfolio.strategy_slug === "opening-range-breakout"
          ? "robot_b"
          : "robot_a";
      const bucket = byRobot.get(robotKey) ?? [];
      bucket.push(portfolio);
      byRobot.set(robotKey, bucket);
    }

    const robots: PortfolioRobotGroup[] = Array.from(byRobot.entries())
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([robotKey, robotPortfolios]) => {
        const sample = robotPortfolios[0];
        const robotSummary = summarizePortfolios(robotPortfolios);
        const byTimeframe = new Map<string, CompetitionPortfolioSummary[]>();
        for (const portfolio of robotPortfolios) {
          const tf = portfolio.timeframe ?? "unknown";
          const bucket = byTimeframe.get(tf) ?? [];
          bucket.push(portfolio);
          byTimeframe.set(tf, bucket);
        }

        const timeframes = Array.from(byTimeframe.entries())
          .sort(
            ([a], [b]) =>
              (TIMEFRAME_SORT[a] ?? 99) - (TIMEFRAME_SORT[b] ?? 99) ||
              a.localeCompare(b)
          )
          .map(([timeframe, tfPortfolios]) => {
            const tfSummary = summarizePortfolios(tfPortfolios);
            const sorted = [...tfPortfolios].sort(
              (a, b) => (a.sort_order ?? 99) - (b.sort_order ?? 99)
            );
            return {
              timeframe,
              timeframeLabel:
                sorted[0]?.timeframe_he ?? translateTimeframe(timeframe),
              ...tfSummary,
              portfolios: sorted,
            };
          });

        return {
          robotKey,
          robotLabel: sample?.robot_label ?? robotKey,
          strategyLabel: translateRobot(sample?.robot_label, sample?.strategy_slug),
          ...robotSummary,
          timeframes,
        };
      });

    return {
      asset,
      ...summarizePortfolios(assetPortfolios),
      robots,
    };
  });
}

export function instrumentMatchesAsset(
  instrument: string,
  asset: { symbol: string; db_symbol: string }
): boolean {
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
