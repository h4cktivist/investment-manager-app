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

    # Макроэкономика на дашборде (ЦБ РФ + MOEX ISS, без ключей)
    macro_enabled: bool = True
    macro_cache_minutes: int = 180
    macro_cbr_base: str = "https://cbr.ru"  # без www: www.cbr.ru закрыт DDoS-Guard
    macro_ofz_tenor: float = 10.0            # срок ОФЗ для основной карточки, лет

    # Фоновые задачи
    sync_on_startup: bool = True
    history_sync_hour: int = 20           # МСК, ежедневная догрузка истории

    # Автоматическое зачисление купонов, амортизаций и погашений в сделки
    auto_post_payments: bool = True
    #: на сколько дней назад «догоняем» выплаты после простоя приложения
    auto_post_lookback_days: int = 45
    auto_post_interval_minutes: int = 60

    moex_iss_base: str = "https://iss.moex.com/iss"
    http_timeout: float = 20.0

    #: Свой CA-bundle для внешних API (корпоративный прокси и т. п.).
    #: Пусто — certifi + официальный корневой сертификат российского УЦ.
    ssl_ca_bundle: str = ""

    @property
    def cors_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
