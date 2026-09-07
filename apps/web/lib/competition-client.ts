import {
  api,
  type CompetitionPortfolioSummary,
  type CompetitionResponse,
  type PortfolioListItem,
} from "./api-client";

const COMPETITION_EXPERIMENT_ID = "00000000-0000-0000-0000-000000000100";
const COMPETITION_TOTAL_INITIAL = 10_000;
const PORTFOLIO_INITIAL = 2_000;

const RISK_SORT: Record<string, number> = {
  very_conservative: 1,
  conservative: 2,
  balanced: 3,
  aggressive: 4,
  very_aggressive: 5,
};

function toSummary(item: PortfolioListItem): CompetitionPortfolioSummary {
  const totalPnl = item.equity - item.initial_capital;
  const returnPct =
    item.initial_capital > 0 ? (totalPnl / item.initial_capital) * 100 : 0;

  return {
    id: item.id,
    name: item.name,
    risk_slug: item.risk_slug ?? "",
    risk_name_he: item.name,
    risk_per_trade_pct: item.risk_per_trade_pct ?? 0,
    initial_capital: item.initial_capital,
    equity: item.equity,
    balance: item.equity,
    realized_pnl: 0,
    unrealized_pnl: 0,
    total_pnl: totalPnl,
    return_pct: returnPct,
    trades_count: 0,
    win_rate: null,
    max_drawdown_pct: 0,
    exposure_pct: 0,
    open_position: false,
    open_positions_count: 0,
    status: "active",
    strategy_instance_id: "",
    sort_order: RISK_SORT[item.risk_slug ?? ""] ?? 99,
    target_risk_pct: item.risk_per_trade_pct,
  };
}

/** Fast competition view via /portfolios (avoids heavy /competition payload through tunnel). */
export async function loadCompetitionView(): Promise<CompetitionResponse> {
  const items = await api.getPortfolios();
  const competitionItems = items
    .filter((item) => item.kind === "competition")
    .sort(
      (a, b) =>
        (RISK_SORT[a.risk_slug ?? ""] ?? 99) - (RISK_SORT[b.risk_slug ?? ""] ?? 99)
    );

  if (!competitionItems.length) {
    return { active: false };
  }

  const portfolios = competitionItems.map(toSummary);
  const combinedEquity = portfolios.reduce((sum, row) => sum + row.equity, 0);

  const leaderboard = [...portfolios]
    .sort((a, b) => b.return_pct - a.return_pct)
    .map((row, index) => ({
      rank: index + 1,
      portfolio_id: row.id,
      name: row.name,
      return_pct: row.return_pct,
      max_drawdown_pct: row.max_drawdown_pct,
      realized_pnl: row.realized_pnl,
      trades_count: row.trades_count,
      return_vs_drawdown: null as number | null,
    }));

  const leader = leaderboard[0]
    ? {
        portfolio_id: leaderboard[0].portfolio_id,
        name: leaderboard[0].name,
        return_pct: leaderboard[0].return_pct,
      }
    : undefined;

  return {
    active: true,
    experiment: {
      id: COMPETITION_EXPERIMENT_ID,
      name: "השוואת 5 תיקים אוטומטיים",
      subtitle: "אותה אסטרטגיה ואותם נתוני שוק — רמות סיכון שונות",
      started_at: null,
      status: "running",
      strategy_name: "Gold Trend Pullback",
      strategy_version: "1.0.0",
      instrument: "XAU/USD",
      timeframe: "1h",
      total_initial_capital: COMPETITION_TOTAL_INITIAL,
      portfolio_initial_capital: PORTFOLIO_INITIAL,
      portfolio_count: portfolios.length,
    },
    combined: {
      initial_equity: COMPETITION_TOTAL_INITIAL,
      current_equity: combinedEquity,
      combined_pnl: combinedEquity - COMPETITION_TOTAL_INITIAL,
      open_positions_total: 0,
    },
    leader,
    portfolios,
    leaderboard,
    equity_curves: {},
    activity: [],
  };
}
