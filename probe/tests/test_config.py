"""Smoke tests for :mod:`xspeed_probe.config`."""

from __future__ import annotations

import pytest

from xspeed_probe.config import ProbeSettings


def test_settings_requires_probe_token() -> None:
    settings = ProbeSettings.model_construct(
        probe_token="abc",
        server_url="wss://x:1/ws",
        probe_alias="",
        region_code=None,
        network_interface=None,
        speedtest_url="https://speed.cloudflare.com/__down?bytes=100",
        xray_socks_host="127.0.0.1",
        xray_socks_port=1080,
        xray_config_dir="/tmp",
        xray_ready_timeout=1.0,
        speedtest_timeout=1.0,
        reconnect_initial_delay=1.0,
        reconnect_max_delay=2.0,
        log_level="INFO",
        version="0.1.0",
    )
    assert settings.probe_token == "abc"
    assert settings.xray_socks_port == 1080


def test_settings_loads_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ALLOW_INSECURE_WS", raising=False)
    monkeypatch.delenv("XSPEED_ALLOW_INSECURE_WS", raising=False)
    monkeypatch.setenv("PROBE_TOKEN", "tok-123")
    monkeypatch.setenv("SERVER_URL", "ws://server:9000/ws")
    monkeypatch.setenv("PROBE_ALIAS", "alpha")
    monkeypatch.setenv("REGION_CODE", "msk")
    monkeypatch.setenv("NETWORK_INTERFACE", "eth0")
    monkeypatch.setenv("SPEEDTEST_URL", "https://example.test/speed")
    monkeypatch.setenv("ALLOW_INSECURE_WS", "true")

    settings = ProbeSettings()
    assert settings.probe_token == "tok-123"
    assert settings.server_url == "ws://server:9000/ws"
    assert settings.probe_alias == "alpha"
    assert settings.region_code == "msk"
    assert settings.network_interface == "eth0"
    assert settings.speedtest_url == "https://example.test/speed"


def test_settings_rejects_ws_url_in_production(monkeypatch: pytest.MonkeyPatch) -> None:
    """``ws://`` URLs are forbidden when no insecure override is provided (DAT-459, item g)."""
    monkeypatch.delenv("ALLOW_INSECURE_WS", raising=False)
    monkeypatch.delenv("XSPEED_ALLOW_INSECURE_WS", raising=False)
    monkeypatch.setenv("PROBE_TOKEN", "tok-123")
    monkeypatch.setenv("SERVER_URL", "ws://server:9000/ws")
    with pytest.raises(ValueError, match="wss://"):
        ProbeSettings()


def test_settings_rejects_unknown_scheme(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ALLOW_INSECURE_WS", raising=False)
    monkeypatch.delenv("XSPEED_ALLOW_INSECURE_WS", raising=False)
    monkeypatch.setenv("PROBE_TOKEN", "tok-123")
    monkeypatch.setenv("SERVER_URL", "ftp://server/ws")
    with pytest.raises(ValueError, match="ws:// or wss://"):
        ProbeSettings()
