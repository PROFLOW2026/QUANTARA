"use client";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import {
  DecisionGroupDesktopTable,
  DecisionGroupMobileList,
  useDecisionGroups,
} from "@/components/trading/DecisionGroupRows";
import { isEntrySignalDecision, translateTimeframe } from "@/lib/display-text";
import { t } from "@/lib/i18n";
import type { Decision } from "@/lib/api-client";

function freshnessBadge(fresh?: boolean) {
  if (fresh == null) return null;
  return (
    <Badge variant={fresh ? "success" : "warning"}>
      {fresh ? t("home.decision_fresh") : t("home.decision_stale")}
    </Badge>
  );
}

function yesNo(value: boolean | undefined) {
  return value ? t("common.yes") : t("common.no");
}

export function LatestDecisionsPanel({
  decisions,
  timeframe,
}: {
  decisions: Decision[];
  timeframe: string;
}) {
  const groups = useDecisionGroups(decisions, true);

  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("home.latest_decisions_title")}</CardTitle>
        <p className="text-xs text-muted">
          {t("home.latest_decisions_hint")} · {translateTimeframe(timeframe)}
        </p>
      </CardHeader>
      <CardContent>
        {!groups.length ? (
          <p className="text-sm text-muted">{t("common.no_data")}</p>
        ) : (
          <>
            <DecisionGroupMobileList
              groups={groups}
              timeframe={timeframe}
              freshnessBadge={freshnessBadge}
              yesNo={yesNo}
            />
            <DecisionGroupDesktopTable
              groups={groups}
              timeframe={timeframe}
              freshnessBadge={freshnessBadge}
              yesNo={yesNo}
            />
          </>
        )}
      </CardContent>
    </Card>
  );
}
