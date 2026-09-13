export type PriceFlashDirection = "up" | "down" | null;

/** Pure flash direction — initial sample returns null (neutral). */
export function computePriceFlash(
  previousPrice: number | null,
  nextPrice: number | null,
  hasPriorSample: boolean
): PriceFlashDirection {
  if (!hasPriorSample || previousPrice == null || nextPrice == null) {
    return null;
  }
  if (nextPrice > previousPrice) return "up";
  if (nextPrice < previousPrice) return "down";
  return null;
}

export function priceFlashClassName(flash: PriceFlashDirection): string {
  if (flash === "up") return "text-profit transition-colors duration-300";
  if (flash === "down") return "text-loss transition-colors duration-300";
  return "text-financial transition-colors duration-300";
}

export const PRICE_FLASH_DURATION_MS = 20_000;
