"use client";



import { useEffect, useState } from "react";

import { PageHeader, ErrorBanner } from "@/components/layout/PageHeader";

import { EquityCurveChart } from "@/components/charts/EquityCurveChart";

import { DrawdownChart } from "@/components/charts/DrawdownChart";

import { MetricCard, MetricCardCurrency } from "@/components/trading/MetricCard";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

import { Tabs } from "@/components/ui/tabs";

import {

  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,

} from "@/components/ui/table";

import {

  api,

  ApiError,

  type AnalyticsPortfolio,

  type AnalyticsStrategy,

  type AnalyticsCosts,

  type RegimePerformanceRow,
  type StrategyBreakdownItem,
  type TradingWeekLearningResponse,

} from "@/lib/api-client";

import {
  formatMetricOrInsufficient,
  translateRobotStrategyLabel,
  translateStructureRegime,
  translateVolatilityRegime,
} from "@/lib/display-text";

import { t } from "@/lib/i18n";
import type { ModuleProps } from "@/lib/modal-workspace/types";

import { formatCurrency, formatPercent } from "@/lib/utils";



function hasPortfolioHistory(portfolio: AnalyticsPortfolio | null): boolean {

  return (portfolio?.equity_curve?.length ?? 0) >= 3;

}



export default function AnalyticsModule({ embedded }: ModuleProps) {

  const [tab, setTab] = useState("portfolio");

  const [portfolio, setPortfolio] = useState<AnalyticsPortfolio | null>(null);

  const [strategy, setStrategy] = useState<AnalyticsStrategy | null>(null);

  const [strategyBreakdown, setStrategyBreakdown] = useState<StrategyBreakdownItem[]>([]);

  const [costs, setCosts] = useState<AnalyticsCosts | null>(null);
  const [regimeRows, setRegimeRows] = useState<RegimePerformanceRow[]>([]);
  const [learning, setLearning] = useState<TradingWeekLearningResponse | null>(null);

  const [error, setError] = useState<string | null>(null);

  const [loading, setLoading] = useState(true);



  useEffect(() => {

    async function load() {

      setLoading(true);

      setError(null);

      try {

        if (tab === "portfolio") {

          setPortfolio(await api.getAnalyticsPortfolio());

        } else if (tab === "strategy") {

          const [strategyData, breakdownData] = await Promise.all([
            api.getAnalyticsStrategy(),
            api.getAnalyticsStrategyBreakdown(),
          ]);
          setStrategy(strategyData);
          setStrategyBreakdown(breakdownData.strategies);

        } else if (tab === "regime") {
          const payload = await api.getRegimePerformance();
          setRegimeRows(payload.rows ?? []);
        } else if (tab === "learning") {
          setLearning(await api.getTradingWeekLearning());
        } else {
          setCosts(await api.getAnalyticsCosts());
        }

      } catch (e) {

        setError(e instanceof ApiError ? e.message : t("common.error"));

      } finally {

        setLoading(false);

      }

    }

    load();

  }, [tab]);



  const tabs = [

    { id: "portfolio", label: t("analytics.tab_portfolio") },

    { id: "strategy", label: t("analytics.tab_strategy") },

    { id: "costs", label: t("analytics.tab_costs") },
    { id: "regime", label: t("analytics.tab_regime") },
    { id: "learning", label: t("analytics.tab_learning") },
  ];



  const portfolioHasHistory = hasPortfolioHistory(portfolio);



  return (

    <>

      {!embedded ? <PageHeader titleKey="analytics.title" /> : null}

      <Tabs tabs={tabs} active={tab} onChange={setTab} className="mb-6" />

      {error && <div className="mb-4"><ErrorBanner message={error} /></div>}



      {loading ? (

        <p className="text-muted">{t("common.loading")}</p>

      ) : tab === "portfolio" ? (

        <div className="space-y-4">

          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">

            <MetricCard

              label={t("analytics.total_return")}

              value={

                portfolioHasHistory && portfolio?.summary.total_return_pct != null

                  ? formatPercent(portfolio.summary.total_return_pct)

                  : t("analytics.insufficient_trades")

              }

            />

            <MetricCard

              label={t("analytics.sharpe")}

              hint={t("analytics.sharpe_hint")}

              value={formatMetricOrInsufficient(

                portfolio?.summary.sharpe_ratio,

                (v) => v.toFixed(2),

                portfolioHasHistory ? 3 : 0

              )}

            />

            <MetricCard

              label={t("analytics.max_dd")}

              hint={t("analytics.max_dd_hint")}

              value={

                portfolioHasHistory && portfolio?.summary.max_drawdown_pct != null

                  ? formatPercent(-portfolio.summary.max_drawdown_pct)

                  : t("analytics.insufficient_trades")

              }

            />

            <MetricCard

              label={t("analytics.win_rate")}

              hint={t("analytics.win_rate_hint")}

              value={formatMetricOrInsufficient(

                portfolio?.summary.win_rate,

                (v) => `${v.toFixed(1)}%`,

                portfolioHasHistory ? 3 : 0

              )}

            />

          </div>

          <div className="grid gap-4 lg:grid-cols-2">

            <Card>

              <CardHeader><CardTitle>{t("analytics.equity_curve")}</CardTitle></CardHeader>

              <CardContent>

                <EquityCurveChart data={portfolio?.equity_curve ?? []} />

              </CardContent>

            </Card>

            <Card>

              <CardHeader><CardTitle>{t("analytics.drawdown")}</CardTitle></CardHeader>

              <CardContent>

                <DrawdownChart data={portfolio?.drawdown_curve ?? []} />

              </CardContent>

            </Card>

          </div>

          {portfolio?.monthly_returns?.length ? (

            <Card>

              <CardHeader><CardTitle>{t("analytics.monthly_returns")}</CardTitle></CardHeader>

              <CardContent>

                <Table>

                  <TableHeader>

                    <TableRow>

                      <TableHead>{t("common.date")}</TableHead>

                      <TableHead>{t("backtests.return")}</TableHead>

                    </TableRow>

                  </TableHeader>

                  <TableBody>

                    {portfolio.monthly_returns.map((m) => (

                      <TableRow key={m.month}>

                        <TableCell>{m.month}</TableCell>

                        <TableCell className="font-mono">{formatPercent(m.return_pct)}</TableCell>

                      </TableRow>

                    ))}

                  </TableBody>

                </Table>

              </CardContent>

            </Card>

          ) : null}

        </div>

      ) : tab === "strategy" ? (

        <div className="space-y-4">

          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">

            <MetricCard

              label={t("analytics.win_rate")}

              hint={t("analytics.win_rate_hint")}

              value={formatMetricOrInsufficient(strategy?.win_rate, (v) => `${v.toFixed(1)}%`)}

            />

            <MetricCard

              label={t("analytics.profit_factor")}

              value={formatMetricOrInsufficient(strategy?.profit_factor, (v) => v.toFixed(2))}

            />

            <MetricCard

              label={t("analytics.expectancy")}

              value={formatMetricOrInsufficient(strategy?.expectancy, (v) => v.toFixed(2))}

            />

            <MetricCard

              label={t("analytics.avg_holding")}

              value={strategy?.avg_holding_time ?? t("analytics.insufficient_trades")}

            />

          </div>

          <Card>

            <CardHeader><CardTitle>{t("analytics.long_vs_short")}</CardTitle></CardHeader>

            <CardContent className="flex gap-8">

              <div>

                <p className="text-sm text-muted">{t("analytics.long_pnl")}</p>

                <p className="font-mono text-profit">{formatCurrency(strategy?.long_pnl ?? 0)}</p>

              </div>

              <div>

                <p className="text-sm text-muted">{t("analytics.short_pnl")}</p>

                <p className="font-mono text-loss">{formatCurrency(strategy?.short_pnl ?? 0)}</p>

              </div>

            </CardContent>

          </Card>

          {strategyBreakdown.length > 0 && (
            <Card>
              <CardHeader><CardTitle>{t("analytics.strategy_breakdown")}</CardTitle></CardHeader>
              <CardContent>
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>{t("strategies.robot")}</TableHead>
                      <TableHead>{t("common.name")}</TableHead>
                      <TableHead>{t("strategies.trades_count")}</TableHead>
                      <TableHead>{t("analytics.win_rate")}</TableHead>
                      <TableHead>{t("analytics.profit_factor")}</TableHead>
                      <TableHead>{t("analytics.expectancy")}</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {strategyBreakdown.map((row) => (
                      <TableRow key={row.strategy_slug}>
                        <TableCell>{row.robot_label ?? "—"}</TableCell>
                        <TableCell>{row.strategy_name}</TableCell>
                        <TableCell>{row.trade_count}</TableCell>
                        <TableCell>{row.win_rate?.toFixed(1) ?? "—"}%</TableCell>
                        <TableCell>{row.profit_factor?.toFixed(2) ?? "—"}</TableCell>
                        <TableCell>{row.expectancy?.toFixed(2) ?? "—"}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </CardContent>
            </Card>
          )}

        </div>

      ) : tab === "regime" ? (
        <Card>
          <CardHeader>
            <CardTitle>{t("analytics.regime_performance_title")}</CardTitle>
          </CardHeader>
          <CardContent>
            {regimeRows.length === 0 ? (
              <p className="text-sm text-muted">{t("analytics.regime_performance_empty")}</p>
            ) : (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>{t("strategies.robot")}</TableHead>
                    <TableHead>{t("home.market_regime_structure")}</TableHead>
                    <TableHead>{t("home.market_regime_volatility")}</TableHead>
                    <TableHead>{t("strategies.trades_count")}</TableHead>
                    <TableHead>{t("analytics.win_rate")}</TableHead>
                    <TableHead>{t("analytics.realized_pnl")}</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {regimeRows.map((row, index) => (
                    <TableRow key={`${row.robot_label}-${row.structure_regime}-${index}`}>
                      <TableCell>{translateRobotStrategyLabel(row.robot_label)}</TableCell>
                      <TableCell>{translateStructureRegime(row.structure_regime)}</TableCell>
                      <TableCell>{translateVolatilityRegime(row.volatility_regime)}</TableCell>
                      <TableCell>{row.trades}</TableCell>
                      <TableCell>{formatPercent(row.win_rate)}</TableCell>
                      <TableCell>{formatCurrency(row.realized_pnl)}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
          </CardContent>
        </Card>
      ) : tab === "learning" ? (
        <LearningWeekPanel learning={learning} onActivated={async () => setLearning(await api.getTradingWeekLearning())} />
      ) : (
        <div className="grid gap-4 sm:grid-cols-3">
          <MetricCardCurrency label={t("analytics.total_fees")} value={costs?.total_fees ?? 0} />
          <MetricCardCurrency label={t("analytics.total_slippage")} value={costs?.total_slippage ?? 0} />
          <MetricCardCurrency label={t("analytics.spread_impact")} value={costs?.spread_impact ?? 0} />
        </div>
      )}

    </>

  );

}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" ? (value as Record<string, unknown>) : {};
}

function LearningWeekPanel({
  learning,
  onActivated,
}: {
  learning: TradingWeekLearningResponse | null;
  onActivated: () => Promise<void>;
}) {
  const daily = asRecord(learning?.daily);
  const weekly = asRecord(learning?.weekly);
  const summary = asRecord(daily.summary);
  const baseline = asRecord(daily.baseline);
  const funnel = asRecord(daily.funnel);
  const funnelTotals = asRecord(funnel.totals);
  const pva = asRecord(daily.planned_vs_actual);
  const rsi = asRecord(daily.rsi_shadow);
  const dailyLoss = Array.isArray(daily.daily_loss_shadow) ? daily.daily_loss_shadow : [];
  const regimes = asRecord(daily.market_regime);

  return (
    <div className="space-y-4">
      <p className="text-sm text-muted">{t("analytics.learning_observational")}</p>
      <div className="flex flex-wrap gap-2">
        <button
          type="button"
          className="rounded border px-3 py-1.5 text-sm"
          onClick={async () => {
            await api.activateTradingWeekLearning();
            await onActivated();
          }}
        >
          {t("analytics.learning_activate")}
        </button>
      </div>

      <Card>
        <CardHeader><CardTitle>{t("analytics.learning_baseline")}</CardTitle></CardHeader>
        <CardContent className="text-sm space-y-1">
          <div>שבוע: {String(baseline.week_label ?? "—")}</div>
          <div>הפעלה: {String(baseline.activated_at ?? "—")}</div>
          <div>Commit: {String(baseline.commit_sha ?? "—")}</div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader><CardTitle>{t("analytics.learning_summary_today")}</CardTitle></CardHeader>
        <CardContent>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
            <MetricCard label={t("analytics.learning_trades")} value={String(summary.closed_trades ?? 0)} />
            <MetricCardCurrency label={t("analytics.learning_pnl")} value={Number(summary.realized_pnl ?? 0)} />
            <MetricCard label={t("analytics.learning_open")} value={String(summary.open_positions ?? 0)} />
            <MetricCardCurrency label={t("analytics.learning_exposure")} value={Number(summary.exposure ?? 0)} />
            <MetricCard label={t("analytics.learning_drawdown")} value={`${Number(summary.drawdown_pct ?? 0).toFixed(2)}%`} />
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader><CardTitle>{t("analytics.learning_funnel")}</CardTitle></CardHeader>
        <CardContent className="text-sm grid gap-1 sm:grid-cols-2">
          {Object.entries(funnelTotals).map(([k, v]) => (
            <div key={k} className="flex justify-between gap-2 border-b border-border/40 py-1">
              <span>{k}</span>
              <span>{String(v)}</span>
            </div>
          ))}
        </CardContent>
      </Card>

      <Card>
        <CardHeader><CardTitle>{t("analytics.learning_planned_vs_actual")}</CardTitle></CardHeader>
        <CardContent className="text-sm space-y-1">
          <div>{t("analytics.learning_avg_risk_diff")}: {pva.avg_risk_diff_pct != null ? Number(pva.avg_risk_diff_pct).toFixed(2) : "—"}%</div>
          <div>{t("analytics.learning_overruns")}: {String(pva.overrun_count ?? 0)}</div>
          <div>gap ממוצע: {pva.avg_gap != null ? Number(pva.avg_gap).toFixed(4) : "—"}</div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader><CardTitle>{t("analytics.learning_rsi")}</CardTitle></CardHeader>
        <CardContent>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>וריאנט</TableHead>
                <TableHead>עסקאות</TableHead>
                <TableHead>PnL</TableHead>
                <TableHead>{t("analytics.win_rate")}</TableHead>
                <TableHead>{t("analytics.profit_factor")}</TableHead>
                <TableHead>{t("analytics.drawdown")}</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {(["active_40_60", "directional", "no_rsi_filter"] as const).map((key) => {
                const row = asRecord(rsi[key]);
                return (
                  <TableRow key={key}>
                    <TableCell>{key}</TableCell>
                    <TableCell>{String(row.hypothetical_trades ?? 0)}</TableCell>
                    <TableCell>{formatCurrency(Number(row.net_pnl ?? 0))}</TableCell>
                    <TableCell>{row.win_rate != null ? `${Number(row.win_rate).toFixed(1)}%` : "—"}</TableCell>
                    <TableCell>{row.profit_factor != null ? Number(row.profit_factor).toFixed(2) : "—"}</TableCell>
                    <TableCell>{row.max_drawdown_pct != null ? `${Number(row.max_drawdown_pct).toFixed(2)}%` : "—"}</TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        </CardContent>
      </Card>

      <Card>
        <CardHeader><CardTitle>{t("analytics.learning_daily_loss")}</CardTitle></CardHeader>
        <CardContent>
          {dailyLoss.length === 0 ? (
            <p className="text-sm text-muted">{t("analytics.learning_no_data")}</p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>תיק</TableHead>
                  <TableHead>עצירה?</TableHead>
                  <TableHead>בפועל</TableHead>
                  <TableHead>צל</TableHead>
                  <TableHead>הפרש</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {dailyLoss.slice(0, 20).map((rowUnknown, idx) => {
                  const row = asRecord(rowUnknown);
                  return (
                    <TableRow key={idx}>
                      <TableCell className="font-mono text-xs">{String(row.portfolio_id ?? "").slice(0, 8)}</TableCell>
                      <TableCell>{row.shadow_halt ? "כן" : "לא"}</TableCell>
                      <TableCell>{formatCurrency(Number(row.actual_eod_pnl ?? 0))}</TableCell>
                      <TableCell>{formatCurrency(Number(row.shadow_stop_eod_pnl ?? 0))}</TableCell>
                      <TableCell>{formatCurrency(Number(row.difference ?? 0))}</TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader><CardTitle>{t("analytics.learning_regime")}</CardTitle></CardHeader>
        <CardContent className="text-sm grid gap-1 sm:grid-cols-2">
          {Object.keys(regimes).length === 0 ? (
            <p className="text-muted">{t("analytics.learning_no_data")}</p>
          ) : (
            Object.entries(regimes).map(([sym, raw]) => {
              const r = asRecord(raw);
              return (
                <div key={sym} className="flex justify-between gap-2 border-b border-border/40 py-1">
                  <span>{sym}</span>
                  <span>{String(r.structure_regime ?? "—")} / {String(r.volatility_regime ?? "—")}</span>
                </div>
              );
            })
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader><CardTitle>{t("analytics.learning_week_summary")}</CardTitle></CardHeader>
        <CardContent className="text-sm">
          <pre className="overflow-x-auto whitespace-pre-wrap break-words text-xs opacity-80">
            {JSON.stringify(asRecord(weekly.questions), null, 2)}
          </pre>
        </CardContent>
      </Card>
    </div>
  );
}

