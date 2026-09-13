import assert from "node:assert/strict";
import test from "node:test";

import { computePriceFlash, priceFlashClassName } from "./price-flash.ts";

test("initial load stays neutral", () => {
  assert.equal(computePriceFlash(null, 2524.91, false), null);
});

test("upward move is green class", () => {
  assert.equal(computePriceFlash(2524.91, 2526.1, true), "up");
  assert.match(priceFlashClassName("up"), /text-profit/);
});

test("downward move is red class", () => {
  assert.equal(computePriceFlash(2526.1, 2523.8, true), "down");
  assert.match(priceFlashClassName("down"), /text-loss/);
});

test("unchanged price has no flash", () => {
  assert.equal(computePriceFlash(2524.91, 2524.91, true), null);
});

test("neutral uses financial text class", () => {
  assert.match(priceFlashClassName(null), /text-financial/);
});
