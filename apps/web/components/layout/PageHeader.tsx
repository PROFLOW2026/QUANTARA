import { t } from "@/lib/i18n";
import { translateStatus } from "@/lib/display-text";
import { Card, CardContent } from "@/components/ui/card";

interface PageHeaderProps {
  titleKey: string;
  subtitleKey?: string;
  action?: React.ReactNode;
}

export function PageHeader({ titleKey, subtitleKey, action }: PageHeaderProps) {
  return (
    <div className="mb-6 flex items-center justify-between">
      <div>
        <h1 className="text-2xl font-bold text-slate-100">{t(titleKey)}</h1>
        {subtitleKey ? (
          <p className="mt-1 text-sm text-muted">{t(subtitleKey)}</p>
        ) : null}
      </div>
      {action}
    </div>
  );
}

export function ErrorBanner({ message }: { message?: string }) {
  return (
    <Card className="border-loss/30 bg-loss/5">
      <CardContent className="py-3 text-sm text-loss">
        {message ?? t("common.error")}
      </CardContent>
    </Card>
  );
}

export function ChartPlaceholder({ label }: { label: string }) {
  return (
    <div className="flex h-48 items-center justify-center rounded-lg border border-dashed border-border bg-surface-elevated/30">
      <p className="text-sm text-muted">{label}</p>
    </div>
  );
}

export function WorkerIndicator({ healthy }: { healthy: boolean }) {
  return (
    <span className="inline-flex items-center gap-2">
      <span
        className={`h-2.5 w-2.5 rounded-full ${healthy ? "bg-profit" : "bg-loss"}`}
      />
      <span className="text-sm">
        {healthy ? t("home.workers_healthy") : t("home.workers_unhealthy")}
      </span>
    </span>
  );
}

export function StatusBadge({ status }: { status: string }) {
  const lower = status.toLowerCase();
  let color = "bg-slate-600";
  if (["active", "running", "completed", "healthy"].includes(lower))
    color = "bg-profit";
  else if (["failed", "error", "halted"].includes(lower)) color = "bg-loss";
  else if (["pending", "draft"].includes(lower)) color = "bg-warning";

  return (
    <span className="inline-flex items-center gap-1.5 text-sm">
      <span className={`h-2 w-2 rounded-full ${color}`} />
      {translateStatus(status)}
    </span>
  );
}
