"""Speedtest implementation that streams a large file through the SOCKS proxy.

We deliberately use :func:`httpx.AsyncClient.stream` rather than
``await client.get(...)`` because the body is large (100 MB+ by default)
and we want to start the clock the moment the first byte arrives while
still draining the entire response — the latter is required to keep
xray alive long enough that the kernel's TCP throughput statistics
stabilise, and it doubles as a sanity check that the connection really
went through.
"""

from __future__ import annotations

import logging
import time
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


async def measure(
    url: str,
    *,
    proxy_url: str,
    timeout_seconds: float = 30.0,
    chunk_size: int = 64 * 1024,
) -> SpeedtestResult:
    """Download ``url`` through ``proxy_url`` and return the throughput.

    The clock starts when the response headers arrive (i.e. when xray
    has finished the TLS handshake) and stops when the body has been
    drained or the timeout fires. A failed connection raises
    :class:`SpeedtestError` so the caller can convert it to the
    ``error`` field of a :class:`ResultMessage`.
    """
    transport = httpx.AsyncHTTPTransport(proxy=httpx.Proxy(proxy_url))
    async with httpx.AsyncClient(
        transport=transport,
        timeout=timeout_seconds,
        follow_redirects=True,
    ) as client:
        try:
            started = time.monotonic()
            received_first_byte = False
            bytes_downloaded = 0
            async with client.stream("GET", url) as response:
                response.raise_for_status()
                async for chunk in response.aiter_bytes(chunk_size):
                    if chunk:
                        if not received_first_byte:
                            started = time.monotonic()
                            received_first_byte = True
                        bytes_downloaded += len(chunk)
        except httpx.HTTPError as exc:
            details = (
                f"{type(exc).__name__}: {exc}"
                if exc.args
                else f"{type(exc).__name__}: <no message>"
            )
            logger.warning("speedtest failed: %s", details)
            raise SpeedtestError(f"http error: {details}") from exc
        except TimeoutError as exc:
            raise SpeedtestError(f"speedtest timed out after {timeout_seconds}s") from exc
    if not received_first_byte:
        raise SpeedtestError("no bytes received from speedtest target")
    elapsed = time.monotonic() - started
    return SpeedtestResult.from_throughput(
        bytes_downloaded=bytes_downloaded,
        elapsed_seconds=elapsed,
    )
