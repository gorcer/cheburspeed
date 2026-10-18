"""WebSocket message handlers and the ``/ws`` endpoint.

The endpoint lives at ``GET /ws`` (FastAPI WebSocket) and is the only
place that talks the probe-to-server JSON protocol defined in
:mod:`xspeed_server.app.ws.protocol`.

Authentication uses the ``Authorization: Bearer <token>`` header
deliberately (rather than a query parameter) so that the token never
leaks into nginx access logs. An invalid or missing token causes the
connection to be closed with status ``4001`` *before* it is accepted.

A connected probe announces itself with an ``online`` message
containing its alias, region code, software version and the network
interface it intends to use. The server then tries to claim a task
for the probe (see :func:`dispatch_to_probe`) and pushes it as a
``task.assigned`` envelope.

After running the speedtest the probe sends a ``result`` message.
The server persists it through :func:`record_result`, runs
:func:`check_completion` and immediately attempts to hand the probe
its next task — so a probe with a backlog of work never idles.
"""

from __future__ import annotations

import logging
from typing import Any

import asyncpg
from fastapi import WebSocket, WebSocketDisconnect

from xspeed_server.app.db import Database
from xspeed_server.app.ws.dispatcher import check_completion, dispatch_to_probe, record_result
from xspeed_server.app.ws.hub import Hub
from xspeed_server.app.ws.protocol import (
    MIN_PROTOCOL_VERSION,
    WS_CLOSE_INVALID_TOKEN,
    WS_CLOSE_UPGRADE_REQUIRED,
    OnlineMessage,
    ResultMessage,
)

__all__ = [
    "handle_online_message",
    "handle_result_message",
    "lookup_probe",
    "register_websocket_route",
]

logger = logging.getLogger(__name__)


class _AuthRejectedError(Exception):
    """Raised when a probe fails authentication and the socket is closed."""


async def lookup_probe(conn: asyncpg.Connection, token: str) -> dict[str, Any] | None:
    """Return the probe row for ``token`` or ``None`` when unknown."""
    row = await conn.fetchrow(
        """
        SELECT id, alias, token, region_id, ip, is_public
        FROM probes
        WHERE token = $1
        """,
        token,
    )
    return dict(row) if row is not None else None


def _extract_token(websocket: WebSocket) -> str:
    """Pull the bearer token from the ``Authorization`` header.

    Returning an empty string makes the caller close the socket with
    ``4001`` (invalid token) without inspecting the source of the
    failure — matching the behaviour described in the task spec.
    """
    header = websocket.headers.get("authorization", "")
    if not header.lower().startswith("bearer "):
        return ""
    token = header[7:].strip()
    return token


async def _authenticate(token: str, db: Database) -> dict[str, Any]:
    """Raise :class:`_AuthRejectedError` when ``token`` does not match a probe."""
    if not token:
        raise _AuthRejectedError
    async with db.pool.acquire() as conn:
        probe = await lookup_probe(conn, token)
    if probe is None:
        raise _AuthRejectedError
    return dict(probe)


async def handle_online_message(
    conn: asyncpg.Connection,
    msg: OnlineMessage,
    probe: dict[str, Any],
    *,
    client_ip: str | None,
) -> None:
    """Persist probe metadata from an ``online`` message.

    The probe's ``alias`` and last-seen timestamp are still authoritative
    — they only describe the operator-visible identity of the box. The
    ``region_code`` field is now treated as **advisory only**: the server
    compares the announced code to the registered region's code and
    logs a warning when they disagree, but the probe's region can only
    be reassigned by an admin through the REST API. This closes the
    spoof vector where a probe could claim to live in any region just
    to bypass ``probe_tokens`` whitelists.
    """
    announced_region_code = (msg.region_code or "").strip()
    if announced_region_code:
        registered = await conn.fetchrow(
            """
            SELECT r.code AS registered_code
            FROM probes p JOIN regions r ON r.id = p.region_id
            WHERE p.id = $1
            """,
            probe["id"],
        )
        if registered is not None:
            registered_code = str(registered["registered_code"])
            if announced_region_code != registered_code:
                logger.warning(
                    "probe %s claimed region_code=%r but is registered to %r; "
                    "ignoring and keeping current region_id=%s. "
                    "Update region through /api/v1/admin/probes if relocation is intentional.",
                    probe["id"],
                    announced_region_code,
                    registered_code,
                    probe["region_id"],
                )
    await conn.execute(
        """
        UPDATE probes
        SET alias = $1,
            ip = COALESCE($2, ip),
            last_seen_at = now()
        WHERE id = $3
        """,
        msg.alias,
        client_ip,
        probe["id"],
    )
    logger.info(
        "probe %s announced (alias=%r, region_code=%r, version=%r, netif=%r)",
        probe["id"],
        msg.alias,
        msg.region_code,
        msg.version,
        msg.netif,
    )


async def handle_result_message(
    conn: asyncpg.Connection,
    msg: ResultMessage,
    probe: dict[str, Any],
) -> bool | None:
    """Persist a ``result`` message and trigger completion check.

    Returns ``True`` when the parent task was completed, ``False`` for
    a persisted partial result, and ``None`` when this probe was not
    assigned the task.
    """
    assigned = await conn.fetchval(
        "SELECT 1 FROM tasks WHERE id = $1 AND status = 'running' AND assigned_to = $2",
        msg.task_id,
        probe["token"],
    )
    if assigned is None:
        logger.warning("probe %s reported an unassigned task %s", probe["id"], msg.task_id)
        return None
    await record_result(
        conn,
        task_id=msg.task_id,
        probe_id=int(probe["id"]),
        region_id=int(probe["region_id"]),
        speed_mbps=msg.speed_mbps,
        error=msg.error,
        raw={"speed_mbps": msg.speed_mbps, "error": msg.error},
    )
    await conn.execute(
        "UPDATE probes SET last_seen_at = now() WHERE id = $1",
        probe["id"],
    )
    return await check_completion(conn, msg.task_id)


def register_websocket_route(app: Any) -> None:
    """Attach the ``/ws`` route to ``app``.

    The split exists so that this module does not need to import
    FastAPI's dependency-injection primitives at module load; the
    handler resolves the database through ``websocket.app.state.db``
    directly so the WebSocket route does not need a custom
    ``Depends`` shim.
    """

    async def endpoint(websocket: WebSocket) -> None:  # noqa: D401 - FastAPI handler
        db = websocket.app.state.db
        await _run_websocket_session(websocket, db)

    app.add_api_websocket_route("/ws", endpoint)

    async def public_endpoint(websocket: WebSocket) -> None:
        """Accept unauthenticated browser clients and forward server events.

        Defensive caps:
        * per-IP connection count (handled by :meth:`Hub.connect_public`);
        * per-IP inbound payload rate (handled by
          :meth:`Hub.allow_public_payload`) so an idle visitor cannot pin
          the event loop by spamming messages;
        * 64 KB frame cap matching the probe client default.
        """
        client_ip = websocket.client.host if websocket.client else None
        hub: Hub = websocket.app.state.hub
        accepted = await hub.connect_public(websocket, client_ip=client_ip)
        if not accepted:
            await websocket.close(code=1013)  # "try again later"
            return
        await websocket.accept()
        try:
            while True:
                try:
                    if not hub.allow_public_payload(client_ip):
                        await websocket.close(code=1008)  # policy violation
                        return
                    message = await websocket.receive()
                except WebSocketDisconnect:
                    break
                if message["type"] == "websocket.disconnect":
                    break
        except WebSocketDisconnect:
            pass
        finally:
            await hub.disconnect_public(websocket, client_ip=client_ip)

    app.add_api_websocket_route("/ws/public", public_endpoint)


async def _run_websocket_session(websocket: WebSocket, db: Database) -> None:
    """Drive a connected probe through its lifecycle."""
    token = _extract_token(websocket)
    try:
        probe = await _authenticate(token, db)
    except _AuthRejectedError:
        await websocket.close(code=WS_CLOSE_INVALID_TOKEN)
        return

    await websocket.accept()
    hub: Hub = websocket.app.state.hub
    await hub.connect(token, websocket)
    client_host = websocket.client.host if websocket.client else None
    logger.info(
        "ws session accepted: probe=%s region=%s",
        probe["id"],
        probe["region_id"],
    )

    try:
        while True:
            try:
                payload = await websocket.receive_json()
            except WebSocketDisconnect:
                break

            protocol_version = payload.get("protocol_version", 1)
            if protocol_version < MIN_PROTOCOL_VERSION:
                await websocket.close(
                    code=WS_CLOSE_UPGRADE_REQUIRED,
                    reason="upgrade required",
                )
                return

            msg_type = payload.get("type")
            if msg_type == "online":
                async with db.pool.acquire() as conn:
                    try:
                        online = OnlineMessage.model_validate(payload)
                    except Exception:  # noqa: BLE001 - protocol errors are non-fatal
                        logger.warning("invalid online payload: %r", payload)
                        continue
                    await handle_online_message(
                        conn,
                        online,
                        probe,
                        client_ip=client_host,
                    )
                await dispatch_to_probe(db, hub, token)
            elif msg_type == "result":
                async with db.pool.acquire() as conn:
                    try:
                        result = ResultMessage.model_validate(payload)
                    except Exception:  # noqa: BLE001 - protocol errors are non-fatal
                        logger.warning("invalid result payload: %r", payload)
                        continue
                    try:
                        completed = await handle_result_message(conn, result, probe)
                    except asyncpg.ForeignKeyViolationError:
                        logger.warning(
                            "probe %s reported result for unknown task %s",
                            probe["id"],
                            payload.get("task_id"),
                        )
                        continue
                if completed is not None:
                    await hub.broadcast(
                        {
                            "type": "result.added",
                            "payload": {
                                "task_id": result.task_id,
                                "probe_id": probe["id"],
                                "region_id": probe["region_id"],
                                "speed_mbps": result.speed_mbps,
                                "error": result.error,
                            },
                        }
                    )
                    if completed:
                        await hub.broadcast(
                            {
                                "type": "task.updated",
                                "payload": {"id": result.task_id, "status": "completed"},
                            }
                        )
                await dispatch_to_probe(db, hub, token)
            else:
                logger.debug("ignoring ws message with unknown type=%r", msg_type)
    finally:
        await hub.disconnect(token, websocket)
        logger.info("ws session closed: probe=%s", probe["id"])
