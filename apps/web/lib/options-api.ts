import { apiFetch } from "./api-client";
import type {
  OptionChainResponse,
  OptionsControlRequest,
  OptionsControlResponse,
  OptionsLabOverviewResponse,
  OptionsStrategyGroupView,
  StrategyCandidate,
} from "./options-types";

function qs(params: Record<string, string | number | boolean | null | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === null || value === undefined || value === "") continue;
    search.set(key, String(value));
  }
  const s = search.toString();
  return s ? `?${s}` : "";
}

export type OptionsChainQuery = {
  symbol: string;
  min_dte?: number;
  max_dte?: number;
  use_fixtures?: boolean;
};

export const optionsApi = {
  getOverview: (params: { account_slug?: string | null; symbol?: string | null } = {}) =>
    apiFetch<OptionsLabOverviewResponse>(
      `/options/overview${qs({ account_slug: params.account_slug, symbol: params.symbol })}`
    ),

  getChain: ({ symbol, min_dte = 3, max_dte = 60, use_fixtures = false }: OptionsChainQuery) =>
    apiFetch<OptionChainResponse>(
      `/options/chain${qs({ symbol, min_dte, max_dte, use_fixtures })}`
    ),

  control: (body: OptionsControlRequest) =>
    apiFetch<OptionsControlResponse>("/options/control", {
      method: "POST",
      body: JSON.stringify(body),
    }),

  start: (opts: Omit<OptionsControlRequest, "action"> = {}) =>
    optionsApi.control({ action: "start", ...opts }),

  stop: () => optionsApi.control({ action: "stop" }),

  runOnce: (opts: Omit<OptionsControlRequest, "action"> = {}) =>
    optionsApi.control({ action: "run_once", ...opts }),

  simulateOpen: (account_slug: string, candidate: StrategyCandidate) =>
    apiFetch<OptionsStrategyGroupView>("/options/simulate/open", {
      method: "POST",
      body: JSON.stringify({ account_slug, candidate }),
    }),

  closePosition: (strategyId: string) =>
    apiFetch<OptionsStrategyGroupView>(
      `/options/positions/${encodeURIComponent(strategyId)}/close`,
      { method: "POST" }
    ),

  rollPosition: (strategyId: string) =>
    apiFetch<OptionsStrategyGroupView>(
      `/options/positions/${encodeURIComponent(strategyId)}/roll`,
      { method: "POST" }
    ),
};
