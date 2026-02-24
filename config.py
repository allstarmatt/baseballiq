"""
Central config — reads all environment variables from .env
Import `settings` anywhere you need a config value.
"""

from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    # App
    app_env: str = "development"
    app_port: int = 8000

    # Database
    database_url: str = ""

    # External APIs
    odds_api_key: str = ""
    odds_api_base_url: str = "https://api.the-odds-api.com/v4"

    weather_api_key: str = ""
    weather_api_base_url: str = "https://api.tomorrow.io/v4"

    mlb_api_base_url: str = "https://statsapi.mlb.com/api/v1"

    # Redis
    redis_url: str = "redis://localhost:6379"

    # CORS
    frontend_url: str = "http://localhost:3000"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


@lru_cache()
def get_settings() -> Settings:
    """Cached settings instance — only reads .env once."""
    return Settings()


settings = get_settings()
