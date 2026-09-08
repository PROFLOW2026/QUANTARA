import {
  api,
  type CompetitionLeaderboardRow,
  type CompetitionPortfolioSummary,
  type CompetitionResponse,
  type PortfolioListItem,
  type TimeframeComparisonRow,
} from "./api-client";

const COMPETITION_EXPERIMENT_ID = "00000000-0000-0000-0000-000000000200";
const COMPETITION_TOTAL_INITIAL = 30_000;
const PORTFOLIO_INITIAL = 2_000;
const TIMEFRAME_ORDER = ["1h", "15m", "5m"] as const;

/** Full competition payload (heavy — may exceed proxy timeout). */
export async function loadCompetitionFull(): Promise<CompetitionResponse> {
  return api.getCompetition();
}

function toSummary(item: PortfolioListItem): CompetitionPortfolioSummary {
  const unrealizedPnl = Number(item.unrealized_pnl ?? 0);
  const totalPnl = item.equity - item.initial_capital;
  const returnPct =
    item.initial_capital > 0 ? (totalPnl / item.initial_capital) * 100 : 0;
  const openPositionsCount = item.open_positions_count ?? 0;

  return {
    id: item.id,
    name: item.name,
    timeframe: item.timeframe,
    timeframe_he: item.timeframe_he,
    risk_slug: item.risk_slug ?? "",
    risk_name_he: item.name,
    risk_per_trade_pct: item.risk_per_trade_pct ?? 0,
    initial_capital: item.initial_capital,
    equity: item.equity,
    balance: item.balance ?? item.equity,
    realized_pnl: totalPnl - unrealizedPnl,
    unrealized_pnl: unrealizedPnl,
    total_pnl: totalPnl,
    return_pct: returnPct,
    trades_count: item.closed_trades_count ?? 0,
    win_rate: null,
    max_drawdown_pct: 0,
    exposure_pct: 0,
    open_position: item.open_position ?? openPositionsCount > 0,
    open_positions_count: openPositionsCount,
    open_direction: item.open_direction ?? null,
    status: "active",
    strategy_instance_id: "",
    sort_order: item.sort_order ?? 99,
    target_risk_pct: item.risk_per_trade_pct,
  };
}

function buildLeaderboard(
  portfolios: CompetitionPortfolioSummary[]
): CompetitionLeaderboardRow[] {
  return [...portfolios]
    .sort((a, b) => b.return_pct - a.return_pct)
    .map((row, index) => ({
      rank: index + 1,
      portfolio_id: row.id,
      name: row.name,
      timeframe: row.timeframe,
      timeframe_he: row.timeframe_he,
      return_pct: row.return_pct,
      max_drawdown_pct: row.max_drawdown_pct,
      realized_pnl: row.realized_pnl,
      trades_count: row.trades_count,
      win_rate: row.win_rate,
      return_vs_drawdown: null,
    }));
}

function buildTimeframeComparison(
  portfolios: CompetitionPortfolioSummary[]
): TimeframeComparisonRow[] {
  const rows: TimeframeComparisonRow[] = [];
  for (const timeframe of TIMEFRAME_ORDER) {
    const group = portfolios.filter((p) => p.timeframe === timeframe);
    if (!group.length) continue;
    const returns = group.map((p) => p.return_pct);
    const drawdowns = group.map((p) => p.max_drawdown_pct);
    rows.push({
      timeframe,
      timeframe_he: group[0].timeframe_he ?? timeframe,
      title_he:
        timeframe === "1h"
          ? "מסחר לפי שעה"
          : timeframe === "15m"
            ? "מסחר לפי 15 דקות"
            : "מסחר לפי 5 דקות",
      portfolio_count: group.length,
      average_return_pct:
        returns.reduce((sum, value) => sum + value, 0) / group.length,
      best_return_pct: Math.max(...returns),
      total_trades: group.reduce((sum, p) => sum + p.trades_count, 0),
      average_drawdown_pct:
        drawdowns.reduce((sum, value) => sum + value, 0) / group.length,
      max_drawdown_pct: Math.max(...drawdowns),
      combined_equity: group.reduce((sum, p) => sum + p.equity, 0),
    });
  }
  return rows;
}
export async function loadCompetitionView(): Promise<CompetitionResponse> {
  const items = await api.getPortfolios();
  const competitionItems = items
    .filter((item) => item.kind === "competition")
    .sort((a, b) => (a.sort_order ?? 99) - (b.sort_order ?? 99));

  if (!competitionItems.length) {
    return { active: false };
  }

  const portfolios = competitionItems.map(toSummary);
  const combinedEquity = portfolios.reduce((sum, row) => sum + row.equity, 0);
  const leaderboard = buildLeaderboard(portfolios);
  const leader = leaderboard[0]
    ? {
        portfolio_id: leaderboard[0].portfolio_id,
        name: leaderboard[0].name,
        return_pct: leaderboard[0].return_pct,
        timeframe: leaderboard[0].timeframe,
        timeframe_he: leaderboard[0].timeframe_he,
      }
    : undefined;

  const timeframeComparison = buildTimeframeComparison(portfolios);
  const leadingTimeframe = timeframeComparison.length
    ? [...timeframeComparison].sort(
        (a, b) => b.average_return_pct - a.average_return_pct
      )[0]
    : null;

  return {
    active: true,
    experiment: {
      id: COMPETITION_EXPERIMENT_ID,
      name: "השוואת 15 תיקים אוטומטיים",
      subtitle: "3 טווחי זמן × 5 רמות סיכון",
      started_at: null,
      status: "running",
      strategy_name: "Gold Trend Pullback",
      strategy_version: "1.0.0",
      instrument: "XAU/USD",
      timeframe: "multi",
      total_initial_capital: COMPETITION_TOTAL_INITIAL,
      portfolio_initial_capital: PORTFOLIO_INITIAL,
      portfolio_count: portfolios.length,
    },
    combined: {
      initial_equity: COMPETITION_TOTAL_INITIAL,
      current_equity: combinedEquity,
      combined_pnl: combinedEquity - COMPETITION_TOTAL_INITIAL,
      open_positions_total: portfolios.reduce(
        (sum, row) => sum + (row.open_positions_count ?? 0),
        0
      ),
    },
    leader,
    leading_timeframe: leadingTimeframe,
    portfolios,
    timeframe_groups: TIMEFRAME_ORDER.map((timeframe) => ({
      timeframe,
      timeframe_he:
        portfolios.find((p) => p.timeframe === timeframe)?.timeframe_he ?? timeframe,
      title_he:
        timeframe === "1h"
          ? "מסחר לפי שעה"
          : timeframe === "15m"
            ? "מסחר לפי 15 דקות"
            : "מסחר לפי 5 דקות",
      portfolios: portfolios.filter((p) => p.timeframe === timeframe),
    })).filter((group) => group.portfolios.length > 0),
    leaderboard,
    leaderboards_by_timeframe: Object.fromEntries(
      TIMEFRAME_ORDER.map((timeframe) => [
        timeframe,
        buildLeaderboard(portfolios.filter((p) => p.timeframe === timeframe)),
      ])
    ),
    timeframe_comparison: timeframeComparison,
    equity_curves: {},
    activity: [],
  };
}
