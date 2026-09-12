"use client";

import { Fragment, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { DecisionTypeBadge } from "@/components/trading/DecisionTypeBadge";
import {
  isEntrySignalDecision,
  translateDecisionMessage,
  translateRobotStrategyLabel,
  translateRiskProfile,
  translateTimeframe,
} from "@/lib/display-text";
import {
  groupResearchDecisions,
  isMultiTierDecisionGroup,
  type DecisionGroup,
} from "@/lib/decision-grouping";
import type { Decision } from "@/lib/api-client";
import { t } from "@/lib/i18n";
import { formatDateTime, formatRelativeTime } from "@/lib/utils";

function tierCountLabel(count: number) {
  return t("decisions.portfolio_tier_count", { count });
}

function TierDrilldown({ members }: { members: Decision[] }) {
  return (
    <div className="mt-2 space-y-2 border-t border-border/50 pt-2">
      {members.map((member) => (
        <div
          key={member.id}
          className="rounded-md border border-border/40 bg-surface-inner px-2.5 py-2 text-xs"
        >
          <p className="font-medium">
            {member.risk_name_he ??
              (member.risk_slug ? translateRiskProfile(member.risk_slug) : member.portfolio_name ?? "—")}
          </p>
          {member.portfolio_name ? (
            <p className="mt-0.5 text-muted">{member.portfolio_name}</p>
          ) : null}
          <p className="mt-1 text-muted">{formatDateTime(member.timestamp)}</p>
          <p className="mt-1 whitespace-pre-wrap break-words leading-relaxed">
            {translateDecisionMessage(member)}
          </p>
        </div>
      ))}
    </div>
  );
}

function ExpandToggle({
  expanded,
  onToggle,
  count,
}: {
  expanded: boolean;
  onToggle: () => void;
  count: number;
}) {
  return (
    <button
      type="button"
      onClick={onToggle}
      className="inline-flex items-center gap-1 rounded-sm text-xs text-accent hover:underline focus:outline-none focus-visible:ring-2 focus-visible:ring-focus/45"
      aria-expanded={expanded}
    >
      <Badge variant="outline">{tierCountLabel(count)}</Badge>
      <span aria-hidden="true">{expanded ? "▾" : "▸"}</span>
    </button>
  );
}

export function useDecisionGroups(decisions: Decision[], enabled: boolean) {
  if (!enabled) {
    return decisions.map(
      (decision): DecisionGroup => ({
        key: decision.id,
        representative: decision,
        members: [decision],
      })
    );
  }
  return groupResearchDecisions(decisions);
}

export function DecisionGroupMobileList({
  groups,
  timeframe,
  freshnessBadge,
  yesNo,
}: {
  groups: DecisionGroup[];
  timeframe: string;
  freshnessBadge: (fresh?: boolean) => React.ReactNode;
  yesNo: (value: boolean | undefined) => string;
}) {
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});

  return (
    <div className="space-y-3 md:hidden">
      {groups.map((group) => {
        const row = group.representative;
        const multi = isMultiTierDecisionGroup(group);
        const entrySignal =
          row.entry_signal ?? (isEntrySignalDecision(row.decision_type) || row.trade_opened);
        const isOpen = Boolean(expanded[group.key]);

        return (
          <div
            key={group.key}
            className="rounded-md border border-border/60 p-3 text-sm"
          >
            <p className="text-xs text-muted">
              {translateRobotStrategyLabel(row.robot_label, row.strategy_name, row.strategy_slug)}
            </p>
            <p className="mt-1 font-medium">{row.instrument ?? "—"}</p>
            <div className="mt-2 flex flex-wrap items-center gap-2">
              <DecisionTypeBadge type={row.decision_type} metadata={row.metadata} />
              {freshnessBadge(row.fresh)}
              {multi ? (
                <ExpandToggle
                  expanded={isOpen}
                  count={group.members.length}
                  onToggle={() =>
                    setExpanded((prev) => ({ ...prev, [group.key]: !prev[group.key] }))
                  }
                />
              ) : null}
            </div>
            <p className="mt-2 text-xs text-muted">
              {formatRelativeTime(row.timestamp)} · {translateTimeframe(row.timeframe ?? timeframe)}
            </p>
            <p className="mt-2 whitespace-pre-wrap break-words text-sm leading-relaxed">
              {translateDecisionMessage(row)}
            </p>
            <div className="mt-2 grid grid-cols-2 gap-2 text-xs">
              <p>
                {t("home.entry_signal")}: {yesNo(entrySignal)}
              </p>
              <p>
                {t("home.position_open_now")}: {yesNo(row.position_open)}
              </p>
            </div>
            {multi && isOpen ? <TierDrilldown members={group.members} /> : null}
          </div>
        );
      })}
    </div>
  );
}

export function DecisionGroupDesktopTable({
  groups,
  timeframe,
  freshnessBadge,
  yesNo,
}: {
  groups: DecisionGroup[];
  timeframe: string;
  freshnessBadge: (fresh?: boolean) => React.ReactNode;
  yesNo: (value: boolean | undefined) => string;
}) {
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});

  return (
    <table className="hidden w-full table-fixed text-sm md:table">
      <colgroup>
        <col className="w-[12%]" />
        <col className="w-[7%]" />
        <col className="w-[6%]" />
        <col className="w-[10%]" />
        <col className="w-[10%]" />
        <col className="w-[7%]" />
        <col className="w-[28%]" />
        <col className="w-[7%]" />
        <col className="w-[7%]" />
        <col className="w-[6%]" />
      </colgroup>
      <thead>
        <tr className="border-b border-border text-muted">
          <th className="py-2 pe-2 text-right">{t("home.robot_strategy")}</th>
          <th className="py-2 px-1 text-right">{t("home.asset_symbol")}</th>
          <th className="py-2 px-1 text-right">{t("home.timeframe")}</th>
          <th className="py-2 px-1 text-right">{t("home.latest_decision")}</th>
          <th className="py-2 px-1 text-right">{t("home.decision_time")}</th>
          <th className="py-2 px-1 text-right">{t("home.data_freshness")}</th>
          <th className="py-2 px-2 text-right">{t("home.reason")}</th>
          <th className="py-2 px-1 text-right">{t("decisions.portfolios")}</th>
          <th className="py-2 px-1 text-right">{t("home.entry_signal")}</th>
          <th className="py-2 ps-1 text-right">{t("home.position_open_now")}</th>
        </tr>
      </thead>
      <tbody>
        {groups.map((group) => {
          const row = group.representative;
          const multi = isMultiTierDecisionGroup(group);
          const entrySignal =
            row.entry_signal ?? (isEntrySignalDecision(row.decision_type) || row.trade_opened);
          const isOpen = Boolean(expanded[group.key]);

          return (
            <Fragment key={group.key}>
              <tr className="border-b border-border/50 align-top">
                <td className="py-2 pe-2 text-xs text-muted truncate">
                  {translateRobotStrategyLabel(row.robot_label, row.strategy_name, row.strategy_slug)}
                </td>
                <td className="py-2 px-1 font-medium">{row.instrument ?? "—"}</td>
                <td className="py-2 px-1">{translateTimeframe(row.timeframe ?? timeframe)}</td>
                <td className="py-2 px-1">
                  <DecisionTypeBadge type={row.decision_type} metadata={row.metadata} />
                </td>
                <td className="py-2 px-1 text-muted">{formatRelativeTime(row.timestamp)}</td>
                <td className="py-2 px-1">{freshnessBadge(row.fresh)}</td>
                <td className="py-2 px-2 whitespace-pre-wrap break-words leading-relaxed">
                  {translateDecisionMessage(row)}
                </td>
                <td className="py-2 px-1">
                  {multi ? (
                    <ExpandToggle
                      expanded={isOpen}
                      count={group.members.length}
                      onToggle={() =>
                        setExpanded((prev) => ({ ...prev, [group.key]: !prev[group.key] }))
                      }
                    />
                  ) : (
                    <span className="text-xs text-muted">1</span>
                  )}
                </td>
                <td className="py-2 px-1">{yesNo(entrySignal)}</td>
                <td className="py-2 ps-1">{yesNo(row.position_open)}</td>
              </tr>
              {multi && isOpen ? (
                <tr className="border-b border-border/40 bg-surface-inner/40">
                  <td colSpan={10} className="px-3 py-2">
                    <TierDrilldown members={group.members} />
                  </td>
                </tr>
              ) : null}
            </Fragment>
          );
        })}
      </tbody>
    </table>
  );
}
