"""Probe configuration via environment variables."""

from __future__ import annotations

import os
from typing import Any

from pydantic import AliasChoices, Field, field_validator
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

    allow_insecure_ws: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "allow_insecure_ws",
            "XSPEED_ALLOW_INSECURE_WS",
            "ALLOW_INSECURE_WS",
        ),
        description=(
            "Allow ``ws://`` server URLs. Must stay False in production "
            "(DAT-459, item g) — the bearer token would otherwise travel "
            "in clear text. Set to True only for local development."
        ),
    )
    probe_token: str = Field(
        ...,
        description="Token used to authenticate this probe against the server.",
    )
    server_url: str = Field(
        default="wss://server/ws",
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
    awgproxy_binary: str = Field(
        default="/usr/local/bin/awgsocks",
        description=(
            "AmneziaWG userspace helper shipped in the probe image. It "
            "exposes the tunnel as a SOCKS5 proxy on the same "
            "``xray_socks_host``/``xray_socks_port`` pair xray uses, so "
            "both protocols share one local proxy port."
        ),
    )
    awg_ready_timeout: float = Field(
        default=15.0,
        gt=0.0,
        description=(
            "Seconds to wait for the AmneziaWG tunnel's SOCKS port. "
            "Higher than xray's because the handshake with the tunnel "
            "endpoint adds a round trip on top of process startup."
        ),
    )
    speedtest_timeout: float = Field(
        default=20.0,
        gt=0.0,
        description=(
            "Maximum wall-clock seconds spent streaming the speedtest "
            "response. When the budget is exhausted the probe returns "
            "the partial throughput instead of failing (DAT-465)."
        ),
    )
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
    refuse_self_ip_speedtests: bool = Field(
        default=True,
        description=(
            "When True (default) the probe refuses to run a speedtest "
            "against a destination whose host resolves to the probe's "
            "own public IP — which would otherwise produce a tautological "
            "measurement and waste the upstream tunnel (DAT-459)."
        ),
    )

    @field_validator("server_url")
    @classmethod
    def _check_server_url(cls, value: str, info: Any) -> str:
        """Reject ``ws://`` URLs unless ``allow_insecure_ws`` is explicitly set.

        The probe always carries a bearer token: ``ws://`` would leak it
        on every reconnect. ``wss://`` is enforced by default; the dev
        escape hatch is set through ``ALLOW_INSECURE_WS=true`` (or the
        ``XSPEED_ALLOW_INSECURE_WS`` env var for the bash wrapper).
        """
        if not value:
            raise ValueError("SERVER_URL must not be empty")
        lowered = value.lower()
        if lowered.startswith("wss://") or lowered.startswith("ws+unix://"):
            return value
        if lowered.startswith("ws://"):
            # ``allow_insecure_ws`` is declared first but may not be
            # populated yet — fall back to the env vars so test code
            # that only mutates ``os.environ`` still works.
            raw_allow = info.data.get("allow_insecure_ws") if info.data else None
            env_allow = (
                os.environ.get("ALLOW_INSECURE_WS", "")
                or os.environ.get("XSPEED_ALLOW_INSECURE_WS", "")
            ).lower() in {"1", "true", "yes"}
            if bool(raw_allow) or env_allow:
                return value
            raise ValueError(
                "SERVER_URL must use wss:// in production; set "
                "ALLOW_INSECURE_WS=true only for local development",
            )
        raise ValueError(
            f"SERVER_URL must start with ws:// or wss:// (got {value[:32]!r})",
        )


def get_settings() -> ProbeSettings:
    """Return a fresh settings instance.

    Unlike a cached factory, this returns a new object on every invocation
    so tests can patch the process environment and observe the change
    without inter-test pollution.
    """
    return ProbeSettings()  # type: ignore[call-arg]
