"""Registries for authenticated probe and public frontend sockets."""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict, deque
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

    def __init__(
        self,
        *,
        max_public_connections_per_ip: int = 4,
        max_total_public_connections: int = 512,
    ) -> None:
        self._clients: dict[str, WebSocket] = {}
        self._public_clients: set[WebSocket] = set()
        self._lock = asyncio.Lock()
        self._max_public_connections_per_ip = max_public_connections_per_ip
        self._max_total_public_connections = max_total_public_connections
        self._public_clients_by_ip: dict[str, int] = defaultdict(int)
        self._window_seconds: float = 60.0
        self._max_payload_per_minute: int = 60
        self._public_payload_stamps: dict[str, deque[float]] = defaultdict(deque)

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

    async def connect_public(
        self,
        ws: WebSocket,
        *,
        client_ip: str | None,
    ) -> bool:
        """Register ``ws`` as a public listener.

        Returns ``True`` when the connection was accepted, ``False`` when
        the per-IP or global cap was hit so the caller can close the
        socket without polluting the registry.
        """
        ip = client_ip or "unknown"
        async with self._lock:
            total = len(self._public_clients)
            if total >= self._max_total_public_connections:
                logger.warning(
                    "public ws cap reached (%d/%d); rejecting new connection from %s",
                    total,
                    self._max_total_public_connections,
                    ip,
                )
                return False
            per_ip = self._public_clients_by_ip.get(ip, 0)
            if per_ip >= self._max_public_connections_per_ip:
                logger.warning(
                    "public ws per-IP cap reached for %s (%d/%d); rejecting",
                    ip,
                    per_ip,
                    self._max_public_connections_per_ip,
                )
                return False
            self._public_clients.add(ws)
            self._public_clients_by_ip[ip] = per_ip + 1
        return True

    async def disconnect_public(self, ws: WebSocket, *, client_ip: str | None = None) -> None:
        async with self._lock:
            removed = ws in self._public_clients
            self._public_clients.discard(ws)
            if removed:
                ip = client_ip or "unknown"
                current = self._public_clients_by_ip.get(ip, 0)
                if current <= 1:
                    self._public_clients_by_ip.pop(ip, None)
                    self._public_payload_stamps.pop(ip, None)
                else:
                    self._public_clients_by_ip[ip] = current - 1

    def allow_public_payload(self, client_ip: str | None) -> bool:
        """Return ``True`` when the client may push another frame.

        Used to rate-limit inbound client→server traffic on
        ``/ws/public``. The probe ``/ws`` channel is server-driven and
        ignores this helper. The bucket is keyed by ``client_ip`` and
        lets through at most ``max_payload_per_minute`` frames per
        ``window_seconds`` per IP.
        """
        ip = client_ip or "unknown"
        import time

        now = time.monotonic()
        stamps = self._public_payload_stamps[ip]
        cutoff = now - self._window_seconds
        while stamps and stamps[0] < cutoff:
            stamps.popleft()
        if len(stamps) >= self._max_payload_per_minute:
            return False
        stamps.append(now)
        return True

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
