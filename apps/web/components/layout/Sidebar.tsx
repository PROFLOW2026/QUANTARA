"use client";

import { cn } from "@/lib/utils";
import { t } from "@/lib/i18n";
import { ModalLink } from "./ModalLink";
import { useModalWorkspace } from "./ModalWorkspaceProvider";

const navItems = [
  { href: "/", labelKey: "nav.home", icon: "🏠" },
  { href: "/portfolio-comparison", labelKey: "nav.portfolio_comparison", icon: "⚖️" },
  { href: "/portfolio", labelKey: "nav.portfolio", icon: "💼" },
  { href: "/positions", labelKey: "nav.positions", icon: "📊" },
  { href: "/journal", labelKey: "nav.journal", icon: "📓" },
  { href: "/decisions", labelKey: "nav.decisions", icon: "📋" },
  { href: "/strategies", labelKey: "nav.strategies", icon: "🎯" },
  { href: "/backtests", labelKey: "nav.backtests", icon: "🔬" },
  { href: "/experiments", labelKey: "nav.experiments", icon: "🧪" },
  { href: "/analytics", labelKey: "nav.analytics", icon: "📈" },
  { href: "/market/gold", labelKey: "nav.market_gold", icon: "🥇" },
  { href: "/settings", labelKey: "nav.settings", icon: "⚙️" },
];

function useNavActive(href: string): boolean {
  const { activeModule, isOpen } = useModalWorkspace();
  if (href === "/") return !isOpen;
  if (!isOpen || !activeModule) return false;
  if (activeModule.path === href) return true;
  return activeModule.path.startsWith(`${href}/`);
}

function NavLink({ href, label, icon }: { href: string; label: string; icon: string }) {
  const active = useNavActive(href);

  return (
    <ModalLink
      href={href}
      scroll={false}
      className={cn(
        "flex items-center gap-3 rounded-lg px-3 py-2.5 text-sm transition-colors",
        active
          ? "bg-accent/15 text-accent"
          : "text-slate-400 hover:bg-surface-elevated hover:text-slate-200"
      )}
    >
      <span className="text-base">{icon}</span>
      <span>{label}</span>
    </ModalLink>
  );
}

export function Sidebar() {
  return (
    <aside className="hidden lg:flex lg:w-60 lg:flex-col lg:border-l lg:border-border lg:bg-surface">
      <div className="border-b border-border px-4 py-5">
        <h1 className="text-lg font-bold tracking-tight text-slate-100">
          {t("app.name")}
        </h1>
        <p className="mt-0.5 text-xs text-muted">{t("app.tagline")}</p>
      </div>
      <nav className="flex-1 space-y-0.5 overflow-y-auto p-3 scrollbar-thin">
        {navItems.map((item) => (
          <NavLink
            key={item.href}
            href={item.href}
            label={t(item.labelKey)}
            icon={item.icon}
          />
        ))}
      </nav>
    </aside>
  );
}

function MobileNavItem({
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
        "flex flex-1 flex-col items-center gap-0.5 py-2 text-[10px]",
        active ? "text-accent" : "text-muted"
      )}
    >
      <span className="text-lg">{icon}</span>
      <span className="truncate px-1">{t(labelKey)}</span>
    </ModalLink>
  );
}

export function MobileBottomNav() {
  const mobileItems = navItems.filter((item) =>
    ["/", "/portfolio-comparison", "/positions", "/decisions", "/analytics", "/settings"].includes(item.href)
  );

  return (
    <nav className="fixed bottom-0 left-0 right-0 z-50 flex border-t border-border bg-surface lg:hidden">
      {mobileItems.map((item) => (
        <MobileNavItem
          key={item.href}
          href={item.href}
          labelKey={item.labelKey}
          icon={item.icon}
        />
      ))}
    </nav>
  );
}
