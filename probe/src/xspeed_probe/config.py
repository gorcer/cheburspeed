"""Probe configuration via environment variables."""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class ProbeSettings(BaseSettings):
    """Runtime configuration loaded from environment / .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    probe_token: str = Field(
        ...,
        description="Token used to authenticate this probe against the server.",
    )
    server_ws_url: str = Field(
        default="ws://localhost:8000/ws/probe",
        description="WebSocket endpoint exposed by the XSpeed server.",
    )
    alias: str = Field(default="probe")
    region_id: int | None = Field(default=None)

    network_interface: str | None = Field(
        default=None,
        description=(
            "Optional outbound network interface. "
            "If set but unavailable on the host the probe skips the task."
        ),
    )
    reconnect_delay_seconds: float = Field(default=2.0, ge=0.1)
    log_level: str = Field(default="INFO")


@lru_cache(maxsize=1)
def get_settings() -> ProbeSettings:
    """Return cached probe settings."""
    return ProbeSettings()  # type: ignore[call-arg]