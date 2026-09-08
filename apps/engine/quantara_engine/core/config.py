"""Application configuration — loads repo-root `.env` via pydantic-settings."""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

_REPO_ROOT = Path(__file__).resolve().parents[4]
_DEFAULT_LOCAL_DB = "postgresql://quantara:quantara@localhost:5432/quantara"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_REPO_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # PostgreSQL / Supabase
    database_url: str = _DEFAULT_LOCAL_DB
    direct_url: str = ""
    supabase_project: str = "QUANTARA"

    # Engine / FastAPI
    engine_host: str = "0.0.0.0"
    engine_port: int = 8000
    quantara_api_key: str = "dev-api-key"
    cors_origins: str = "http://localhost:3000"

    # Market data / workers
    market_data_provider: str = "mock"
    market_data_api_key: str = ""

    # Twelve Data (FX: XAU/USD, EUR/USD)
    # market_data_api_key above

    # Tiingo (US equities + crypto intraday)
    tiingo_api_key: str = ""

    # Alpaca (US equities + crypto)
    alpaca_api_key_id: str = ""
    alpaca_api_secret_key: str = ""
    alpaca_paper_base_url: str = "https://paper-api.alpaca.markets"
    alpaca_data_base_url: str = "https://data.alpaca.markets"
    alpaca_data_feed: str = "iex"

    # App defaults
    default_timezone: str = "Asia/Jerusalem"
    default_initial_capital: float = 10000.0

    # Paper trading
    paper_trading_enabled: bool = True
    paper_default_spread: float = 0.30
    paper_default_slippage_pct: float = 0.0001
    paper_default_fee_rate: float = 0.0
    paper_fill_timing: str = "next_open"

    @property
    def database_configured(self) -> bool:
        url = self.database_url.strip()
        return bool(url) and url != _DEFAULT_LOCAL_DB

    @property
    def migration_database_url(self) -> str:
        direct = self.direct_url.strip()
        if direct:
            return direct
        return self.database_url.strip()

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


settings = Settings()
