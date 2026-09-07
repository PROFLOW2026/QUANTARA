import {
  boolean,
  index,
  numeric,
  pgTable,
  timestamp,
  unique,
  uuid,
  varchar,
} from "drizzle-orm/pg-core";
import { timeframeEnum } from "./enums";
import { instruments } from "./instruments";

export const candles = pgTable(
  "candles",
  {
    id: uuid("id").primaryKey().defaultRandom(),
    instrumentId: uuid("instrument_id")
      .notNull()
      .references(() => instruments.id),
    timeframe: timeframeEnum("timeframe").notNull(),
    timestamp: timestamp("timestamp", {
      withTimezone: true,
      mode: "string",
    }).notNull(),
    open: numeric("open", { precision: 18, scale: 8 }).notNull(),
    high: numeric("high", { precision: 18, scale: 8 }).notNull(),
    low: numeric("low", { precision: 18, scale: 8 }).notNull(),
    close: numeric("close", { precision: 18, scale: 8 }).notNull(),
    volume: numeric("volume", { precision: 18, scale: 4 }),
    source: varchar("source", { length: 50 }).notNull(),
    isComplete: boolean("is_complete").notNull().default(true),
    createdAt: timestamp("created_at", { withTimezone: true, mode: "string" })
      .notNull()
      .defaultNow(),
  },
  (table) => [
    unique("candles_instrument_timeframe_timestamp_source_unique").on(
      table.instrumentId,
      table.timeframe,
      table.timestamp,
      table.source,
    ),
    index("candles_instrument_timeframe_timestamp_idx").on(
      table.instrumentId,
      table.timeframe,
      table.timestamp,
    ),
  ],
);
