import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { DecisionTypeBadge } from "@/components/trading/DecisionTypeBadge";
import {
  translateDecisionMessage,
  translateExecution,
  translateSignalReason,
  translateTimeframe,
} from "@/lib/display-text";
import { formatPrice, formatRelativeTime } from "@/lib/utils";
import { t } from "@/lib/i18n";
import type { Decision } from "@/lib/api-client";

interface SignalCardProps {
  decision: Decision | null;
}

export function SignalCard({ decision }: SignalCardProps) {
  const hasSignal =
    decision &&
    decision.decision_type &&
    !decision.decision_type.toUpperCase().includes("HOLD") &&
    !decision.decision_type.toUpperCase().includes("NO_SETUP");

  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("home.active_signal")}</CardTitle>
        <p className="text-xs text-muted">{t("home.signal_hint")}</p>
        {decision && (
          <DecisionTypeBadge type={decision.decision_type} metadata={decision.metadata} />
        )}
      </CardHeader>
      <CardContent>
        {!decision ? (
          <p className="text-sm text-muted">{t("common.no_data")}</p>
        ) : !hasSignal ? (
          <div className="space-y-2 text-sm">
            <p className="font-medium text-foreground-secondary">{t("home.no_signal")}</p>
            <p>
              <span className="text-muted">{t("home.reason")}: </span>
              {translateDecisionMessage(decision)}
            </p>
            {decision.signal?.strategy && (
              <p>
                <span className="text-muted">{t("common.strategy")}: </span>
                {decision.signal.strategy}
              </p>
            )}
            {decision.signal?.timeframe && (
              <p>
                <span className="text-muted">{t("home.timeframe")}: </span>
                {translateTimeframe(decision.signal.timeframe)}
              </p>
            )}
            <p className="text-xs text-muted">
              {t("common.updated")}: {formatRelativeTime(decision.timestamp)}
            </p>
          </div>
        ) : (
          <div className="space-y-2 text-sm">
            <p className="font-medium text-profit">
              {decision.decision_type.toUpperCase().includes("SELL")
                ? t("home.sell_signal")
                : t("home.buy_signal")}
            </p>
            {decision.instrument && (
              <p>
                <span className="text-muted">{t("common.instrument")}: </span>
                {decision.instrument}
              </p>
            )}
            {decision.signal?.entry_price != null && (
              <p>
                <span className="text-muted">{t("home.entry")}: </span>
                <span className="font-mono">~{formatPrice(decision.signal.entry_price)}</span>
              </p>
            )}
            {decision.signal?.stop_loss != null && (
              <p>
                <span className="text-muted">{t("home.stop_loss")}: </span>
                <span className="font-mono">{formatPrice(decision.signal.stop_loss)}</span>
              </p>
            )}
            {decision.signal?.take_profit != null && (
              <p>
                <span className="text-muted">{t("home.take_profit")}: </span>
                <span className="font-mono">{formatPrice(decision.signal.take_profit)}</span>
              </p>
            )}
            {(decision.signal?.risk_target != null || decision.signal?.risk_actual != null) && (
              <p>
                <span className="text-muted">{t("home.risk")}: </span>
                {t("home.risk_target_actual", {
                  target: decision.signal?.risk_target ?? "—",
                  actual: decision.signal?.risk_actual ?? "—",
                })}
              </p>
            )}
            {decision.signal?.execution && (
              <p>
                <span className="text-muted">{t("home.execution")}: </span>
                {translateExecution(decision.signal.execution)}
              </p>
            )}
            {decision.signal?.strategy && (
              <p>
                <span className="text-muted">{t("common.strategy")}: </span>
                {decision.signal.strategy}
              </p>
            )}
            <p>
              <span className="text-muted">{t("home.reason")}: </span>
              {translateSignalReason(decision.signal?.reason ?? decision.message)}
            </p>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
