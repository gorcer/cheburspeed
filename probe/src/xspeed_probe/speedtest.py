"""Speedtest implementation that streams a large file through the SOCKS proxy.

We deliberately use :func:`httpx.AsyncClient.stream` rather than
``await client.get(...)`` because the body is large (100 MB+ by default)
and we want to start the clock the moment the first byte arrives while
still draining the entire response — the latter is required to keep
xray alive long enough that the kernel's TCP throughput statistics
stabilise, and it doubles as a sanity check that the connection really
went through.

The download is bounded by ``timeout_seconds`` of wall-clock time
*after* the first byte arrives (DAT-465). When the budget is
exhausted we return the partial throughput instead of raising — the
goal is to surface an actual Mbps reading rather than a timeout
error.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)


class SpeedtestError(Exception):
    """Raised when the speedtest could not produce a measurement."""


@dataclass(slots=True)
class SpeedtestResult:
    """The outcome of a single speedtest attempt."""

    bytes_downloaded: int
    elapsed_seconds: float
    speed_mbps: float

    @classmethod
    def from_throughput(
        cls,
        *,
        bytes_downloaded: int,
        elapsed_seconds: float,
    ) -> SpeedtestResult:
        if elapsed_seconds <= 0:
            raise ValueError("elapsed_seconds must be > 0")
        mbps = (bytes_downloaded * 8) / (elapsed_seconds * 1_000_000)
        return cls(
            bytes_downloaded=bytes_downloaded,
            elapsed_seconds=elapsed_seconds,
            speed_mbps=mbps,
        )


def _http_timeout(timeout_seconds: float) -> httpx.Timeout:
    """Build an :class:`httpx.Timeout` aligned with the download budget.

    The ``read`` budget is padded slightly above ``timeout_seconds`` so
    that httpx does not race our manual deadline while the response is
    still healthy. ``connect``/``write``/``pool`` are capped at 10s
    each so a broken destination cannot wedge the probe for the full
    download window.
    """
    pad = max(5.0, timeout_seconds * 0.25)
    side_budget = min(timeout_seconds, 10.0)
    return httpx.Timeout(
        connect=side_budget,
        read=timeout_seconds + pad,
        write=side_budget,
        pool=side_budget,
    )


async def _next_chunk(
    stream: AsyncIterator[bytes],
    *,
    budget_seconds: float | None,
) -> bytes | None:
    """Return the next chunk from ``stream`` or ``None`` on EOF / budget expiry.

    ``budget_seconds`` is the remaining wall-clock time the caller is
    willing to wait for the next chunk. ``None`` means wait forever
    (used before the first byte arrives — httpx's connect-side
    timeout already guards that path). A value of ``0`` or less
    short-circuits to ``None``.
    """
    if budget_seconds is not None and budget_seconds <= 0:
        return None
    try:
        if budget_seconds is None:
            return await stream.__anext__()
        async with asyncio.timeout(budget_seconds):
            return await stream.__anext__()
    except (TimeoutError, StopAsyncIteration):
        return None


async def measure(
    url: str,
    *,
    proxy_url: str,
    timeout_seconds: float = 20.0,
    chunk_size: int = 64 * 1024,
) -> SpeedtestResult:
    """Download ``url`` through ``proxy_url`` and return the throughput.

    The throughput clock starts when the first body byte arrives (i.e.
    once xray has finished the TLS handshake) and stops when either
    the body has been drained or ``timeout_seconds`` of wall-clock
    time has elapsed since that first byte.

    When the download budget is exhausted mid-stream the function
    returns a :class:`SpeedtestResult` computed from whatever bytes
    arrived up to that point (DAT-465). The probe would rather report
    a real throughput number than a timeout error — operators
    otherwise see the task as permanently failed even when the tunnel
    is fine and only slow.

    A failed connection still raises :class:`SpeedtestError` so the
    caller can convert it to the ``error`` field of a
    :class:`ResultMessage`.
    """
    transport = httpx.AsyncHTTPTransport(proxy=httpx.Proxy(proxy_url))
    async with httpx.AsyncClient(
        transport=transport,
        timeout=_http_timeout(timeout_seconds),
        follow_redirects=True,
    ) as client:
        started = 0.0
        deadline = 0.0
        received_first_byte = False
        bytes_downloaded = 0
        try:
            async with client.stream("GET", url) as response:
                response.raise_for_status()
                stream = response.aiter_bytes(chunk_size)
                # Before the first byte arrives there is no deadline:
                # httpx's connect-side timeout guards against a broken
                # destination, and we don't want to punish a slow but
                # legitimate upstream handshake.
                while True:
                    budget = max(deadline - time.monotonic(), 0.0) if received_first_byte else None
                    chunk = await _next_chunk(stream, budget_seconds=budget)
                    if chunk:
                        if not received_first_byte:
                            started = time.monotonic()
                            deadline = started + timeout_seconds
                            received_first_byte = True
                        bytes_downloaded += len(chunk)
                        continue
                    # ``chunk is None`` means the iterator ended (EOF)
                    # or the per-read budget fired.
                    if not received_first_byte:
                        # Upstream hung before sending anything.
                        raise SpeedtestError("no bytes received from speedtest target")
                    logger.info(
                        "speedtest download budget of %.1fs exhausted "
                        "after %d bytes — returning partial result",
                        timeout_seconds,
                        bytes_downloaded,
                    )
                    break
        except httpx.HTTPError as exc:
            details = (
                f"{type(exc).__name__}: {exc}"
                if exc.args
                else f"{type(exc).__name__}: <no message>"
            )
            logger.warning("speedtest failed: %s", details)
            raise SpeedtestError(f"http error: {details}") from exc
    if not received_first_byte:
        raise SpeedtestError("no bytes received from speedtest target")
    elapsed = time.monotonic() - started
    return SpeedtestResult.from_throughput(
        bytes_downloaded=bytes_downloaded,
        elapsed_seconds=elapsed,
    )
