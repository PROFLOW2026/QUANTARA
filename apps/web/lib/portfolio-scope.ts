import { redirect } from "next/navigation";

import type { PortfolioListItem } from "@/lib/api-client";

/** Hardcoded defaults and archived legacy refs — never use as fallbacks. */
export const OBSOLETE_PORTFOLIO_REFS = new Set([
  "00000000-0000-0000-0000-00000001101",
  "00000000-0000-0000-0000-00001101",
  "00000000-0000-0000-0000-000000000101",
]);

export function canonicalizePortfolioId(ref: string): string {
  const cleaned = ref.trim().toLowerCase();
  const match = cleaned.match(
    /^([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-)([0-9a-f]+)$/
  );
  if (!match) return cleaned;
  return `${match[1]}${match[2].padStart(12, "0")}`;
}

export function competitionPortfolios(
  portfolios: PortfolioListItem[]
): PortfolioListItem[] {
  return portfolios
    .filter((item) => item.kind === "competition")
    .sort((a, b) => (a.sort_order ?? 99) - (b.sort_order ?? 99));
}

export type PortfolioScope = {
  portfolioId?: string;
  scopeAll: boolean;
};

export function resolvePortfolioScope(
  requestedId: string | undefined,
  portfolios: PortfolioListItem[],
  options?: {
    redirectPath?: string;
    requireSelection?: boolean;
  }
): PortfolioScope {
  const active = competitionPortfolios(portfolios);
  const activeIds = new Map(
    active.map((item) => [canonicalizePortfolioId(item.id), item.id])
  );

  if (!requestedId) {
    if (options?.requireSelection) {
      const first = active[0];
      return { portfolioId: first?.id, scopeAll: false };
    }
    return { scopeAll: true };
  }

  const canonical = canonicalizePortfolioId(requestedId);
  if (
    OBSOLETE_PORTFOLIO_REFS.has(canonical) ||
    OBSOLETE_PORTFOLIO_REFS.has(requestedId.trim().toLowerCase())
  ) {
    if (options?.redirectPath) redirect(options.redirectPath);
    if (options?.requireSelection) {
      return { portfolioId: active[0]?.id, scopeAll: false };
    }
    return { scopeAll: true };
  }

  const resolvedId = activeIds.get(canonical);
  if (!resolvedId) {
    if (options?.redirectPath) redirect(options.redirectPath);
    if (options?.requireSelection) {
      return { portfolioId: active[0]?.id, scopeAll: false };
    }
    return { scopeAll: true };
  }

  return { portfolioId: resolvedId, scopeAll: false };
}
