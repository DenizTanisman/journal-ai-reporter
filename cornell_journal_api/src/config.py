"""Sidecar configuration."""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class SidecarSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    cornell_db_path: str = Field(default="cornell-diary.db")
    cornell_api_key: str = Field(default="")
    rate_limit_per_minute: int = 60
    allowed_origins: str = "http://localhost:8002"

    @property
    def allowed_origins_list(self) -> list[str]:
        return [o.strip() for o in self.allowed_origins.split(",") if o.strip()]


@lru_cache(maxsize=1)
def get_settings() -> SidecarSettings:
    return SidecarSettings()
