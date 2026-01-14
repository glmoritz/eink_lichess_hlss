"""
Application configuration using pydantic-settings.
"""

from functools import lru_cache
from typing import Literal

from pydantic import PostgresDsn
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # Application
    app_name: str = "eink-lichess-hlss"
    app_env: Literal["development", "staging", "production"] = "development"
    debug: bool = False
    secret_key: str = "change-me-in-production"

    # Server
    host: str = "0.0.0.0"
    port: int = 8000

    # Database
    database_url: PostgresDsn = "postgresql://lichess_hlss_root:password@localhost:5432/eink_lichess_hlss"  # type: ignore
    database_schema: str = "lichess"

    # LLSS Integration
    llss_base_url: str = "https://eink.tutu.eng.br/api"
    llss_api_token: str = ""

    # Lichess
    lichess_base_url: str = "https://lichess.org"

    # Display defaults
    default_display_width: int = 800
    default_display_height: int = 480
    default_display_bit_depth: int = 1

    @property
    def is_development(self) -> bool:
        return self.app_env == "development"

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()
