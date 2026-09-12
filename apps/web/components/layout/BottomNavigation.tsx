"use client";

import { useCallback, useEffect, useState } from "react";
import { cn } from "@/lib/utils";
import { t } from "@/lib/i18n";
import { ModalLink } from "./ModalLink";
import { useModalWorkspace } from "./ModalWorkspaceProvider";

const bottomNavItems = [
  { href: "/portfolio-comparison", labelKey: "nav.portfolio_comparison", icon: "⚖️" },
  { href: "/positions", labelKey: "nav.positions", icon: "📊" },
  { href: "/decisions", labelKey: "nav.decisions", icon: "📋" },
  { href: "/analytics", labelKey: "nav.analytics", icon: "📈" },
] as const;

const moreMenuItems = [
  { href: "/portfolio", labelKey: "nav.portfolio", icon: "💼" },
  { href: "/portfolios", labelKey: "nav.portfolios", icon: "📁" },
  { href: "/journal", labelKey: "nav.journal", icon: "📓" },
  { href: "/strategies", labelKey: "nav.strategies", icon: "🎯" },
  { href: "/backtests", labelKey: "nav.backtests", icon: "🔬" },
  { href: "/experiments", labelKey: "nav.experiments", icon: "🧪" },
  { href: "/market/gold", labelKey: "nav.market_gold", icon: "🥇" },
] as const;

const settingsItem = {
  href: "/settings",
  labelKey: "nav.settings",
  icon: "⚙️",
} as const;

function useNavActive(href: string): boolean {
  const { activeModule, isOpen } = useModalWorkspace();
  if (href === "/") return !isOpen;
  if (!isOpen || !activeModule) return false;
  if (activeModule.path === href) return true;
  return activeModule.path.startsWith(`${href}/`);
}

function useMoreMenuActive(): boolean {
  const { activeModule, isOpen } = useModalWorkspace();
  if (!isOpen || !activeModule) return false;
  return moreMenuItems.some(
    (item) =>
      activeModule.path === item.href || activeModule.path.startsWith(`${item.href}/`)
  );
}

function BottomNavItem({
  href,
  labelKey,
  icon,
}: {
  href: string;
  labelKey: string;
  icon: string;
}) {
  const active = useNavActive(href);

  return (
    <ModalLink
      href={href}
      scroll={false}
      className={cn(
        "flex min-w-0 flex-1 flex-col items-center justify-center gap-0.5 px-1 py-2 text-[10px] leading-tight transition-colors sm:text-xs",
        "focus:outline-none focus-visible:ring-2 focus-visible:ring-accent/45 focus-visible:ring-inset",
        active ? "text-accent" : "text-muted hover:text-foreground"
      )}
      aria-current={active ? "page" : undefined}
    >
      <span className="text-lg leading-none" aria-hidden="true">
        {icon}
      </span>
      <span className="max-w-full truncate text-center">{t(labelKey)}</span>
    </ModalLink>
  );
}

function MoreMenu({
  open,
  onClose,
}: {
  open: boolean;
  onClose: () => void;
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
    <div className="fixed inset-0 z-[60] flex items-end justify-center sm:items-end">
      <button
        type="button"
        className="absolute inset-0 bg-overlay"
        aria-label={t("common.close_module")}
        onClick={onClose}
      />
      <div
        role="menu"
        aria-label={t("nav.more")}
        dir="rtl"
        className="relative z-10 mb-[4.25rem] w-full max-w-lg rounded-t-xl border border-border bg-background shadow-xl sm:mb-[4.5rem] sm:rounded-xl"
      >
        <div className="border-b border-border px-4 py-3">
          <p className="text-sm font-semibold">{t("nav.more")}</p>
        </div>
        <ul className="max-h-[min(60vh,24rem)] overflow-y-auto py-1">
          {moreMenuItems.map((item) => (
            <li key={item.href}>
              <ModalLink
                href={item.href}
                scroll={false}
                role="menuitem"
                onClick={onClose}
                className="flex items-center gap-3 px-4 py-3 text-sm text-foreground transition-colors hover:bg-surface-elevated focus:outline-none focus-visible:bg-surface-elevated"
              >
                <span className="text-base" aria-hidden="true">
                  {item.icon}
                </span>
                <span>{t(item.labelKey)}</span>
              </ModalLink>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}

export function BottomNavigation() {
  const [moreOpen, setMoreOpen] = useState(false);
  const moreActive = useMoreMenuActive();
  const { isOpen } = useModalWorkspace();

  useEffect(() => {
    if (isOpen) setMoreOpen(false);
  }, [isOpen]);

  const openMore = useCallback(() => setMoreOpen(true), []);
  const closeMore = useCallback(() => setMoreOpen(false), []);

  return (
    <>
      <nav
        className="fixed bottom-0 left-0 right-0 z-50 flex border-t border-border bg-surface/95 pb-[env(safe-area-inset-bottom)] backdrop-blur supports-[backdrop-filter]:bg-surface/90"
        aria-label={t("nav.main_navigation")}
      >
        {bottomNavItems.map((item) => (
          <BottomNavItem
            key={item.href}
            href={item.href}
            labelKey={item.labelKey}
            icon={item.icon}
          />
        ))}
        <button
          type="button"
          onClick={openMore}
          className={cn(
            "flex min-w-0 flex-1 flex-col items-center justify-center gap-0.5 px-1 py-2 text-[10px] leading-tight transition-colors sm:text-xs",
            "focus:outline-none focus-visible:ring-2 focus-visible:ring-accent/45 focus-visible:ring-inset",
            moreActive || moreOpen ? "text-accent" : "text-muted hover:text-foreground"
          )}
          aria-expanded={moreOpen}
          aria-haspopup="menu"
          aria-label={t("nav.more")}
        >
          <span className="text-lg leading-none" aria-hidden="true">
            ⋯
          </span>
          <span className="max-w-full truncate text-center">{t("nav.more")}</span>
        </button>
        <BottomNavItem
          href={settingsItem.href}
          labelKey={settingsItem.labelKey}
          icon={settingsItem.icon}
        />
      </nav>
      <MoreMenu open={moreOpen} onClose={closeMore} />
    </>
  );
}
