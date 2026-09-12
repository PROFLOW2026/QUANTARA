"use client";

import { useMemo, useState } from "react";
import { ModalLink } from "@/components/layout/ModalLink";
import { PnLDisplay } from "@/components/trading/PnLDisplay";
import { Card, CardContent } from "@/components/ui/card";
import type { CompetitionPortfolioSummary } from "@/lib/api-client";
import {
  groupPortfoliosByHierarchy,
  type PortfolioAssetGroup,
} from "@/lib/portfolio-hierarchy";
import {
  translateRiskProfile,
  translateRobotStrategyLabel,
} from "@/lib/display-text";
import { t } from "@/lib/i18n";
import { formatCurrency, formatPercent } from "@/lib/utils";

function portfolioRiskLabel(p: CompetitionPortfolioSummary): string {
  const pct = Number(p.risk_per_trade_pct ?? p.target_risk_pct ?? 0);
  return `${pct.toFixed(2)}%`;
}

function portfolioDirectionLabel(direction?: string | null): string {
  if (!direction) return "—";
  const lower = direction.toLowerCase();
  if (lower === "long") return t("common.long");
  if (lower === "short") return t("common.short");
  return direction;
}

function PortfolioCardCompact({ p }: { p: CompetitionPortfolioSummary }) {
  return (
    <div className="rounded-md border border-border-nested bg-surface-inner p-3 text-xs">
      <p className="text-sm font-medium">
        {translateRiskProfile(p.risk_slug)} — {portfolioRiskLabel(p)}
      </p>
      <div className="mt-2 grid grid-cols-2 gap-x-3 gap-y-1">
        <span className="text-muted">{t("competition.current_equity")}</span>
        <span className="text-left font-mono">{formatCurrency(Number(p.equity ?? 0))}</span>
        <span className="text-muted">{t("competition.pnl")}</span>
        <span className="text-left">
          <PnLDisplay value={Number(p.total_pnl ?? 0)} size="sm" />
        </span>
        <span className="text-muted">{t("competition.return_pct")}</span>
        <span
          className={`text-left ${(p.return_pct ?? 0) >= 0 ? "text-profit" : "text-loss"}`}
        >
          {formatPercent(Number(p.return_pct ?? 0))}
        </span>
        <span className="text-muted">{t("competition.open_position_label")}</span>
        <span className="text-left">
          {p.open_position ? t("competition.open_position_yes") : t("competition.open_position_no")}
        </span>
        <span className="text-muted">{t("competition.closed_trades")}</span>
        <span className="text-left">{p.trades_count ?? 0}</span>
        <span className="text-muted">{t("competition.win_rate")}</span>
        <span className="text-left">
          {p.win_rate != null ? `${Number(p.win_rate).toFixed(1)}%` : "—"}
        </span>
        <span className="text-muted">{t("competition.max_drawdown")}</span>
        <span className="text-left text-loss">
          {formatPercent(-Number(p.max_drawdown_pct ?? 0))}
        </span>
        <span className="text-muted">{t("competition.exposure")}</span>
        <span className="text-left">{Number(p.exposure_pct ?? 0).toFixed(1)}%</span>
        {p.open_position ? (
          <>
            <span className="text-muted">{t("competition.open_direction")}</span>
            <span className="text-left">{portfolioDirectionLabel(p.open_direction)}</span>
            <span className="text-muted">{t("competition.unrealized_pnl")}</span>
            <span className="text-left">
              <PnLDisplay value={Number(p.unrealized_pnl ?? 0)} size="sm" />
            </span>
          </>
        ) : null}
      </div>
      <ModalLink
        href={`/portfolio?portfolio_id=${encodeURIComponent(p.id)}`}
        className="mt-2 inline-block text-xs text-accent hover:underline"
      >
        {t("competition.view_portfolio")} →
      </ModalLink>
    </div>
  );
}

function GroupSummary({
  portfolioCount,
  combinedEquity,
  totalPnl,
  openPositions,
}: Pick<
  PortfolioAssetGroup,
  "portfolioCount" | "combinedEquity" | "totalPnl" | "openPositions"
>) {
  return (
    <span className="text-xs text-muted">
      {t("competition.group_portfolios", { count: portfolioCount })} ·{" "}
      {t("competition.group_equity", { value: formatCurrency(combinedEquity) })} ·{" "}
      {t("competition.group_pnl")}{" "}
      <PnLDisplay value={totalPnl} size="sm" /> ·{" "}
      {t("competition.group_open_positions", { count: openPositions })}
    </span>
  );
}

function AccordionToggle({
  expanded,
  onToggle,
  label,
  summary,
}: {
  expanded: boolean;
  onToggle: () => void;
  label: string;
  summary?: React.ReactNode;
}) {
  return (
    <button
      type="button"
      className="flex w-full items-start justify-between gap-3 rounded-md border border-border/60 bg-surface-inner px-3 py-3 text-right hover:bg-surface-inner-hover-soft"
      onClick={onToggle}
      aria-expanded={expanded}
    >
      <span className="min-w-0 flex-1">
        <span className="block font-medium">{label}</span>
        {summary ? <span className="mt-1 block">{summary}</span> : null}
      </span>
      <span className="shrink-0 text-xs text-accent">
        {expanded ? t("competition.collapse_group") : t("competition.expand_group")}
      </span>
    </button>
  );
}

export function PortfolioHierarchyAccordion({
  portfolios,
}: {
  portfolios: CompetitionPortfolioSummary[];
}) {
  const groups = useMemo(
    () =>
      groupPortfoliosByHierarchy(portfolios, (robotLabel, strategySlug) =>
        translateRobotStrategyLabel(robotLabel, undefined, strategySlug)
      ),
    [portfolios]
  );

  const [expandedAssets, setExpandedAssets] = useState<Set<string>>(new Set());
  const [expandedRobots, setExpandedRobots] = useState<Set<string>>(new Set());
  const [expandedTimeframes, setExpandedTimeframes] = useState<Set<string>>(new Set());

  const toggle = (
    setter: React.Dispatch<React.SetStateAction<Set<string>>>,
    key: string
  ) => {
    setter((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  };

  return (
    <Card className="mb-4">
      <CardContent className="space-y-3 pt-6">
        <p className="text-sm text-muted">{t("competition.hierarchy_hint")}</p>
        {groups.map((assetGroup) => {
          const assetKey = assetGroup.asset;
          const assetOpen = expandedAssets.has(assetKey);
          return (
            <section key={assetKey} className="space-y-2">
              <AccordionToggle
                expanded={assetOpen}
                onToggle={() => toggle(setExpandedAssets, assetKey)}
                label={assetGroup.asset}
                summary={
                  <GroupSummary
                    portfolioCount={assetGroup.portfolioCount}
                    combinedEquity={assetGroup.combinedEquity}
                    totalPnl={assetGroup.totalPnl}
                    openPositions={assetGroup.openPositions}
                  />
                }
              />
              {assetOpen ? (
                <div className="space-y-2 border-r-2 border-border/40 pr-3 mr-1">
                  {assetGroup.robots.map((robotGroup) => {
                    const robotKey = `${assetKey}:${robotGroup.robotKey}`;
                    const robotOpen = expandedRobots.has(robotKey);
                    return (
                      <section key={robotKey} className="space-y-2">
                        <AccordionToggle
                          expanded={robotOpen}
                          onToggle={() => toggle(setExpandedRobots, robotKey)}
                          label={robotGroup.strategyLabel}
                          summary={
                            <GroupSummary
                              portfolioCount={robotGroup.portfolioCount}
                              combinedEquity={robotGroup.combinedEquity}
                              totalPnl={robotGroup.totalPnl}
                              openPositions={robotGroup.openPositions}
                            />
                          }
                        />
                        {robotOpen ? (
                          <div className="space-y-2 border-r-2 border-border/30 pr-3 mr-1">
                            {robotGroup.timeframes.map((tfGroup) => {
                              const tfKey = `${robotKey}:${tfGroup.timeframe}`;
                              const tfOpen = expandedTimeframes.has(tfKey);
                              return (
                                <section key={tfKey} className="space-y-2">
                                  <AccordionToggle
                                    expanded={tfOpen}
                                    onToggle={() => toggle(setExpandedTimeframes, tfKey)}
                                    label={tfGroup.timeframeLabel}
                                    summary={
                                      <GroupSummary
                                        portfolioCount={tfGroup.portfolioCount}
                                        combinedEquity={tfGroup.combinedEquity}
                                        totalPnl={tfGroup.totalPnl}
                                        openPositions={tfGroup.openPositions}
                                      />
                                    }
                                  />
                                  {tfOpen ? (
                                    <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
                                      {tfGroup.portfolios.map((portfolio) => (
                                        <PortfolioCardCompact
                                          key={portfolio.id}
                                          p={portfolio}
                                        />
                                      ))}
                                    </div>
                                  ) : null}
                                </section>
                              );
                            })}
                          </div>
                        ) : null}
                      </section>
                    );
                  })}
                </div>
              ) : null}
            </section>
          );
        })}
        <p className="text-xs text-muted">
          {t("competition.total_portfolios_reachable", { count: portfolios.length })}
        </p>
      </CardContent>
    </Card>
  );
}
