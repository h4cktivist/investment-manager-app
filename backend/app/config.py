from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://invest:invest@localhost:5433/invest"
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    log_level: str = "INFO"

    # Котировки
    quote_provider: str = "moex"          # moex | tinvest
    quote_poll_seconds: int = 15
    tinvest_token: str = ""

    # Метрики
    risk_free_rate: float = 0.16          # доля годовых, 0.16 == 16%
    trading_days_per_year: int = 252

    # Фоновые задачи
    sync_on_startup: bool = True
    history_sync_hour: int = 20           # МСК, ежедневная догрузка истории

    moex_iss_base: str = "https://iss.moex.com/iss"
    http_timeout: float = 20.0

    @property
    def cors_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
