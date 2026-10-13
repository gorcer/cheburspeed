"""Application configuration via environment variables."""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration loaded from environment / .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    database_url: str = Field(
        default="postgresql+asyncpg://xspeed:xspeed@localhost:5432/xspeed",
        description="Async SQLAlchemy database URL.",
    )
    db_pool_min_size: int = Field(default=1, ge=1, description="asyncpg.Pool min size.")
    db_pool_max_size: int = Field(default=10, ge=1, description="asyncpg.Pool max size.")
    db_pool_command_timeout: float = Field(
        default=30.0,
        gt=0,
        description="Default command timeout for asyncpg connections, in seconds.",
    )

    jwt_secret: str = Field(
        default="change-me-in-prod",
        min_length=16,
        description="Secret used to sign JWT tokens.",
    )
    jwt_algorithm: str = Field(default="HS256")
    jwt_expires_minutes: int = Field(default=60 * 24)

    ws_host: str = Field(
        default="ws://localhost:8000",
        description="Public WebSocket host advertised to probes.",
    )
    frontend_origin: str = Field(
        default="http://localhost:5173",
        description="Allowed CORS origin for the SvelteKit frontend.",
    )

    log_level: str = Field(default="INFO")

    probe_online_after_seconds: int = Field(
        default=120,
        ge=1,
        description="Window in seconds that defines whether a probe is 'online'.",
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return cached application settings."""
    return Settings()