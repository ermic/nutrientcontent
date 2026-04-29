"""Settings loaded from .env via pydantic-settings.
Settings ingelezen uit .env via pydantic-settings."""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_version: str = "0.1.0"
    nevo_api_url: str
    api_key: str
    log_level: str = "info"


settings = Settings()
