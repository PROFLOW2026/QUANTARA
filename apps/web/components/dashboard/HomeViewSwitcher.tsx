"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { t } from "@/lib/i18n";
import { cn } from "@/lib/utils";

export type HomeView = "research" | "live-sim" | "compare";
export type LiveSimSubView = "quantara" | "ai-trader" | "ai-compare";

const VIEWS: { id: HomeView; labelKey: string }[] = [
  { id: "research", labelKey: "home.view_research" },
  { id: "live-sim", labelKey: "home.view_live_sim" },
  { id: "compare", labelKey: "home.view_compare" },
];

const LIVE_SIM_SUBVIEWS: { id: LiveSimSubView; labelKey: string }[] = [
  { id: "quantara", labelKey: "home.subview_quantara" },
  { id: "ai-trader", labelKey: "home.subview_ai_trader" },
  { id: "ai-compare", labelKey: "home.subview_ai_compare" },
];

export function parseHomeView(raw: string | null): HomeView {
  if (raw === "live-sim" || raw === "compare") return raw;
  return "research";
}

export function parseLiveSimSubView(raw: string | null): LiveSimSubView {
  if (raw === "ai-trader" || raw === "ai-compare") return raw;
  return "quantara";
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
      params.delete("subview");
    } else {
      params.set("view", view);
      if (view !== "live-sim") params.delete("subview");
    }
    const qs = params.toString();
    router.replace(qs ? `/?${qs}` : "/", { scroll: false });
  }

  return (
    <div className="mb-4 flex flex-wrap gap-2 rounded-lg border border-switcher-border bg-switcher-bg p-1">
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

/** Sub-navigation shown only within the live-sim view */
export function LiveSimSubViewSwitcher() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const current = parseLiveSimSubView(searchParams.get("subview"));

  function select(subview: LiveSimSubView) {
    const params = new URLSearchParams(searchParams.toString());
    if (subview === "quantara") {
      params.delete("subview");
    } else {
      params.set("subview", subview);
    }
    router.replace(`/?${params.toString()}`, { scroll: false });
  }

  return (
    <div className="mb-6 flex flex-wrap gap-1 rounded-md border border-border/50 bg-surface-inner p-1">
      {LIVE_SIM_SUBVIEWS.map((sv) => (
        <button
          key={sv.id}
          type="button"
          onClick={() => select(sv.id)}
          className={cn(
            "rounded px-3 py-1.5 text-sm font-medium transition-colors",
            current === sv.id
              ? "bg-accent text-accent-foreground shadow-sm"
              : "text-foreground-secondary hover:bg-surface-active hover:text-foreground"
          )}
        >
          {t(sv.labelKey)}
        </button>
      ))}
    </div>
  );
}
