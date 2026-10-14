"""Registries for authenticated probe and public frontend sockets."""

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
        self._public_clients: set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def connect(self, token: str, ws: WebSocket) -> None:
        async with self._lock:
            self._clients[token] = ws
        logger.info("probe connected: %s", token)

    async def disconnect(self, token: str, ws: WebSocket) -> None:
        async with self._lock:
            if self._clients.get(token) is ws:
                self._clients.pop(token, None)
        logger.info("probe disconnected: %s", token)

    async def send(self, token: str, message: dict[str, Any]) -> bool:
        ws = self._clients.get(token)
        if ws is None:
            return False
        try:
            await ws.send_json(message)
        except Exception:  # noqa: BLE001 - a socket can close after lookup
            await self.disconnect(token, ws)
            return False
        return True

    async def connect_public(self, ws: WebSocket) -> None:
        async with self._lock:
            self._public_clients.add(ws)

    async def disconnect_public(self, ws: WebSocket) -> None:
        async with self._lock:
            self._public_clients.discard(ws)

    async def broadcast(self, message: dict[str, Any]) -> None:
        async with self._lock:
            clients = list(self._public_clients)
        for ws in clients:
            try:
                await ws.send_json(message)
            except Exception:  # noqa: BLE001 - remove stale public sockets
                await self.disconnect_public(ws)

    def online_probes(self) -> set[str]:
        return set(self._clients)


__all__ = ["Hub"]
