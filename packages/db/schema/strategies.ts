import {
  boolean,
  integer,
  jsonb,
  pgTable,
  text,
  timestamp,
  unique,
  uuid,
  varchar,
} from "drizzle-orm/pg-core";
import {
  riskProfileSlugEnum,
  strategyStatusEnum,
  timeframeEnum,
} from "./enums";

export const strategies = pgTable("strategies", {
  id: uuid("id").primaryKey().defaultRandom(),
  slug: varchar("slug", { length: 50 }).notNull().unique(),
  name: varchar("name", { length: 100 }).notNull(),
  description: text("description"),
  supportedInstruments: uuid("supported_instruments").array().notNull(),
  supportedTimeframes: timeframeEnum("supported_timeframes").array().notNull(),
  status: strategyStatusEnum("status").notNull().default("draft"),
  createdAt: timestamp("created_at", { withTimezone: true, mode: "string" })
    .notNull()
    .defaultNow(),
  updatedAt: timestamp("updated_at", { withTimezone: true, mode: "string" })
    .notNull()
    .defaultNow(),
});

export const strategyVersions = pgTable(
  "strategy_versions",
  {
    id: uuid("id").primaryKey().defaultRandom(),
    strategyId: uuid("strategy_id")
      .notNull()
      .references(() => strategies.id),
    version: varchar("version", { length: 20 }).notNull(),
    versionMajor: integer("version_major").notNull(),
    versionMinor: integer("version_minor").notNull(),
    versionPatch: integer("version_patch").notNull(),
    parameters: jsonb("parameters").notNull(),
    parametersSchema: jsonb("parameters_schema").notNull(),
    riskProfileCompatibility: riskProfileSlugEnum(
      "risk_profile_compatibility",
    ).array().notNull(),
    logicHash: varchar("logic_hash", { length: 64 }).notNull(),
    changelog: text("changelog"),
    isActive: boolean("is_active").notNull().default(true),
    createdAt: timestamp("created_at", { withTimezone: true, mode: "string" })
      .notNull()
      .defaultNow(),
  },
  (table) => [
    unique("strategy_versions_strategy_id_version_unique").on(
      table.strategyId,
      table.version,
    ),
  ],
);
