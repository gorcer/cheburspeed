"""WebSocket client that talks to the XSpeed server's ``/ws`` endpoint.

The probe keeps a single long-lived WebSocket connection alive and
piggy-backs all protocol traffic on it: an ``online`` message on
connect, a ``task.assigned`` envelope from the server per measurement,
and a ``result`` message back after each speedtest.

Reconnection uses an exponential backoff (capped) so a transient server
restart does not pin the probe in a tight loop; the loop respects a
caller-supplied :class:`asyncio.Event` so SIGTERM can break out of it
cleanly.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

import websockets
from websockets.exceptions import ConnectionClosed, InvalidStatus

from xspeed_probe.config import ProbeSettings, get_settings

if TYPE_CHECKING:
    from websockets import ClientConnection

logger = logging.getLogger(__name__)


TaskHandler = Callable[[int, str], Awaitable[tuple[float | None, str | None]]]
"""Callable invoked on every ``task.assigned`` envelope.

It receives ``(task_id, link)`` and returns ``(speed_mbps, error)``.
Returning ``(None, None)`` is treated as success and the client will not
send anything — useful for handlers that post results out-of-band.
"""


class ProbeClient:
    """Async WebSocket client with reconnect + backoff."""

    def __init__(
        self,
        settings: ProbeSettings,
        *,
        on_task: TaskHandler,
        version: str = "0.1.0",
    ) -> None:
        self._settings = settings
        self._on_task = on_task
        self._version = version
        self._stop_event = asyncio.Event()
        self._ws: ClientConnection | None = None

    def stop(self) -> None:
        """Signal the run loop to exit at the next iteration boundary."""
        self._stop_event.set()

    async def close(self) -> None:
        """Close the active WebSocket (if any) and mark the client stopped."""
        self.stop()
        ws = self._ws
        if ws is not None:
            try:
                await ws.close()
            except Exception:  # noqa: BLE001 - best effort
                logger.debug("ws close failed", exc_info=True)
            self._ws = None

    async def run_forever(self) -> None:
        """Connect, run until stop_event, then reconnect with backoff."""
        backoff = self._settings.reconnect_initial_delay
        while not self._stop_event.is_set():
            try:
                await self._run_session()
            except Exception as exc:  # noqa: BLE001
                if self._stop_event.is_set():
                    return
                logger.warning(
                    "ws session ended (%s: %s); retrying in %.1fs",
                    type(exc).__name__,
                    exc,
                    backoff,
                )
                try:
                    await asyncio.wait_for(
                        self._stop_event.wait(),
                        timeout=backoff,
                    )
                except TimeoutError:
                    pass
                backoff = min(backoff * 2.0, self._settings.reconnect_max_delay)
                continue
            backoff = self._settings.reconnect_initial_delay

    async def _run_session(self) -> None:
        """Open one WebSocket session, drive it until disconnect or stop."""
        additional_headers = [("Authorization", f"Bearer {self._settings.probe_token}")]
        logger.info("connecting to %s", self._settings.server_url)
        async with websockets.connect(
            self._settings.server_url,
            additional_headers=additional_headers,
            ping_interval=30,
            ping_timeout=20,
            max_size=2**20,
        ) as ws:
            self._ws = ws
            await self._send_online(ws)
            while not self._stop_event.is_set():
                try:
                    raw = await ws.recv()
                except ConnectionClosed:
                    logger.info("ws closed by server")
                    return
                payload = _decode(raw)
                if payload is None:
                    continue
                msg_type = payload.get("type")
                if msg_type == "task.assigned":
                    await self._handle_task(ws, payload)
                else:
                    logger.debug("ignoring ws message with type=%r", msg_type)
            self._ws = None

    async def _send_online(self, ws: ClientConnection) -> None:
        """Send the ``online`` handshake required by the server."""
        payload = {
            "type": "online",
            "alias": self._settings.probe_alias,
            "region_code": self._settings.region_code or "",
            "version": self._version,
            "netif": self._settings.network_interface or "",
            "protocol_version": 1,
        }
        await ws.send(json.dumps(payload))

    async def _handle_task(
        self,
        ws: ClientConnection,
        payload: dict[str, Any],
    ) -> None:
        """Run the speedtest for a single task and reply with the result."""
        try:
            task_id = int(payload["task_id"])
            link = str(payload["link"])
        except (KeyError, TypeError, ValueError):
            logger.warning("task.assigned missing task_id/link: %r", payload)
            return
        logger.info("received task %s", task_id)
        speed_mbps: float | None = None
        error: str | None = None
        try:
            speed_mbps, error = await self._on_task(task_id, link)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.exception("task %s handler failed", task_id)
            error = f"handler_error: {exc}"
        await self._send_result(ws, task_id, speed_mbps, error)

    async def _send_result(
        self,
        ws: ClientConnection,
        task_id: int,
        speed_mbps: float | None,
        error: str | None,
    ) -> None:
        payload = {
            "type": "result",
            "task_id": task_id,
            "speed_mbps": speed_mbps,
            "error": error,
            "protocol_version": 1,
        }
        try:
            await ws.send(json.dumps(payload))
        except ConnectionClosed:
            logger.warning("ws closed before result for task %s was sent", task_id)


def _decode(raw: Any) -> dict[str, Any] | None:
    """Parse a single WS message into a dict, logging on failure."""
    if not isinstance(raw, (str, bytes)):
        logger.debug("non-text ws frame: %r", raw)
        return None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("invalid json on ws: %r", raw[:120])
        return None
    if not isinstance(payload, dict):
        logger.warning("non-object ws payload: %r", payload)
        return None
    return payload


async def run_probe(
    on_task: TaskHandler,
    *,
    settings: ProbeSettings | None = None,
    version: str = "0.1.0",
) -> None:
    """Convenience entrypoint that wires up a client and runs forever."""
    client = ProbeClient(
        settings or get_settings(),
        on_task=on_task,
        version=version,
    )
    try:
        await client.run_forever()
    finally:
        await client.close()


__all__ = ["ProbeClient", "TaskHandler", "run_probe", "InvalidStatus"]
