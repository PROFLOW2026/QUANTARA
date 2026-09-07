import type { BacktestMetrics, Trade } from "@/lib/api-client";
import type { EquityPoint } from "@/components/charts/EquityCurveChart";
import type { DrawdownPoint } from "@/components/charts/DrawdownChart";

export function getBacktestEquityCurve(
  metrics: BacktestMetrics | undefined,
  trades: Trade[] | null
): EquityPoint[] {
  if (metrics?.equity_curve?.length) {
    return metrics.equity_curve;
  }

  const initial = metrics?.initial_capital;
  if (initial == null || !trades?.length) return [];

  const sorted = [...trades].sort(
    (a, b) => new Date(a.close_time).getTime() - new Date(b.close_time).getTime()
  );

  let equity = initial;
  const curve: EquityPoint[] = [
    { date: sorted[0].close_time, equity: initial },
  ];

  for (const tr of sorted) {
    equity += tr.pnl;
    curve.push({ date: tr.close_time, equity });
  }

  return curve;
}

export function getBacktestDrawdownCurve(
  metrics: BacktestMetrics | undefined,
  equityCurve: EquityPoint[]
): DrawdownPoint[] {
  if (metrics?.drawdown_curve?.length) {
    return metrics.drawdown_curve;
  }

  if (equityCurve.length < 2) return [];

  let peak = equityCurve[0].equity;
  return equityCurve.map((point) => {
    peak = Math.max(peak, point.equity);
    const drawdown_pct = peak > 0 ? ((peak - point.equity) / peak) * 100 : 0;
    return { date: point.date, drawdown_pct };
  });
}
