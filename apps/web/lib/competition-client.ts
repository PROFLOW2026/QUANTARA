import {
  api,
  type CompetitionLeaderboardRow,
  type CompetitionPortfolioSummary,
  type CompetitionResponse,
  type PortfolioListItem,
  type TimeframeComparisonRow,
} from "./api-client";
import { COMPETITION_NAME_HE, COMPETITION_SUBTITLE_HE } from "./competition-meta";

const COMPETITION_EXPERIMENT_ID = "00000000-0000-0000-0000-000000000400";
const PORTFOLIO_INITIAL = 2_000;
const TIMEFRAME_ORDER = ["1h", "15m", "5m"] as const;
const TIMEFRAME_DISPLAY_ORDER = ["5m", "15m", "1h"] as const;

/** Full competition payload — summary from /competition, curves loaded lazily. */
export async function loadCompetitionFull(): Promise<CompetitionResponse> {
  const summary = await api.getCompetition();
  if (!summary.active || !summary.experiment) {
    return summary;
  }
  try {
    const curvesPayload = await api.getCompetitionEquityCurves();
    return {
      ...summary,
      equity_curves: curvesPayload.equity_curves ?? {},
    };
  } catch {
    return summary;
  }
}

function toSummary(item: PortfolioListItem): CompetitionPortfolioSummary {
  const unrealizedPnl = Number(item.unrealized_pnl ?? 0);
  const realizedPnl =
    item.realized_pnl != null
      ? Number(item.realized_pnl)
      : item.equity - item.initial_capital - unrealizedPnl;
  const totalPnl = realizedPnl + unrealizedPnl;
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
    realized_pnl: realizedPnl,
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
    robot_label: item.robot_label,
    strategy_slug: item.strategy_slug,
    strategy_name: item.strategy_name,
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
    const closedTrades = group.reduce((sum, p) => sum + p.trades_count, 0);
    const realizedPnl = group.reduce((sum, p) => sum + p.realized_pnl, 0);
    const unrealizedPnl = group.reduce((sum, p) => sum + p.unrealized_pnl, 0);
    const openPositions = group.reduce(
      (sum, p) => sum + (p.open_positions_count ?? 0),
      0
    );
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
      total_trades: closedTrades,
      closed_trades: closedTrades,
      realized_pnl: realizedPnl,
      unrealized_pnl: unrealizedPnl,
      total_pnl: realizedPnl + unrealizedPnl,
      open_positions: openPositions,
      average_drawdown_pct:
        drawdowns.reduce((sum, value) => sum + value, 0) / group.length,
      max_drawdown_pct: Math.max(...drawdowns),
      combined_equity: group.reduce((sum, p) => sum + p.equity, 0),
    });
  }
  return rows;
}

function deriveExperimentTotals(items: PortfolioListItem[]) {
  const robotA = items.filter((i) => i.robot_label !== "Robot B");
  const robotB = items.filter((i) => i.robot_label === "Robot B");
  const robotAInitial = robotA.reduce((sum, row) => sum + row.initial_capital, 0);
  const robotBInitial = robotB.reduce((sum, row) => sum + row.initial_capital, 0);
  return {
    robotACount: robotA.length,
    robotBCount: robotB.length,
    totalInitial: robotAInitial + robotBInitial,
    robotAInitial,
    robotBInitial,
  };
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
  const totals = deriveExperimentTotals(competitionItems);
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
  const worst = leaderboard.length
    ? {
        portfolio_id: leaderboard[leaderboard.length - 1].portfolio_id,
        name: leaderboard[leaderboard.length - 1].name,
        return_pct: leaderboard[leaderboard.length - 1].return_pct,
        timeframe: leaderboard[leaderboard.length - 1].timeframe,
        timeframe_he: leaderboard[leaderboard.length - 1].timeframe_he,
      }
    : undefined;

  const timeframeComparison = buildTimeframeComparison(portfolios).sort(
    (a, b) =>
      TIMEFRAME_DISPLAY_ORDER.indexOf(a.timeframe as (typeof TIMEFRAME_DISPLAY_ORDER)[number]) -
      TIMEFRAME_DISPLAY_ORDER.indexOf(b.timeframe as (typeof TIMEFRAME_DISPLAY_ORDER)[number])
  );
  const leadingTimeframe = timeframeComparison.length
    ? [...timeframeComparison].sort(
        (a, b) => b.average_return_pct - a.average_return_pct
      )[0]
    : null;

  return {
    active: true,
    experiment: {
      id: COMPETITION_EXPERIMENT_ID,
      name: COMPETITION_NAME_HE,
      subtitle: COMPETITION_SUBTITLE_HE,
      started_at: null,
      status: "running",
      strategy_name: "Gold Trend Pullback",
      strategy_version: "1.0.0",
      instrument: "multi",
      timeframe: "multi",
      total_initial_capital: totals.totalInitial,
      portfolio_initial_capital: PORTFOLIO_INITIAL,
      portfolio_count: portfolios.length,
      robot_a_portfolio_count: totals.robotACount,
      robot_b_portfolio_count: totals.robotBCount,
      robot_a_initial_capital: totals.robotAInitial,
      robot_b_initial_capital: totals.robotBInitial,
    },
    combined: {
      initial_equity: totals.totalInitial,
      current_equity: combinedEquity,
      combined_pnl: combinedEquity - totals.totalInitial,
      open_positions_total: portfolios.reduce(
        (sum, row) => sum + (row.open_positions_count ?? 0),
        0
      ),
    },
    leader,
    worst_performer: worst,
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
