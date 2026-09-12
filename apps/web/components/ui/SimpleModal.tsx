"use client";

import { useEffect } from "react";
import { t } from "@/lib/i18n";

export function SimpleModal({
  open,
  title,
  onClose,
  children,
}: {
  open: boolean;
  title: string;
  onClose: () => void;
  children: React.ReactNode;
}) {
  useEffect(() => {
    if (!open) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-[80] flex items-end justify-center p-3 sm:items-center sm:p-4">
      <button
        type="button"
        className="absolute inset-0 bg-overlay"
        aria-label={t("common.close_module")}
        onClick={onClose}
      />
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="simple-modal-title"
        className="relative z-10 flex max-h-[88vh] w-full max-w-3xl flex-col overflow-hidden rounded-lg border border-border bg-background shadow-xl"
      >
        <div className="flex items-start justify-between gap-3 border-b border-border px-4 py-3">
          <h3 id="simple-modal-title" className="text-base font-semibold">
            {title}
          </h3>
          <button
            type="button"
            className="rounded-md px-2 py-1 text-sm text-muted hover:bg-surface-elevated hover:text-foreground"
            onClick={onClose}
          >
            {t("common.close_module")}
          </button>
        </div>
        <div className="overflow-y-auto p-4">{children}</div>
      </div>
    </div>
  );
}
