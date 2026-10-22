"""Tests for the protocol branch in :func:`main._run_task`.

``task.assigned`` carries a ``protocol``; the probe must pick the right
tunnel manager, keep the shared self-IP guard and teardown, and refuse a
protocol it does not know instead of guessing.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from xspeed_probe.awg import AwgManager
from xspeed_probe.config import ProbeSettings
from xspeed_probe.main import _build_handler, _run_task
from xspeed_probe.xray import XrayManager

VLESS_LINK = (
    "vless://11111111-2222-3333-4444-555555555555@example.com:443"
    "?security=reality&sni=cdn.example.com&type=tcp#demo"
)
AWG_LINK = (
    "[Interface]\n"
    "PrivateKey = cNx0N+nxLtxHhbHG1M1tM8pDvDNnOWGbkpHFrTnHqAA=\n"
    "Address = 10.8.40.106/32\n"
    "\n"
    "[Peer]\n"
    "PublicKey = fgI6VqMCIvjErRo2vL6XQhDqfBiK+FTxLBRLxMCCZVU=\n"
    "Endpoint = in.oneyearsolution.com:40974\n"
    "AllowedIPs = 0.0.0.0/0\n"
)


def _settings(**overrides: Any) -> ProbeSettings:
    base: dict[str, Any] = {
        "probe_token": "tok",
        "server_url": "wss://server/ws",
        "probe_alias": "lab",
        "region_code": "msk",
        "network_interface": None,
        "speedtest_url": "https://speed.example.com/__down",
        "xray_socks_host": "127.0.0.1",
        "xray_socks_port": 1080,
        "xray_config_dir": "/tmp",
        "xray_ready_timeout": 1.0,
        "speedtest_timeout": 1.0,
        "reconnect_initial_delay": 1.0,
        "reconnect_max_delay": 2.0,
        "log_level": "INFO",
        "version": "0.1.0",
    }
    base.update(overrides)
    return ProbeSettings.model_construct(**base)  # type: ignore[arg-type]


class _RecordingTunnel:
    """Stands in for XrayManager/AwgManager inside ``_run_task``."""

    def __init__(self, ready: bool = True) -> None:
        self.ready = ready
        self.started: list[tuple[int, str, dict[str, Any]]] = []
        self.terminated: list[int] = []

    @property
    def socks_url(self) -> str:
        return "socks5://127.0.0.1:1080"

    def start(self, task_id: int, link: str, **kwargs: Any) -> None:
        self.started.append((task_id, link, kwargs))

    async def wait_ready(self, task_id: int) -> bool:
        return self.ready

    def terminate(self, task_id: int) -> None:
        self.terminated.append(task_id)


@pytest.fixture
def measured() -> Any:
    with patch("xspeed_probe.main.measure") as measure:

        async def fake_measure(*_args: Any, **_kwargs: Any) -> Any:
            class _Result:
                speed_mbps = 42.5
                elapsed_seconds = 1.0
                bytes_downloaded = 5_000_000

            return _Result()

        measure.side_effect = fake_measure
        yield measure


async def test_vless_task_uses_xray(measured: Any) -> None:
    tunnel = _RecordingTunnel()
    speed, error = await _run_task(
        1,
        VLESS_LINK,
        settings=_settings(),
        xray=tunnel,  # type: ignore[arg-type]
        awg=_RecordingTunnel(),  # type: ignore[arg-type]
        protocol="vless",
    )
    assert (speed, error) == (42.5, None)
    assert len(tunnel.started) == 1
    assert tunnel.started[0][1] == VLESS_LINK
    # xray takes both the source address and the NIC name.
    assert tunnel.started[0][2] == {"send_through": None}
    assert tunnel.terminated == [1]


async def test_amneziawg_task_uses_awg_manager(measured: Any) -> None:
    xray = _RecordingTunnel()
    awg = _RecordingTunnel()
    speed, error = await _run_task(
        2,
        AWG_LINK,
        settings=_settings(),
        xray=xray,  # type: ignore[arg-type]
        awg=awg,  # type: ignore[arg-type]
        protocol="amneziawg",
    )
    assert (speed, error) == (42.5, None)
    assert xray.started == []
    assert len(awg.started) == 1
    assert awg.started[0][1] == AWG_LINK
    # awgsocks only understands the NIC name.
    assert awg.started[0][2] == {}
    assert awg.terminated == [2]


async def test_amneziawg_task_forwards_network_interface(measured: Any) -> None:
    awg = _RecordingTunnel()
    with (
        patch("xspeed_probe.main.ensure_interface_available", return_value=True),
        patch("xspeed_probe.main.resolve_interface_ip", return_value="10.0.0.5"),
    ):
        await _run_task(
            3,
            AWG_LINK,
            settings=_settings(network_interface="usbphone0"),
            xray=_RecordingTunnel(),  # type: ignore[arg-type]
            awg=awg,  # type: ignore[arg-type]
            protocol="amneziawg",
        )
    assert awg.started[0][2] == {"network_interface": "usbphone0"}


async def test_vless_task_forwards_interface_ip_as_send_through(measured: Any) -> None:
    xray = _RecordingTunnel()
    with (
        patch("xspeed_probe.main.ensure_interface_available", return_value=True),
        patch("xspeed_probe.main.resolve_interface_ip", return_value="10.0.0.5"),
    ):
        await _run_task(
            4,
            VLESS_LINK,
            settings=_settings(network_interface="usbphone0"),
            xray=xray,  # type: ignore[arg-type]
            awg=_RecordingTunnel(),  # type: ignore[arg-type]
            protocol="vless",
        )
    assert xray.started[0][2] == {
        "send_through": "10.0.0.5",
        "network_interface": "usbphone0",
    }


async def test_unknown_protocol_is_reported_without_starting_a_tunnel(
    measured: Any,
) -> None:
    xray = _RecordingTunnel()
    speed, error = await _run_task(
        5,
        VLESS_LINK,
        settings=_settings(),
        xray=xray,  # type: ignore[arg-type]
        awg=_RecordingTunnel(),  # type: ignore[arg-type]
        protocol="wireguard",
    )
    assert speed is None
    assert error is not None
    assert error.startswith("awg_unsupported_protocol:")
    assert xray.started == []


async def test_amneziawg_start_failure_surfaces_the_reason() -> None:
    class _Failing(_RecordingTunnel):
        def start(self, task_id: int, link: str, **kwargs: Any) -> None:
            raise ValueError("amneziawg config has no [Interface] section")

    speed, error = await _run_task(
        6,
        "garbage",
        settings=_settings(),
        xray=_RecordingTunnel(),  # type: ignore[arg-type]
        awg=_Failing(),  # type: ignore[arg-type]
        protocol="amneziawg",
    )
    assert speed is None
    assert error is not None
    assert error.startswith("awg_start_failed:")
    assert "[Interface]" in error


async def test_tunnel_not_ready_still_terminates() -> None:
    awg = _RecordingTunnel(ready=False)
    speed, error = await _run_task(
        7,
        AWG_LINK,
        settings=_settings(),
        xray=_RecordingTunnel(),  # type: ignore[arg-type]
        awg=awg,  # type: ignore[arg-type]
        protocol="amneziawg",
    )
    assert (speed, error) == (None, "awg_not_ready")
    assert awg.terminated == [7]


async def test_self_ip_guard_blocks_both_protocols() -> None:
    settings = _settings()
    resolved: list[tuple[int, int, int, str, tuple[str, int]]] = [
        (2, 1, 6, "", ("203.0.113.10", 0)),
    ]
    for protocol, link in (("vless", VLESS_LINK), ("amneziawg", AWG_LINK)):
        tunnel = _RecordingTunnel()
        with patch(
            "xspeed_probe.main.socket.getaddrinfo",
            return_value=resolved,
        ):
            speed, error = await _run_task(
                8,
                link,
                settings=settings,
                xray=tunnel,  # type: ignore[arg-type]
                awg=tunnel,  # type: ignore[arg-type]
                protocol=protocol,
                speedtest_url="https://self.example.com/__down",
                probe_public_ip="203.0.113.10",
            )
        assert (speed, error) == (None, "self_ip_forbidden"), protocol
        assert tunnel.started == [], protocol


def test_handler_threads_protocol_through() -> None:
    """The WS handler passes the envelope's protocol down to ``_run_task``."""
    import inspect

    handler = _build_handler(
        _settings(),
        XrayManager(config_dir="/tmp"),
        AwgManager(config_dir="/tmp"),
    )
    assert "protocol" in inspect.signature(handler).parameters


def test_managers_share_the_local_socks_port() -> None:
    xray = XrayManager(socks_host="127.0.0.1", socks_port=1080)
    awg = AwgManager(socks_host="127.0.0.1", socks_port=1080)
    assert xray.socks_url == awg.socks_url
