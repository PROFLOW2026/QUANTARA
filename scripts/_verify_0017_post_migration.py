"""Post-0017 quantara_prod verification — read-only."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine, text


def _load_database_url() -> str:
    for line in Path(__file__).resolve().parents[1].joinpath(".env").read_text(encoding="utf-8").splitlines():
        if line.strip().startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip() in ("DATABASE_URL", "DIRECT_URL"):
            return value.strip().strip('"')
    raise RuntimeError("DATABASE_URL not found in .env")


def main() -> None:
    engine = create_engine(_load_database_url())
    with engine.connect() as conn:
        flags = conn.execute(
            text(
                """
                SELECT slug, multi_broker_mode_enabled, equal_asset_allocation_enabled, target_capital
                FROM owner_trading_portfolios WHERE slug = 'live-sim-owner'
                """
            )
        ).mappings().first()
        assets = conn.execute(
            text(
                """
                SELECT COUNT(*) AS n,
                       COUNT(*) FILTER (WHERE enabled) AS enabled_n,
                       MIN(starting_allocated_capital) AS min_start,
                       MAX(starting_allocated_capital) AS max_start
                FROM owner_portfolio_asset_allocations a
                JOIN owner_trading_portfolios p ON p.id = a.owner_portfolio_id
                WHERE p.slug = 'live-sim-owner'
                """
            )
        ).mappings().first()
        brokers = conn.execute(
            text(
                """
                SELECT ba.slug, pba.allocated_capital, pba.enabled, pba.is_legacy_primary
                FROM portfolio_broker_accounts pba
                JOIN broker_accounts ba ON ba.id = pba.broker_account_id
                JOIN owner_trading_portfolios p ON p.id = pba.owner_portfolio_id
                WHERE p.slug = 'live-sim-owner'
                ORDER BY ba.slug
                """
            )
        ).mappings().all()
        research_count = conn.execute(text("SELECT COUNT(*) FROM portfolios")).scalar()
        run = conn.execute(
            text(
                """
                SELECT id::text, status, started_at::text
                FROM paper_runs
                WHERE id = '231d0c57-e991-4008-914c-aac92ab2bf2c'
                """
            )
        ).mappings().first()
        live = conn.execute(
            text(
                """
                SELECT slug, is_active, account_state, equity
                FROM broker_accounts WHERE slug = 'live-sim-10k'
                """
            )
        ).mappings().first()
        cash_chk = conn.execute(
            text(
                """
                SELECT conname FROM pg_constraint
                WHERE conrelid = 'owner_portfolio_asset_allocations'::regclass
                  AND conname = 'owner_asset_cash_nonneg'
                """
            )
        ).scalar()
        integrity_fn = conn.execute(
            text(
                """
                SELECT proname FROM pg_proc
                WHERE proname IN (
                  'check_equal_asset_portfolio_integrity',
                  'enforce_asset_broker_referential',
                  'enforce_asset_row_immutable_when_active'
                )
                ORDER BY proname
                """
            )
        ).scalars().all()

    print("FLAGS", dict(flags))
    print("ASSETS", dict(assets))
    print("BROKERS", [dict(b) for b in brokers])
    print("RESEARCH_PORTFOLIOS", research_count)
    print("RESEARCH_RUN", dict(run) if run else None)
    print("LIVE_SIM", dict(live) if live else None)
    print("CASH_NONNEG_CONSTRAINT", cash_chk)
    print("INTEGRITY_FUNCTIONS", integrity_fn)


if __name__ == "__main__":
    main()
