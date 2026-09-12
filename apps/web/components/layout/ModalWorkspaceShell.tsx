"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { cn } from "@/lib/utils";
import { t } from "@/lib/i18n";
import { loadModule, type LoadedModule } from "@/lib/modal-workspace/module-registry";
import { useModalWorkspace } from "./ModalWorkspaceProvider";

export function ModalWorkspaceShell() {
  const { activeModule, closeModule, isOpen } = useModalWorkspace();
  const [loaded, setLoaded] = useState<LoadedModule | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const loadToken = useRef(0);

  useEffect(() => {
    if (!activeModule) {
      setLoaded(null);
      setError(null);
      setLoading(false);
      return;
    }

    const token = ++loadToken.current;
    setLoading(true);
    setError(null);

    void loadModule(activeModule)
      .then((result) => {
        if (token !== loadToken.current) return;
        if (!result) {
          setError(t("common.error"));
          setLoaded(null);
          return;
        }
        setLoaded(result);
      })
      .catch(() => {
        if (token !== loadToken.current) return;
        setError(t("common.error"));
        setLoaded(null);
      })
      .finally(() => {
        if (token === loadToken.current) setLoading(false);
      });
  }, [activeModule]);

  useEffect(() => {
    if (!isOpen) return;

    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        closeModule();
      }
    };

    window.addEventListener("keydown", onKeyDown);
    panelRef.current?.focus();

    return () => {
      document.body.style.overflow = previousOverflow;
      window.removeEventListener("keydown", onKeyDown);
    };
  }, [isOpen, closeModule]);

  const handleBackdropClick = useCallback(
    (event: React.MouseEvent<HTMLDivElement>) => {
      if (event.target === event.currentTarget) {
        closeModule();
      }
    },
    [closeModule]
  );

  if (!isOpen || !activeModule) return null;

  const titleKey = loaded?.definition.titleKey;
  const ModuleComponent = loaded?.Component;

  return (
    <div
      className="fixed inset-0 z-[70] flex items-center justify-center bg-overlay p-2 sm:p-4"
      onMouseDown={handleBackdropClick}
      role="presentation"
    >
      <div
        ref={panelRef}
        tabIndex={-1}
        role="dialog"
        aria-modal="true"
        aria-label={titleKey ? t(titleKey) : t("common.actions")}
        className={cn(
          "flex w-full flex-col overflow-hidden rounded-xl border border-border bg-surface-elevated shadow-modal outline-none",
          "h-[96dvh] max-h-[96dvh] sm:h-[94dvh] sm:max-h-[94dvh]",
          "max-w-[96vw] sm:max-w-[95vw]"
        )}
      >
        <header className="flex shrink-0 items-center justify-between gap-3 border-b border-border bg-modal-header px-4 py-3 sm:px-6">
          <h2 className="truncate text-lg font-semibold text-foreground">
            {titleKey ? t(titleKey) : "—"}
          </h2>
          <button
            type="button"
            onClick={closeModule}
            aria-label={t("common.close_module")}
            className="inline-flex h-9 min-w-9 items-center justify-center rounded-md border border-border bg-secondary-btn px-3 text-sm text-foreground-secondary transition hover:bg-secondary-btn-hover hover:text-foreground"
          >
            ✕ {t("common.close_module")}
          </button>
        </header>

        <div className="min-h-0 flex-1 overflow-y-auto bg-surface-elevated px-4 py-4 sm:px-6 sm:py-5 scrollbar-thin">
          {loading ? (
            <p className="text-sm text-muted">{t("common.loading")}</p>
          ) : error ? (
            <p className="text-sm text-loss">{error}</p>
          ) : ModuleComponent ? (
            <ModuleComponent
              embedded
              searchParams={activeModule.searchParams}
              params={loaded?.params}
            />
          ) : null}
        </div>
      </div>
    </div>
  );
}
