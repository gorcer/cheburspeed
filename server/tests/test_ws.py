"""End-to-end tests for the ``/ws`` WebSocket endpoint.

The suite covers the four behaviours from the DAT-325.4 spec:

* invalid token closes the socket with code ``4001`` *before* it is
  accepted (no nginx log of the bearer token),
* a probe that announces itself with ``protocol_version`` below the
  minimum is bounced with code ``4002``,
* a probe that sends a valid ``online`` message receives a
  ``task.assigned`` envelope for a pending task,
* a probe that reports a ``result`` for the assigned task causes the
  task to flip to ``completed``.
"""

from __future__ import annotations

import json
import os
from typing import Any

import asyncpg
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from xspeed_server.app.main import create_app

VALID_LINK = (
    "vless://11111111-2222-3333-4444-555555555555@example.com:443"
    "?security=reality&sni=cdn.example.com&type=tcp#demo"
)


def _test_dsn() -> str:
    url = os.environ["TEST_DATABASE_URL"]
    if url.startswith("postgresql+asyncpg://"):
        return "postgresql://" + url[len("postgresql+asyncpg://") :]
    return url


async def _seed_region(code: str = "RU-MOW", title: str = "Москва") -> int:
    conn = await asyncpg.connect(_test_dsn())
    try:
        row = await conn.fetchrow(
            """
            INSERT INTO regions (code, title, lat, lon)
            VALUES ($1, $2, 55.7558, 37.6173)
            RETURNING id
            """,
            code,
            title,
        )
        return int(row["id"])
    finally:
        await conn.close()


async def _seed_probe(token: str, region_id: int) -> int:
    conn = await asyncpg.connect(_test_dsn())
    try:
        row = await conn.fetchrow(
            """
            INSERT INTO probes (alias, token, region_id, is_public)
            VALUES ($1, $2, $3, TRUE)
            RETURNING id
            """,
            f"probe-{token[:4]}",
            token,
            region_id,
        )
        return int(row["id"])
    finally:
        await conn.close()


async def _insert_task(link: str, probe_tokens: list[str] | None = None) -> int:
    conn = await asyncpg.connect(_test_dsn())
    try:
        row = await conn.fetchrow(
            """
            INSERT INTO tasks (link, status, probe_tokens)
            VALUES ($1, 'pending', $2::jsonb)
            RETURNING id
            """,
            link,
            None if probe_tokens is None else json.dumps(probe_tokens),
        )
        return int(row["id"])
    finally:
        await conn.close()


@pytest.fixture
def ws_client(clean_db: None, app_env: Any) -> Any:
    """Synchronous TestClient with the FastAPI lifespan active.

    ``TestClient`` is a context manager that runs the ASGI lifespan
    in its own background thread — running it here means the
    WebSocket handler can rely on ``app.state.db`` (asyncpg pool) and
    ``app.state.hub`` (probe registry) being fully populated for
    every test.
    """
    with TestClient(create_app()) as client:
        yield client


def test_ws_closes_with_4001_when_authorization_header_missing(ws_client: Any) -> None:
    """No ``Authorization`` header → reject before accepting."""
    with pytest.raises(WebSocketDisconnect) as exc_info:
        with ws_client.websocket_connect("/ws") as websocket:
            websocket.receive_json()
    assert exc_info.value.code == 4001


def test_ws_closes_with_4001_when_token_unknown(ws_client: Any) -> None:
    """Bearer token with no matching probe row → reject before accepting."""
    with pytest.raises(WebSocketDisconnect) as exc_info:
        with ws_client.websocket_connect(
            "/ws", headers={"Authorization": "Bearer not-a-real-token"}
        ) as websocket:
            websocket.receive_json()
    assert exc_info.value.code == 4001


async def test_public_ws_receives_task_created_event(ws_client: Any) -> None:
    """The browser-facing socket is anonymous and emits live task updates."""
    conn = await asyncpg.connect(_test_dsn())
    try:
        destination_row = await conn.fetchrow(
            """
            INSERT INTO destinations (name, url)
            VALUES ($1, $2)
            RETURNING id
            """,
            "Cloudflare",
            "https://speed.example.com/__down?bytes=100000000",
        )
        destination_id = int(destination_row["id"])
    finally:
        await conn.close()

    with ws_client.websocket_connect("/ws/public") as websocket:
        response = ws_client.post(
            "/api/v1/tasks",
            json={"link": VALID_LINK, "destination_id": destination_id},
        )
        event = websocket.receive_json()

    assert response.status_code == 201
    assert event["type"] == "task.created"
    assert event["payload"]["id"] == response.json()["id"]
    assert event["payload"]["status"] == "pending"


async def test_ws_accepts_probe_with_valid_token_and_assigns_task(
    ws_client: Any,
) -> None:
    """Valid token + ``online`` message → server pushes a task to the probe."""
    region_id = await _seed_region()
    token = "probe-token-accept-test"
    await _seed_probe(token, region_id)
    task_id = await _insert_task(VALID_LINK)

    with ws_client.websocket_connect(
        "/ws", headers={"Authorization": f"Bearer {token}"}
    ) as websocket:
        websocket.send_json(
            {
                "type": "online",
                "alias": "probe-A",
                "region_code": "RU-MOW",
                "version": "0.1.0",
                "netif": "eth0",
                "protocol_version": 1,
            }
        )
        envelope = websocket.receive_json()

    assert envelope["type"] == "task.assigned"
    assert envelope["task_id"] == task_id
    assert envelope["link"] == VALID_LINK
    assert envelope["protocol_version"] == 1


async def test_ws_closes_with_4002_when_protocol_version_below_minimum(
    ws_client: Any,
) -> None:
    """``protocol_version`` < 1 → reject with close code 4002."""
    region_id = await _seed_region(code="XSW-001", title="Test")
    token = "probe-token-version-test"
    await _seed_probe(token, region_id)

    with ws_client.websocket_connect(
        "/ws", headers={"Authorization": f"Bearer {token}"}
    ) as websocket:
        websocket.send_json(
            {
                "type": "online",
                "alias": "probe-A",
                "region_code": "XSW-001",
                "version": "0.1.0",
                "netif": "eth0",
                "protocol_version": 0,
            }
        )
        with pytest.raises(WebSocketDisconnect) as exc_info:
            websocket.receive_json()
    assert exc_info.value.code == 4002


async def test_ws_marks_task_completed_after_probe_reports_result(
    ws_client: Any,
) -> None:
    """Sending a valid ``result`` flips the task to ``completed``."""
    region_id = await _seed_region(code="XSW-002", title="Completion")
    token = "probe-token-completion-test"
    await _seed_probe(token, region_id)
    task_id = await _insert_task(VALID_LINK)
    second_task_id = await _insert_task(VALID_LINK)

    with ws_client.websocket_connect(
        "/ws", headers={"Authorization": f"Bearer {token}"}
    ) as websocket:
        websocket.send_json(
            {
                "type": "online",
                "alias": "probe-A",
                "region_code": "XSW-002",
                "version": "0.1.0",
                "netif": "eth0",
                "protocol_version": 1,
            }
        )
        envelope = websocket.receive_json()
        assert envelope["task_id"] == task_id

        websocket.send_json(
            {
                "type": "result",
                "task_id": task_id,
                "speed_mbps": 12.4,
                "error": None,
            }
        )
        # Wait for the server to dispatch the next pending task — this
        # proves it has finished persisting and processing the result
        # for the previous task.
        next_envelope = websocket.receive_json()
        assert next_envelope["task_id"] == second_task_id

    conn = await asyncpg.connect(_test_dsn())
    try:
        row = await conn.fetchrow("SELECT status, completed_at FROM tasks WHERE id = $1", task_id)
    finally:
        await conn.close()

    assert row["status"] == "completed"
    assert row["completed_at"] is not None


async def test_ws_records_probe_metadata_from_online_message(ws_client: Any) -> None:
    """The ``online`` message updates alias and last_seen_at on the probe."""
    region_id = await _seed_region(code="XSW-003", title="Metadata")
    token = "probe-token-meta-test"
    probe_id = await _seed_probe(token, region_id)
    await _insert_task(VALID_LINK)

    with ws_client.websocket_connect(
        "/ws", headers={"Authorization": f"Bearer {token}"}
    ) as websocket:
        websocket.send_json(
            {
                "type": "online",
                "alias": "real-alias",
                "region_code": "XSW-003",
                "version": "0.1.0",
                "netif": "eth0",
                "protocol_version": 1,
            }
        )
        # Drain the task.assigned envelope the server emits after
        # every online message; without it the WebSocket frame buffer
        # fills up and the server-side send() blocks the event loop.
        websocket.receive_json()

    conn = await asyncpg.connect(_test_dsn())
    try:
        row = await conn.fetchrow("SELECT alias, last_seen_at FROM probes WHERE id = $1", probe_id)
    finally:
        await conn.close()
    assert row["alias"] == "real-alias"
    assert row["last_seen_at"] is not None


async def test_ws_ignores_spoofed_region_code_from_probe(ws_client: Any) -> None:
    """A probe must not be able to relocate itself just by sending a
    ``region_code`` (DAT-459, item C).

    Setup: a region ``real`` is assigned to the probe at registration.
    The probe then connects and claims ``region_code = spoofed``. The
    server logs a warning and keeps the original ``region_id`` —
    subsequent task assignments still surface ``region_id = real``.
    """
    real_id = await _seed_region(code="REAL", title="Real region")
    await _seed_region(code="SPOOF", title="Spoofed region")
    token = "probe-token-spoof-test"

    conn = await asyncpg.connect(_test_dsn())
    try:
        await conn.execute(
            """
            INSERT INTO probes (alias, token, region_id, is_public)
            VALUES ($1, $2, $3, TRUE)
            """,
            "probe-spoof",
            token,
            real_id,
        )
        task_id = await conn.fetchval(
            "INSERT INTO tasks (link, status) VALUES ($1, 'pending') RETURNING id",
            VALID_LINK,
        )
    finally:
        await conn.close()

    with ws_client.websocket_connect(
        "/ws", headers={"Authorization": f"Bearer {token}"}
    ) as websocket:
        websocket.send_json(
            {
                "type": "online",
                "alias": "probe-spoof",
                "region_code": "SPOOF",
                "version": "0.1.0",
                "netif": "eth0",
                "protocol_version": 1,
            }
        )
        envelope = websocket.receive_json()
        assigned = envelope

    conn = await asyncpg.connect(_test_dsn())
    try:
        row = await conn.fetchrow("SELECT region_id, alias FROM probes WHERE token = $1", token)
    finally:
        await conn.close()

    assert row["region_id"] == real_id
    assert assigned["task_id"] == task_id


__all__ = [
    "test_ws_closes_with_4001_when_authorization_header_missing",
    "test_ws_closes_with_4001_when_token_unknown",
    "test_ws_accepts_probe_with_valid_token_and_assigns_task",
    "test_ws_closes_with_4002_when_protocol_version_below_minimum",
    "test_ws_marks_task_completed_after_probe_reports_result",
    "test_ws_records_probe_metadata_from_online_message",
]
