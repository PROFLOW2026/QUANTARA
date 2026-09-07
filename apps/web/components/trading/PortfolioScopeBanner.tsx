import Link from "next/link";
import { t } from "@/lib/i18n";

interface PortfolioScopeBannerProps {
  portfolioId: string;
  portfolioName?: string;
}

export function PortfolioScopeBanner({
  portfolioId,
  portfolioName,
}: PortfolioScopeBannerProps) {
  return (
    <div className="mb-4 flex flex-wrap items-center justify-between gap-2 rounded-lg border border-border bg-surface-elevated/40 px-4 py-2 text-sm">
      <span>
        {t("portfolio.viewing")}:{" "}
        <strong>{portfolioName ?? portfolioId.slice(0, 8)}</strong>
      </span>
      <div className="flex gap-3">
        <Link href="/portfolio-comparison" className="text-accent hover:underline">
          {t("home.competition_view")} →
        </Link>
        <Link href="/portfolios" className="text-muted hover:text-accent hover:underline">
          {t("portfolio.switch")}
        </Link>
      </div>
    </div>
  );
}
