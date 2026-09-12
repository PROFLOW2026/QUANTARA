"use client";

import { ModalLink } from "@/components/layout/ModalLink";
import { t } from "@/lib/i18n";

export function TopHeader() {
  return (
    <header className="sticky top-0 z-40 border-b border-border bg-surface-chrome backdrop-blur supports-[backdrop-filter]:bg-surface-chrome/95">
      <div className="mx-auto max-w-7xl px-4 py-2.5 sm:px-6">
        <ModalLink
          href="/"
          scroll={false}
          className="block rounded-md focus:outline-none focus-visible:ring-2 focus-visible:ring-focus/45"
          aria-label={t("app.name")}
        >
          <h1 className="text-base font-bold tracking-tight text-foreground sm:text-lg">
            {t("app.name")}
          </h1>
          <p className="mt-0.5 text-[11px] leading-snug text-text-subtle sm:text-xs">
            {t("app.tagline")}
          </p>
        </ModalLink>
      </div>
    </header>
  );
}
