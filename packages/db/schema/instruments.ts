import {
  boolean,
  jsonb,
  numeric,
  pgTable,
  timestamp,
  uuid,
  varchar,
} from "drizzle-orm/pg-core";
import { assetClassEnum } from "./enums";

export const instruments = pgTable("instruments", {
  id: uuid("id").primaryKey().defaultRandom(),
  symbol: varchar("symbol", { length: 20 }).notNull().unique(),
  name: varchar("name", { length: 100 }).notNull(),
  assetClass: assetClassEnum("asset_class").notNull(),
  baseCurrency: varchar("base_currency", { length: 10 }).notNull(),
  quoteCurrency: varchar("quote_currency", { length: 10 }).notNull(),
  pipSize: numeric("pip_size").notNull(),
  contractSize: numeric("contract_size").notNull(),
  priceTickSize: numeric("price_tick_size").notNull(),
  quantityStep: numeric("quantity_step").notNull(),
  minQuantity: numeric("min_quantity").notNull(),
  tradingSessions: jsonb("trading_sessions"),
  isActive: boolean("is_active").notNull().default(true),
  metadata: jsonb("metadata"),
  createdAt: timestamp("created_at", { withTimezone: true, mode: "string" })
    .notNull()
    .defaultNow(),
  updatedAt: timestamp("updated_at", { withTimezone: true, mode: "string" })
    .notNull()
    .defaultNow(),
});
