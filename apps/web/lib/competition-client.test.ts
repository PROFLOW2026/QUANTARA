import assert from "node:assert/strict";
import test from "node:test";

import type { PortfolioListItem } from "./api-client";
import { buildCompetitionViewFromItems } from "./competition-client";

function competitionRow(id: string, initial = 2000): PortfolioListItem {
  return {
    id,
    name: `P-${id}`,
    kind: "competition",
    initial_capital: initial,
    equity: initial,
    timeframe: "5m",
    timeframe_he: "5m",
    robot_label: "Robot A",
    sort_order: 1,
  };
}

test("buildCompetitionViewFromItems sets shadow_reference_capital from sum of seeds", () => {
  const items = Array.from({ length: 735 }, (_, i) => competitionRow(`p-${i}`));
  const view = buildCompetitionViewFromItems(items);
  assert.equal(view.active, true);
  assert.equal(view.experiment?.portfolio_count, 735);
  assert.equal(view.experiment?.portfolio_initial_capital, 2000);
  assert.equal(view.experiment?.total_initial_capital, 1_470_000);
  assert.equal(view.experiment?.shadow_reference_capital, 1_470_000);
  assert.equal(view.combined?.initial_equity, 1_470_000);
});

test("shadow_reference_capital is defined when portfolios exist (not null for UI zero fallback)", () => {
  const view = buildCompetitionViewFromItems([competitionRow("one", 2000)]);
  assert.notEqual(view.experiment?.shadow_reference_capital, null);
  assert.notEqual(view.experiment?.shadow_reference_capital, undefined);
});
