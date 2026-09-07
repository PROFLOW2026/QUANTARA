#!/usr/bin/env python3
"""Static check: SQL migration tables/columns vs SQLAlchemy ORM models."""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "apps" / "engine"
MIGRATION = ROOT / "packages" / "db" / "migrations" / "0001_initial.sql"
DRIZZLE_SCHEMA = ROOT / "packages" / "db" / "schema"

sys.path.insert(0, str(ENGINE))

from sqlalchemy.orm import DeclarativeBase  # noqa: E402

import quantara_engine.models as models_pkg  # noqa: E402
from quantara_engine.models.base import Base  # noqa: E402


def parse_sql_schema(sql_text: str) -> dict[str, set[str]]:
    tables: dict[str, set[str]] = {}
    current: str | None = None
    for line in sql_text.splitlines():
        stripped = line.strip()
        if stripped.upper().startswith("CREATE TABLE "):
            match = re.match(r"CREATE TABLE\s+(\w+)\s*\(", stripped, re.I)
            if match:
                current = match.group(1)
                tables[current] = set()
            continue
        if current and stripped.startswith(")"):
            current = None
            continue
        if current and stripped and not stripped.startswith("--"):
            col_match = re.match(r"^(\w+)\s+", stripped)
            if col_match:
                col = col_match.group(1)
                if col.upper() not in ("CONSTRAINT", "PRIMARY", "UNIQUE", "CHECK", "FOREIGN"):
                    tables[current].add(col)
    return tables


def parse_drizzle_schema(schema_dir: Path) -> dict[str, set[str]]:
    """Parse Drizzle pgTable definitions for table/column names."""
    tables: dict[str, set[str]] = {}
    col_pattern = re.compile(
        r'(?:uuid|text|varchar|integer|numeric|jsonb|boolean|timestamp|[a-zA-Z]+Enum)\s*\(\s*"([a-z_0-9]+)"',
        re.MULTILINE,
    )
    table_pattern = re.compile(r'pgTable\s*\(\s*"(\w+)"')

    for path in sorted(schema_dir.glob("*.ts")):
        if path.name in ("index.ts", "enums.ts"):
            continue
        text = path.read_text(encoding="utf-8")
        for table_match in table_pattern.finditer(text):
            table = table_match.group(1)
            start = table_match.end()
            next_table = table_pattern.search(text, start)
            block = text[start : next_table.start() if next_table else len(text)]
            cols = set(col_pattern.findall(block))
            tables[table] = cols
    return tables


def collect_orm_schema(base: type[DeclarativeBase]) -> dict[str, set[str]]:
    tables: dict[str, set[str]] = {}
    for mapper in base.registry.mappers:
        table = mapper.local_table
        if table is None:
            continue
        name = table.name
        cols: set[str] = set()
        for column in table.columns:
            cols.add(column.key)
            if column.key != column.name:
                cols.add(column.name)
        tables[name] = cols
    return tables


def main() -> int:
    if not MIGRATION.exists():
        print(f"FAIL: migration not found at {MIGRATION}")
        return 1

    sql_schema = parse_sql_schema(MIGRATION.read_text(encoding="utf-8"))
    orm_schema = collect_orm_schema(Base)
    drizzle_schema = parse_drizzle_schema(DRIZZLE_SCHEMA)

    sql_tables = set(sql_schema)
    orm_tables = set(orm_schema)
    drizzle_tables = set(drizzle_schema)

    errors: list[str] = []

    missing_in_orm = sorted(sql_tables - orm_tables)
    extra_in_orm = sorted(orm_tables - sql_tables)
    if missing_in_orm:
        errors.append(f"Tables in SQL missing ORM models: {missing_in_orm}")
    if extra_in_orm:
        errors.append(f"ORM models missing SQL tables: {extra_in_orm}")

    missing_drizzle = sorted(sql_tables - drizzle_tables)
    extra_drizzle = sorted(drizzle_tables - sql_tables)
    if missing_drizzle:
        errors.append(f"Tables in SQL missing Drizzle schema: {missing_drizzle}")
    if extra_drizzle:
        errors.append(f"Drizzle tables missing SQL: {extra_drizzle}")

    for table in sorted(sql_tables & orm_tables):
        sql_cols = {c for c in sql_schema[table] if c not in ("id",)}
        orm_cols = {c for c in orm_schema[table] if c not in ("id",)}
        # metadata column mapped as metadata_ in ORM
        if "metadata" in sql_cols and "metadata_" in orm_cols:
            sql_cols.discard("metadata")
            orm_cols.discard("metadata_")

        missing_cols = sorted(sql_cols - orm_cols)
        extra_cols = sorted(orm_cols - sql_cols)
        if missing_cols:
            errors.append(f"{table}: SQL columns missing in ORM: {missing_cols}")
        if extra_cols:
            errors.append(f"{table}: ORM columns missing in SQL: {extra_cols}")

    for table in sorted(sql_tables & drizzle_tables):
        sql_cols = {c for c in sql_schema[table] if c not in ("id",)}
        drizzle_cols = {c for c in drizzle_schema[table] if c not in ("id",)}
        missing_d_cols = sorted(sql_cols - drizzle_cols)
        extra_d_cols = sorted(drizzle_cols - sql_cols)
        if missing_d_cols:
            errors.append(f"{table}: SQL columns missing in Drizzle: {missing_d_cols}")
        if extra_d_cols:
            errors.append(f"{table}: Drizzle columns missing in SQL: {extra_d_cols}")

    if errors:
        print("FAIL")
        for err in errors:
            print(f"  - {err}")
        return 1

    print("PASS")
    print(f"  SQL <-> ORM: {len(sql_tables)} tables, {len(models_pkg.__all__)} ORM exports")
    print(f"  SQL <-> Drizzle: {len(drizzle_tables)} tables")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
