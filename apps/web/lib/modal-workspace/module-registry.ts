import { matchModuleParams } from "./parse-route";
import type { ActiveModule, ModuleDefinition, ModuleProps } from "./types";
import type { ComponentType } from "react";

export const MODULE_DEFINITIONS: ModuleDefinition[] = [
  {
    path: "/portfolio-comparison",
    titleKey: "nav.portfolio_comparison",
    load: () => import("@/components/modules/PortfolioComparisonModule"),
  },
  {
    path: "/portfolio",
    titleKey: "nav.portfolio",
    load: () => import("@/components/modules/PortfolioModule"),
  },
  {
    path: "/positions",
    titleKey: "nav.positions",
    load: () => import("@/components/modules/PositionsModule"),
  },
  {
    path: "/journal",
    titleKey: "nav.journal",
    load: () => import("@/components/modules/JournalModule"),
  },
  {
    path: "/decisions",
    titleKey: "nav.decisions",
    load: () => import("@/components/modules/DecisionsModule"),
  },
  {
    path: "/strategies",
    titleKey: "nav.strategies",
    load: () => import("@/components/modules/StrategiesModule"),
  },
  {
    pattern: /^\/strategies\/([^/]+)\/versions$/,
    titleKey: "strategies.versions_title",
    load: () => import("@/components/modules/StrategyVersionsModule"),
  },
  {
    pattern: /^\/strategies\/([^/]+)$/,
    titleKey: "nav.strategies",
    load: () => import("@/components/modules/StrategyDetailModule"),
  },
  {
    path: "/backtests",
    titleKey: "nav.backtests",
    load: () => import("@/components/modules/BacktestsModule"),
  },
  {
    pattern: /^\/backtests\/([^/]+)$/,
    titleKey: "backtests.detail_title",
    load: () => import("@/components/modules/BacktestDetailModule"),
  },
  {
    path: "/experiments",
    titleKey: "nav.experiments",
    load: () => import("@/components/modules/ExperimentsModule"),
  },
  {
    path: "/analytics",
    titleKey: "nav.analytics",
    load: () => import("@/components/modules/AnalyticsModule"),
  },
  {
    path: "/market/gold",
    titleKey: "nav.market_gold",
    load: () => import("@/components/modules/MarketGoldModule"),
  },
  {
    path: "/settings",
    titleKey: "nav.settings",
    load: () => import("@/components/modules/SettingsModule"),
  },
  {
    path: "/portfolios",
    titleKey: "portfolio.title",
    load: () => import("@/components/modules/PortfoliosModule"),
  },
];

export function resolveModuleDefinition(module: ActiveModule): ModuleDefinition | null {
  for (const definition of MODULE_DEFINITIONS) {
    if (definition.path && definition.path === module.path) {
      return definition;
    }
    if (definition.pattern && definition.pattern.test(module.path)) {
      return definition;
    }
  }
  return null;
}

export function resolveModuleParams(module: ActiveModule): Record<string, string> {
  for (const definition of MODULE_DEFINITIONS) {
    if (definition.pattern?.test(module.path)) {
      return matchModuleParams(definition.pattern, module.path);
    }
  }
  return {};
}

export type LoadedModule = {
  definition: ModuleDefinition;
  Component: ComponentType<ModuleProps>;
  params: Record<string, string>;
};

export async function loadModule(module: ActiveModule): Promise<LoadedModule | null> {
  const definition = resolveModuleDefinition(module);
  if (!definition) return null;
  const loaded = await definition.load();
  return {
    definition,
    Component: loaded.default,
    params: resolveModuleParams(module),
  };
}

export function isModalPath(path: string): boolean {
  return MODULE_DEFINITIONS.some((definition) => {
    if (definition.path === path) return true;
    if (definition.pattern) return definition.pattern.test(path);
    return false;
  });
}
