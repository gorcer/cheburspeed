"""WebSocket Hub and dispatcher (filled in by DAT-325.4).

The :class:`Hub` below is a no-op placeholder so the lifespan startup
can wire it into ``app.state.hub`` without depending on the upcoming
WebSocket task. The real implementation will replace this stub.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import WebSocket

logger = logging.getLogger(__name__)


class Hub:
    """Registry of connected probe WebSocket clients.

    The current implementation is a minimal in-memory map keyed by probe
    token. The :meth:`connect`/:meth:`disconnect`/:meth:`send` methods
    have the right surface for the upcoming dispatcher; they will be
    expanded by the WebSocket task (DAT-325.4) with full protocol
    handling and broadcast helpers.
    """

    def __init__(self) -> None:
        self._clients: dict[str, WebSocket] = {}
        self._lock = asyncio.Lock()

    async def connect(self, token: str, ws: WebSocket) -> None:
        async with self._lock:
            self._clients[token] = ws
        logger.info("probe connected: %s", token)

    async def disconnect(self, token: str) -> None:
        async with self._lock:
            self._clients.pop(token, None)
        logger.info("probe disconnected: %s", token)

    async def send(self, token: str, message: dict[str, Any]) -> bool:
        ws = self._clients.get(token)
        if ws is None:
            return False
        await ws.send_json(message)
        return True

    def online_probes(self) -> set[str]:
        return set(self._clients)


__all__ = ["Hub"]
