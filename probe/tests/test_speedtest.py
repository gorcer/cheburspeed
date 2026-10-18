from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest

from xspeed_probe.speedtest import SpeedtestError, SpeedtestResult, measure


def test_speedtest_result_computes_mbps() -> None:
    # 100 Mbit == 12.5 MB == 12_500_000 bytes in 1 second.
    result = SpeedtestResult.from_throughput(
        bytes_downloaded=12_500_000,
        elapsed_seconds=1.0,
    )
    assert result.speed_mbps == pytest.approx(100.0, rel=1e-3)


def test_speedtest_result_rejects_zero_elapsed() -> None:
    with pytest.raises(ValueError, match="elapsed_seconds"):
        SpeedtestResult.from_throughput(
            bytes_downloaded=1024,
            elapsed_seconds=0.0,
        )


def test_speedtest_result_handles_fractional_seconds() -> None:
    result = SpeedtestResult.from_throughput(
        bytes_downloaded=1_250_000,
        elapsed_seconds=0.1,
    )
    assert result.speed_mbps == pytest.approx(100.0, rel=1e-3)


def _patch_transport(
    monkeypatch: pytest.MonkeyPatch,
    handler: Any,
) -> None:
    """Force :mod:`xspeed_probe.speedtest` to use the supplied handler.

    :mod:`xspeed_probe.speedtest` builds its transport via
    ``httpx.AsyncHTTPTransport(proxy=...)``. We can't replace it with
    the ``MockTransport`` instance directly because that class doesn't
    accept a ``proxy`` keyword; instead we wrap the handler in a thin
    callable that ignores its kwargs.
    """

    class _ProxyIgnoringMock(httpx.MockTransport):
        def __init__(self, **_kwargs: Any) -> None:
            super().__init__(handler)

    import xspeed_probe.speedtest as speedtest_mod

    monkeypatch.setattr(speedtest_mod.httpx, "AsyncHTTPTransport", _ProxyIgnoringMock)


class _AsyncChunkStream(httpx.AsyncByteStream):
    """An :class:`httpx.AsyncByteStream` that yields ``chunks`` lazily.

    Used by the budget-exhaustion test to force the probe's wall-clock
    deadline to fire while the stream is still mid-flight.
    """

    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = chunks

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for chunk in self._chunks:
            yield chunk

    async def aclose(self) -> None:
        return None


async def test_measure_returns_throughput_when_download_finishes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A response that streams 125 kB in well under the budget."""
    body = b"x" * 125_000

    async def handler(request: httpx.Request) -> httpx.Response:
        await request.aread()
        return httpx.Response(
            200,
            headers={"content-length": str(len(body))},
            request=request,
            stream=_AsyncChunkStream([body]),
        )

    _patch_transport(monkeypatch, handler)
    result = await measure(
        "https://example.test/speed",
        proxy_url="socks5://127.0.0.1:1080",
        timeout_seconds=5.0,
        chunk_size=64 * 1024,
    )
    assert result.bytes_downloaded == 125_000
    assert result.elapsed_seconds > 0
    assert result.speed_mbps > 0
    # Sanity-check: Mbps = bytes * 8 / (elapsed * 1_000_000)
    expected_mbps = (125_000 * 8) / (result.elapsed_seconds * 1_000_000)
    assert result.speed_mbps == pytest.approx(expected_mbps, rel=1e-3)


async def test_measure_returns_partial_result_when_budget_exhausted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DAT-465: a stream that stalls mid-flight must end with the bytes
    received so far rather than raising a timeout error.

    The transport emits a 4 MB chunk, then blocks on the next read
    for far longer than the budget. The probe must stop streaming
    after the deadline and surface the partial throughput.
    """
    delivered = b"x" * 4 * 1024 * 1024

    async def handler(request: httpx.Request) -> httpx.Response:
        await request.aread()

        async def _aiter() -> AsyncIterator[bytes]:
            yield delivered
            # Sleep far longer than the probe's budget so the next
            # ``aiter_bytes`` call only resolves after the deadline
            # fires.
            await asyncio.sleep(10.0)
            yield b"never-arrives"

        class _StallingStream(httpx.AsyncByteStream):
            def __init__(self, gen: Any) -> None:
                self._gen = gen

            async def __aiter__(self) -> AsyncIterator[bytes]:
                async for chunk in self._gen():
                    yield chunk

            async def aclose(self) -> None:
                return None

        return httpx.Response(
            200,
            headers={"content-length": "100000000"},
            request=request,
            stream=_StallingStream(_aiter),
        )

    _patch_transport(monkeypatch, handler)
    started = time.monotonic()
    result = await measure(
        "https://example.test/speed",
        proxy_url="socks5://127.0.0.1:1080",
        timeout_seconds=0.1,
        chunk_size=64 * 1024,
    )
    elapsed_real = time.monotonic() - started
    # The probe should bail out well before the 10 s transport sleep.
    assert elapsed_real < 2.0
    # The budget must fire before the full 4 MB body drains — we
    # assert against an upper bound rather than a fixed number
    # because httpx's chunking depends on socket buffers.
    assert 0 < result.bytes_downloaded <= len(delivered)
    # Throughput clock starts at the first byte and stops at the deadline.
    assert result.elapsed_seconds == pytest.approx(0.1, abs=0.2)
    assert result.speed_mbps > 0


async def test_measure_raises_when_no_bytes_received(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 204 with an empty body must surface as ``SpeedtestError``."""

    async def handler(request: httpx.Request) -> httpx.Response:
        await request.aread()
        return httpx.Response(204, request=request)

    _patch_transport(monkeypatch, handler)
    with pytest.raises(SpeedtestError, match="no bytes received"):
        await measure(
            "https://example.test/speed",
            proxy_url="socks5://127.0.0.1:1080",
            timeout_seconds=1.0,
        )


async def test_measure_raises_on_http_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 5xx from the speedtest target must surface as ``SpeedtestError``."""

    async def handler(request: httpx.Request) -> httpx.Response:
        await request.aread()
        return httpx.Response(503, request=request)

    _patch_transport(monkeypatch, handler)
    with pytest.raises(SpeedtestError, match="http error"):
        await measure(
            "https://example.test/speed",
            proxy_url="socks5://127.0.0.1:1080",
            timeout_seconds=1.0,
        )
