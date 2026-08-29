from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings, loaded from the environment."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "Realtime Monitoring System API"
    app_version: str = "0.1.0"
    log_level: str = "INFO"

    db_host: str = "db"
    db_port: int = 3306
    db_user: str = "monitoring"
    db_password: str = "monitoring"
    db_name: str = "monitoring"

    db_pool_size: int = 10
    db_max_overflow: int = 5
    db_pool_recycle: int = 1800

    # No default: the application must refuse to start without a real secret.
    jwt_secret_key: str
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60

    @property
    def database_url(self) -> str:
        return (
            f"mysql+asyncmy://{self.db_user}:{self.db_password}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}"
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
