"use client";

import { useState } from "react";
import { usePwaInstall } from "@/lib/pwa/use-pwa-install";
import { t } from "@/lib/i18n";
import { cn } from "@/lib/utils";

export function PwaInstallPanel() {
  const { capability, install, lastOutcome } = usePwaInstall();
  const [busy, setBusy] = useState(false);

  async function handleInstall() {
    setBusy(true);
    try {
      await install();
    } finally {
      setBusy(false);
    }
  }

  if (capability === "installed") {
    return (
      <p className="text-sm text-foreground-secondary">
        {t("settings.pwa_installed")}
      </p>
    );
  }

  if (capability === "prompt_available") {
    return (
      <div className="space-y-3">
        <button
          type="button"
          onClick={handleInstall}
          disabled={busy}
          className={cn(
            "rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground",
            "hover:bg-primary/90 disabled:opacity-50"
          )}
        >
          {t("settings.pwa_install_button")}
        </button>
        {lastOutcome === "dismissed" ? (
          <p className="text-xs text-muted">{t("settings.pwa_install_dismissed")}</p>
        ) : null}
        {lastOutcome === "error" || lastOutcome === "unavailable" ? (
          <p className="text-xs text-muted">{t("settings.pwa_install_unavailable")}</p>
        ) : null}
      </div>
    );
  }

  if (capability === "manual_ios") {
    return (
      <ol className="list-decimal space-y-2 pr-5 text-sm text-foreground-secondary">
        <li>{t("settings.pwa_ios_step_share")}</li>
        <li>{t("settings.pwa_ios_step_add")}</li>
        <li>{t("settings.pwa_ios_step_confirm")}</li>
      </ol>
    );
  }

  return (
    <p className="text-sm text-muted">{t("settings.pwa_install_unavailable")}</p>
  );
}
