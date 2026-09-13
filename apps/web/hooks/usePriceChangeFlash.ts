"use client";

import {
  computePriceFlash,
  PRICE_FLASH_DURATION_MS,
  type PriceFlashDirection,
} from "@/lib/price-flash";
import { useEffect, useRef, useState } from "react";

export type { PriceFlashDirection } from "@/lib/price-flash";

/**
 * Temporary green/red flash when displayed price moves after initial render.
 * Initial load stays neutral; each move resets the 20s timer.
 */
export function usePriceChangeFlash(
  price: number | null | undefined,
  symbol: string
): PriceFlashDirection {
  const [flash, setFlash] = useState<PriceFlashDirection>(null);
  const prevRef = useRef<{ symbol: string; price: number | null }>({
    symbol,
    price: null,
  });
  const timerRef = useRef<number | undefined>(undefined);

  useEffect(() => {
    if (price == null || !Number.isFinite(price)) {
      return;
    }

    const prev = prevRef.current;
    if (prev.symbol !== symbol) {
      prevRef.current = { symbol, price };
      setFlash(null);
      return;
    }

    const hasPriorSample = prev.price != null;
    const direction = computePriceFlash(prev.price, price, hasPriorSample);
    prevRef.current = { symbol, price };

    if (direction == null) {
      if (!hasPriorSample) {
        setFlash(null);
      }
      return;
    }

    setFlash(direction);

    if (timerRef.current !== undefined) {
      window.clearTimeout(timerRef.current);
    }
    timerRef.current = window.setTimeout(() => {
      setFlash(null);
      timerRef.current = undefined;
    }, PRICE_FLASH_DURATION_MS);
  }, [price, symbol]);

  useEffect(
    () => () => {
      if (timerRef.current !== undefined) {
        window.clearTimeout(timerRef.current);
      }
    },
    []
  );

  return flash;
}
