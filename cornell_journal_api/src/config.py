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

    # Diary FAZ 1.3 moved storage from SQLite to Postgres; the sidecar
    # now connects through asyncpg. Read-only is enforced via the
    # Postgres role permissions (deploy with a SELECT-only user).
    cornell_database_url: str = Field(
        default="postgres://diary_user:change_me_in_dev@127.0.0.1:5435/diary_db",
    )
    cornell_api_key: str = Field(default="")
    rate_limit_per_minute: int = 60
    allowed_origins: str = "http://localhost:8002"

    @property
    def allowed_origins_list(self) -> list[str]:
        return [o.strip() for o in self.allowed_origins.split(",") if o.strip()]


@lru_cache(maxsize=1)
def get_settings() -> SidecarSettings:
    return SidecarSettings()
