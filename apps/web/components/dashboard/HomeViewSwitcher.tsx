"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { t } from "@/lib/i18n";
import { cn } from "@/lib/utils";

export type HomeView = "research" | "live-sim" | "compare";

const VIEWS: { id: HomeView; labelKey: string }[] = [
  { id: "research", labelKey: "home.view_research" },
  { id: "live-sim", labelKey: "home.view_live_sim" },
  { id: "compare", labelKey: "home.view_compare" },
];

export function parseHomeView(raw: string | null): HomeView {
  if (raw === "live-sim" || raw === "compare") return raw;
  return "research";
}

function EngineInlineStatus({ healthy }: { healthy: boolean | null | undefined }) {
  const dotClass =
    healthy === null || healthy === undefined
      ? "bg-muted"
      : healthy
        ? "bg-profit"
        : "bg-loss";

  return (
    <span className="inline-flex items-center gap-1 text-xs font-normal opacity-90">
      <span className={cn("h-1.5 w-1.5 shrink-0 rounded-full", dotClass)} aria-hidden />
      <span>{t("home.engine_inline_label")}</span>
    </span>
  );
}

type Props = {
  engineHealthy?: boolean | null;
};

export function HomeViewSwitcher({ engineHealthy }: Props) {
  const router = useRouter();
  const searchParams = useSearchParams();
  const current = parseHomeView(searchParams.get("view"));

  function select(view: HomeView) {
    const params = new URLSearchParams(searchParams.toString());
    if (view === "research") {
      params.delete("view");
    } else {
      params.set("view", view);
    }
    const qs = params.toString();
    router.replace(qs ? `/?${qs}` : "/", { scroll: false });
  }

  return (
    <div className="mb-6 flex flex-wrap gap-2 rounded-lg border border-switcher-border bg-switcher-bg p-1">
      {VIEWS.map((view) => (
        <button
          key={view.id}
          type="button"
          onClick={() => select(view.id)}
          className={cn(
            "rounded-md px-4 py-2 text-sm font-medium transition-colors",
            current === view.id
              ? "bg-primary text-primary-foreground shadow-sm"
              : "text-foreground-secondary hover:bg-surface-active hover:text-foreground"
          )}
        >
          {view.id === "research" ? (
            <span className="inline-flex items-center gap-2">
              <span>{t(view.labelKey)}</span>
              <EngineInlineStatus healthy={engineHealthy} />
            </span>
          ) : (
            t(view.labelKey)
          )}
        </button>
      ))}
    </div>
  );
}
