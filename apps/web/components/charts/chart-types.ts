/** Reserved for future execution overlays on candlestick charts (fills only). */
export interface ChartExecutionMarker {
  time: string;
  price: number;
  kind: "entry" | "exit" | "sl" | "tp";
  label?: string;
}
