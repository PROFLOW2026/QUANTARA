"use client";

import { useEffect, useState } from "react";
import { useTheme } from "next-themes";
import { cn } from "@/lib/utils";
import { t } from "@/lib/i18n";

type ThemeChoice = "dark" | "light";

export function ThemeSettings() {
  const { theme, setTheme, resolvedTheme } = useTheme();
  const [mounted, setMounted] = useState(false);

  useEffect(() => setMounted(true), []);

  const current = (mounted ? (resolvedTheme ?? theme) : "dark") as ThemeChoice;

  function select(next: ThemeChoice) {
    setTheme(next);
  }

  return (
    <div className="rounded-lg border border-border bg-surface-section p-2">
      <div className="flex flex-wrap gap-2">
        {(["dark", "light"] as const).map((choice) => (
          <button
            key={choice}
            type="button"
            onClick={() => select(choice)}
            className={cn(
              "rounded-md border px-4 py-2 text-sm font-medium transition-colors",
              current === choice
                ? "border-border-active bg-surface-active text-foreground"
                : "border-border bg-surface text-foreground-secondary hover:border-border-interactive hover:bg-surface-inner-hover-soft hover:text-foreground"
            )}
            aria-pressed={current === choice}
          >
            {t(choice === "dark" ? "settings.theme_dark" : "settings.theme_light")}
          </button>
        ))}
      </div>
    </div>
  );
}
