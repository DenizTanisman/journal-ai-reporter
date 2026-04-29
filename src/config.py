"""Application configuration loaded from environment variables.

All secrets and deployment-specific values flow through `Settings`. Nothing
hardcoded — `.env` (gitignored) supplies real values, `.env.example` documents
the schema. Import `get_settings()` from anywhere and FastAPI dependencies will
share a single cached instance.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Cornell Journal API
    cornell_api_url: str = Field(default="http://localhost:8001")
    cornell_api_key: str = Field(default="")

    # Gemini
    gemini_api_key: str = Field(default="")
    gemini_model: str = Field(default="gemini-2.5-flash")

    # Internal auth (Jarvis <-> Reporter)
    internal_api_key: str = Field(default="")

    # CORS
    allowed_origins: str = Field(default="http://localhost:3000,http://localhost:8000")

    # Server
    app_env: Literal["development", "staging", "production"] = "development"
    app_debug: bool = False
    app_port: int = 8002
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"

    # Rate limiting
    rate_limit_per_minute: int = 20

    # HTTP timeouts
    http_timeout_seconds: float = 30.0
    gemini_timeout_seconds: float = 60.0

    @field_validator("cornell_api_url")
    @classmethod
    def _strip_trailing_slash(cls, v: str) -> str:
        return v.rstrip("/")

    @property
    def allowed_origins_list(self) -> list[str]:
        return [o.strip() for o in self.allowed_origins.split(",") if o.strip()]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
