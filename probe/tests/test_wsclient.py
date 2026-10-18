from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import Iterator
from typing import Any

import pytest

from xspeed_probe.config import ProbeSettings
from xspeed_probe.wsclient import ProbeClient


class FakeWebSocket:
    """Minimal stand-in for ``websockets.WebSocketClientProtocol``.

    Implements ``send``, ``recv`` and ``close``. ``recv`` returns queued
    messages until exhausted, then raises :class:`ConnectionClosed` so
    the client's reconnect loop can take over (or, with a stop event,
    exit cleanly).
    """

    def __init__(self, *, messages: list[str] | None = None) -> None:
        self.sent: list[str] = []
        self._messages: asyncio.Queue[str | None] = asyncio.Queue()
        for msg in messages or []:
            self._messages.put_nowait(msg)
        self.closed = False

    def push(self, msg: str) -> None:
        self._messages.put_nowait(msg)

    async def send(self, payload: str) -> None:
        self.sent.append(payload)

    async def recv(self) -> str:
        item = await self._messages.get()
        if item is None:
            raise _ClosedSentinelError
        return item

    async def close(self) -> None:
        self.closed = True
        self._messages.put_nowait(None)


class _ClosedSentinelError(Exception):
    """Raised by :meth:`FakeWebSocket.recv` to simulate server disconnect."""


@pytest.fixture
def fake_connect() -> Any:
    """Return a factory that monkey-patches ``websockets.connect``.

    The factory creates a fresh :class:`FakeWebSocket` per connect
    attempt and tracks them in ``attempts`` so tests can introspect the
    reconnection behaviour.
    """

    sockets: list[FakeWebSocket] = []

    def factory() -> FakeWebSocket:
        socket = FakeWebSocket()
        sockets.append(socket)
        return socket

    @contextlib.asynccontextmanager
    async def connect(
        url: str,
        **_kwargs: Any,
    ) -> Iterator[FakeWebSocket]:
        _ = url
        yield factory()

    return connect, sockets


def _settings(**overrides: Any) -> ProbeSettings:
    base = {
        "probe_token": "tok",
        "server_url": "wss://server/ws",
        "probe_alias": "lab",
        "region_code": "msk",
        "network_interface": None,
        "speedtest_url": "http://example.test/speed",
        "xray_socks_host": "127.0.0.1",
        "xray_socks_port": 1080,
        "xray_config_dir": "/tmp",
        "xray_ready_timeout": 1.0,
        "speedtest_timeout": 1.0,
        "reconnect_initial_delay": 0.01,
        "reconnect_max_delay": 0.05,
        "log_level": "INFO",
        "version": "0.1.0",
    }
    base.update(overrides)
    return ProbeSettings.model_construct(**base)


@pytest.fixture(autouse=True)
def _install_fake_connect(monkeypatch: pytest.MonkeyPatch, fake_connect: Any) -> None:
    import xspeed_probe.wsclient as wsclient_mod

    connect, _ = fake_connect
    monkeypatch.setattr(wsclient_mod.websockets, "connect", connect)


async def _drive(
    client: ProbeClient,
    *,
    expected_sends: int,
    timeout_seconds: float = 1.0,
) -> FakeWebSocket:
    """Run ``client`` until it has sent ``expected_sends`` messages or times out."""
    runner = asyncio.create_task(client.run_forever())
    deadline = asyncio.get_event_loop().time() + timeout_seconds
    while asyncio.get_event_loop().time() < deadline:
        if len(client._ws.sent) >= expected_sends if client._ws else False:  # type: ignore[union-attr]
            break
        await asyncio.sleep(0.01)
    client.stop()
    await client.close()
    await asyncio.wait_for(runner, timeout=1.0)
    assert client._ws is not None, "client never produced a socket"
    return client._ws  # type: ignore[return-value]


async def test_client_sends_online_then_result_on_task(fake_connect: Any) -> None:
    _connect, sockets = fake_connect
    handler_calls: list[tuple[int, str, str | None, str | None]] = []

    async def on_task(
        task_id: int,
        link: str,
        destination_url: str | None,
        probe_public_ip: str | None = None,
    ) -> tuple[float | None, str | None]:
        handler_calls.append((task_id, link, destination_url, probe_public_ip))
        return 12.5, None

    settings = _settings()
    client = ProbeClient(settings, on_task=on_task)
    runner = asyncio.create_task(client.run_forever())

    try:
        # Wait for the client to connect and queue its first message.
        for _ in range(50):
            if sockets and sockets[-1].sent:
                break
            await asyncio.sleep(0.01)
        assert sockets, "client never connected"
        sockets[-1].push(
            json.dumps(
                {
                    "type": "task.assigned",
                    "task_id": 7,
                    "link": "vless://x@example.com:443",
                    "destination_url": "https://speed.example.com/__down?bytes=100000000",
                    "probe_public_ip": "203.0.113.10",
                    "protocol_version": 1,
                }
            )
        )

        # Wait until the client replies with a result.
        for _ in range(50):
            if len(sockets[-1].sent) >= 2:
                break
            await asyncio.sleep(0.01)
    finally:
        client.stop()
        await client.close()
        await asyncio.wait_for(runner, timeout=1.0)

    assert handler_calls == [
        (
            7,
            "vless://x@example.com:443",
            "https://speed.example.com/__down?bytes=100000000",
            "203.0.113.10",
        )
    ]
    fake = sockets[-1]
    online = json.loads(fake.sent[0])
    assert online["type"] == "online"
    assert online["alias"] == "lab"
    assert online["region_code"] == "msk"
    assert online["protocol_version"] == 1
    result = json.loads(fake.sent[1])
    assert result["type"] == "result"
    assert result["task_id"] == 7
    assert result["speed_mbps"] == 12.5
    assert result["error"] is None


async def test_client_uses_destination_url_when_present(fake_connect: Any) -> None:
    """A populated destination_url must be passed through to the handler."""
    _connect, sockets = fake_connect
    handler_calls: list[str | None] = []

    async def on_task(
        task_id: int,
        link: str,
        destination_url: str | None,
        probe_public_ip: str | None = None,
    ) -> tuple[float | None, str | None]:
        handler_calls.append(destination_url)
        return 1.0, None

    client = ProbeClient(_settings(), on_task=on_task)
    runner = asyncio.create_task(client.run_forever())

    try:
        for _ in range(50):
            if sockets and sockets[-1].sent:
                break
            await asyncio.sleep(0.01)
        sockets[-1].push(
            json.dumps(
                {
                    "type": "task.assigned",
                    "task_id": 3,
                    "link": "vless://x@example.com:443",
                    "destination_url": "https://override.example.test/__down",
                }
            )
        )
        for _ in range(50):
            if len(sockets[-1].sent) >= 2:
                break
            await asyncio.sleep(0.01)
    finally:
        client.stop()
        await client.close()
        await asyncio.wait_for(runner, timeout=1.0)

    assert handler_calls == ["https://override.example.test/__down"]


async def test_client_falls_back_when_destination_url_absent(fake_connect: Any) -> None:
    """Tasks without destination_url must reach the handler with ``None``."""
    _connect, sockets = fake_connect
    handler_calls: list[str | None] = []

    async def on_task(
        task_id: int,
        link: str,
        destination_url: str | None,
        probe_public_ip: str | None = None,
    ) -> tuple[float | None, str | None]:
        handler_calls.append(destination_url)
        return 1.0, None

    client = ProbeClient(_settings(), on_task=on_task)
    runner = asyncio.create_task(client.run_forever())

    try:
        for _ in range(50):
            if sockets and sockets[-1].sent:
                break
            await asyncio.sleep(0.01)
        sockets[-1].push(
            json.dumps(
                {
                    "type": "task.assigned",
                    "task_id": 4,
                    "link": "vless://x@example.com:443",
                }
            )
        )
        for _ in range(50):
            if len(sockets[-1].sent) >= 2:
                break
            await asyncio.sleep(0.01)
    finally:
        client.stop()
        await client.close()
        await asyncio.wait_for(runner, timeout=1.0)

    assert handler_calls == [None]


async def test_client_reports_netif_unavailable_error(fake_connect: Any) -> None:
    _connect, sockets = fake_connect

    async def on_task(
        task_id: int,
        link: str,
        destination_url: str | None,
        probe_public_ip: str | None = None,
    ) -> tuple[float | None, str | None]:
        return None, "netif_unavailable"

    settings = _settings(network_interface="lo")
    client = ProbeClient(settings, on_task=on_task)
    runner = asyncio.create_task(client.run_forever())

    try:
        for _ in range(50):
            if sockets and sockets[-1].sent:
                break
            await asyncio.sleep(0.01)
        sockets[-1].push(
            json.dumps(
                {
                    "type": "task.assigned",
                    "task_id": 1,
                    "link": "vless://x@example.com:443",
                }
            ),
        )
        for _ in range(50):
            if len(sockets[-1].sent) >= 2:
                break
            await asyncio.sleep(0.01)
    finally:
        client.stop()
        await client.close()
        await asyncio.wait_for(runner, timeout=1.0)

    result = json.loads(sockets[-1].sent[1])
    assert result["speed_mbps"] is None
    assert result["error"] == "netif_unavailable"


async def test_client_ignores_unknown_message_types(fake_connect: Any) -> None:
    _connect, sockets = fake_connect

    async def on_task(
        task_id: int,
        link: str,
        destination_url: str | None,
        probe_public_ip: str | None = None,
    ) -> tuple[float | None, str | None]:
        raise AssertionError("handler should not be called")

    settings = _settings()
    client = ProbeClient(settings, on_task=on_task)
    runner = asyncio.create_task(client.run_forever())

    try:
        for _ in range(50):
            if sockets and sockets[-1].sent:
                break
            await asyncio.sleep(0.01)
        sockets[-1].push(json.dumps({"type": "heartbeat", "ts": 1}))
        sockets[-1].push(json.dumps({"not_a_type": "yes"}))
        sockets[-1].push(json.dumps("not even a dict"))

        # Give the client a chance to drain the bad messages.
        await asyncio.sleep(0.05)
        client.stop()
        await client.close()
        await asyncio.wait_for(runner, timeout=1.0)
    finally:
        pass

    assert len(sockets[-1].sent) == 1
    assert json.loads(sockets[-1].sent[0])["type"] == "online"


async def test_client_handler_exception_reports_handler_error(fake_connect: Any) -> None:
    _connect, sockets = fake_connect

    async def on_task(
        task_id: int,
        link: str,
        destination_url: str | None,
        probe_public_ip: str | None = None,
    ) -> tuple[float | None, str | None]:
        raise RuntimeError("boom")

    settings = _settings()
    client = ProbeClient(settings, on_task=on_task)
    runner = asyncio.create_task(client.run_forever())

    try:
        for _ in range(50):
            if sockets and sockets[-1].sent:
                break
            await asyncio.sleep(0.01)
        sockets[-1].push(
            json.dumps(
                {
                    "type": "task.assigned",
                    "task_id": 42,
                    "link": "vless://x@example.com:443",
                }
            ),
        )
        for _ in range(50):
            if len(sockets[-1].sent) >= 2:
                break
            await asyncio.sleep(0.01)
    finally:
        client.stop()
        await client.close()
        await asyncio.wait_for(runner, timeout=1.0)

    result = json.loads(sockets[-1].sent[1])
    assert result["task_id"] == 42
    assert result["speed_mbps"] is None
    assert result["error"] is not None
    assert "handler_error" in result["error"]


async def test_client_reconnects_after_disconnect(fake_connect) -> None:
    _connect, sockets = fake_connect
    client = ProbeClient(_settings(), on_task=lambda *_: (None, None))
    runner = asyncio.create_task(client.run_forever())

    try:
        # First socket: send empty (close immediately), then a task.
        for _ in range(50):
            if sockets:
                break
            await asyncio.sleep(0.01)
        first = sockets[0]
        await first.close()  # sentinel → simulates disconnect
        # Client should reconnect — wait for the second socket to appear.
        for _ in range(100):
            if len(sockets) >= 2 and sockets[1].sent:
                break
            await asyncio.sleep(0.01)
    finally:
        client.stop()
        await client.close()
        await asyncio.wait_for(runner, timeout=1.0)

    assert len(sockets) >= 2
    assert json.loads(sockets[1].sent[0])["type"] == "online"


async def test_client_resends_online_to_poll_queue(fake_connect: Any) -> None:
    _connect, sockets = fake_connect

    settings = _settings(idle_keepalive_seconds=0.05)
    client = ProbeClient(settings, on_task=lambda *_: (None, None))
    runner = asyncio.create_task(client.run_forever())

    try:
        for _ in range(50):
            if sockets and sockets[-1].sent:
                break
            await asyncio.sleep(0.01)
        # After at least one keepalive tick the probe must have
        # re-sent its ``online`` handshake without any server push.
        for _ in range(100):
            sent = sockets[0].sent
            if len([m for m in sent if json.loads(m)["type"] == "online"]) >= 2:
                break
            await asyncio.sleep(0.01)
    finally:
        client.stop()
        await client.close()
        await asyncio.wait_for(runner, timeout=1.0)

    online_sends = [json.loads(m) for m in sockets[0].sent if json.loads(m)["type"] == "online"]
    assert len(online_sends) >= 2
