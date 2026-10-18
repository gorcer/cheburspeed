"""Probe configuration via environment variables."""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_SPEEDTEST_URL: str = "https://speed.cloudflare.com/__down?bytes=100000000"


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
    server_url: str = Field(
        default="ws://server:8080/ws",
        description="WebSocket endpoint exposed by the CheburSpeed server.",
    )
    probe_alias: str = Field(
        default="probe",
        description="Human-readable probe name reported to the server.",
    )
    region_code: str | None = Field(
        default=None,
        description="Region code the probe belongs to (matches `regions.code`).",
    )
    network_interface: str | None = Field(
        default=None,
        description=(
            "Optional outbound network interface. "
            "If set but unavailable on the host the probe skips the task."
        ),
    )
    speedtest_url: str = Field(
        default=DEFAULT_SPEEDTEST_URL,
        description="URL used to measure download throughput.",
    )
    xray_socks_host: str = Field(default="127.0.0.1")
    xray_socks_port: int = Field(default=1080, ge=1, le=65535)
    xray_config_dir: str = Field(default="/tmp")  # noqa: S108 - ephemeral xray configs
    xray_ready_timeout: float = Field(default=10.0, gt=0.0)
    speedtest_timeout: float = Field(default=30.0, gt=0.0)
    reconnect_initial_delay: float = Field(default=1.0, ge=0.1)
    reconnect_max_delay: float = Field(default=30.0, ge=1.0)
    idle_keepalive_seconds: float = Field(
        default=5.0,
        gt=0.0,
        description=(
            "Interval at which the probe re-sends its ``online`` message "
            "so the server polls the task queue while the probe is idle."
        ),
    )
    log_level: str = Field(default="INFO")
    version: str = Field(default="0.1.0")


def get_settings() -> ProbeSettings:
    """Return a fresh settings instance.

    Unlike a cached factory, this returns a new object on every invocation
    so tests can patch the process environment and observe the change
    without inter-test pollution.
    """
    return ProbeSettings()  # type: ignore[call-arg]
