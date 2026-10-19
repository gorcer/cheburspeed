"""CheburSpeed probe entry point.

The probe runs an asyncio event loop that wires together three
collaborators:

* :mod:`xspeed_probe.wsclient` — the WebSocket transport with
  reconnect/backoff and the ``online`` / ``result`` envelopes.
* :mod:`xspeed_probe.xray` — parses ``vless://`` URIs into JSON
  configs, spawns ``xray-core`` and waits for its SOCKS5 port to
  become ready.
* :mod:`xspeed_probe.speedtest` — measures download throughput through
  the SOCKS proxy.

A SIGTERM/SIGINT sets a stop event so the in-flight speedtest can be
cancelled, xray is terminated, and the WebSocket is closed before the
loop exits.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import signal
import socket
from typing import Any
from urllib.parse import urlparse

from xspeed_probe.config import ProbeSettings, get_settings
from xspeed_probe.netif import (
    InterfaceNoIpError,
    NetworkInterfaceUnavailableError,
    ensure_interface_available,
    resolve_interface_ip,
)
from xspeed_probe.speedtest import SpeedtestError, measure
from xspeed_probe.wsclient import ProbeClient, run_probe
from xspeed_probe.xray import XrayManager

logger = logging.getLogger(__name__)


def _destination_targets_self_ip(url: str, own_ip: str | None) -> bool:
    """Return ``True`` when ``url`` resolves to ``own_ip``.

    DAT-459: a probe must refuse to run a speedtest whose destination
    resolves to its own public IP — the result would be
    tautological, wastes the operator's upstream tunnel and could mask
    measurement regressions. We compare every resolved address so that
    DNS round-robin tricks still get caught.
    """
    if not url or not own_ip:
        return False
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    host = parsed.hostname
    if not host:
        return False
    try:
        own_addr = ipaddress.ip_address(own_ip)
    except ValueError:
        return False
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        # DNS lookup failed — let the speedtest fail on its own and
        # surface the underlying error rather than speculating.
        return False
    addresses = {info[4][0] for info in infos}
    for raw in addresses:
        try:
            candidate = ipaddress.ip_address(raw)
        except ValueError:
            continue
        if candidate == own_addr:
            return True
    return False


async def _run_task(
    task_id: int,
    link: str,
    *,
    settings: ProbeSettings,
    xray: XrayManager,
    speedtest_url: str | None = None,
    probe_public_ip: str | None = None,
) -> tuple[float | None, str | None]:
    """Drive a single task to completion and return ``(speed_mbps, error)``.

    ``speedtest_url`` overrides :attr:`ProbeSettings.speedtest_url` when
    provided; otherwise the probe falls back to its configured default.
    ``probe_public_ip`` is the IP the server last observed for this
    probe; when set and :attr:`ProbeSettings.refuse_self_ip_speedtests`
    is true, we skip the run if ``speedtest_url`` resolves to it.

    When ``settings.network_interface`` is set, the probe resolves the
    interface's IPv4 right before launching xray and forwards it as the
    ``sendThrough`` field on the vless outbound. The resolution happens
    on every task so a DHCP lease change between runs is picked up
    without restarting the probe.
    """
    target_url = speedtest_url or settings.speedtest_url
    send_through: str | None = None
    if settings.network_interface:
        if not ensure_interface_available(settings.network_interface):
            return None, None
        try:
            send_through = resolve_interface_ip(settings.network_interface)
        except NetworkInterfaceUnavailableError:
            return None, None
        except InterfaceNoIpError as exc:
            logger.warning(
                "task %s: NETWORK_INTERFACE=%r has no routable IPv4 (%s); "
                "skipping task silently",
                task_id,
                settings.network_interface,
                exc,
            )
            return None, None
    if (
        settings.refuse_self_ip_speedtests
        and probe_public_ip
        and _destination_targets_self_ip(target_url, probe_public_ip)
    ):
        logger.warning(
            "task %s: refusing to run speedtest because destination %r "
            "resolves to the probe's own public IP %s",
            task_id,
            target_url,
            probe_public_ip,
        )
        return None, "self_ip_forbidden"
    try:
        xray.start(
            task_id,
            link,
            send_through=send_through,
            network_interface=settings.network_interface,
        )
    except Exception as exc:  # noqa: BLE001 - surface as result error
        logger.exception("failed to start xray for task %s", task_id)
        return None, f"xray_start_failed: {exc}"
    try:
        ready = await xray.wait_ready(task_id)
        if not ready:
            return None, "xray_not_ready"
        try:
            result = await measure(
                target_url,
                proxy_url=xray.socks_url,
                timeout_seconds=settings.speedtest_timeout,
            )
        except SpeedtestError as exc:
            logger.warning("speedtest failed for task %s: %s", task_id, exc)
            return None, f"speedtest_failed: {exc}"
        logger.info(
            "task %s: %.2f Mbps in %.2fs (%d bytes)",
            task_id,
            result.speed_mbps,
            result.elapsed_seconds,
            result.bytes_downloaded,
        )
        return result.speed_mbps, None
    finally:
        xray.terminate(task_id)


def _build_handler(
    settings: ProbeSettings,
    xray: XrayManager,
) -> Any:
    """Return an async callback suitable for :class:`ProbeClient`."""

    async def handler(
        task_id: int,
        link: str,
        destination_url: str | None,
        probe_public_ip: str | None = None,
    ) -> tuple[float | None, str | None]:
        return await _run_task(
            task_id,
            link,
            settings=settings,
            xray=xray,
            speedtest_url=destination_url,
            probe_public_ip=probe_public_ip,
        )

    return handler


def _install_signal_handlers(loop: asyncio.AbstractEventLoop, stop: asyncio.Event) -> None:
    """Wire SIGINT/SIGTERM to ``stop`` so the run loop can exit cleanly."""

    def _set_stop() -> None:
        logger.info("received signal, shutting down")
        stop.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _set_stop)
        except (NotImplementedError, RuntimeError):
            signal.signal(sig, lambda signum, _frame: _set_stop())


async def _amain(stop: asyncio.Event) -> None:
    """Async main: build settings, manager and client, then run forever."""
    settings = get_settings()
    xray = XrayManager(
        socks_host=settings.xray_socks_host,
        socks_port=settings.xray_socks_port,
        config_dir=settings.xray_config_dir,
        ready_timeout=settings.xray_ready_timeout,
    )
    handler = _build_handler(settings, xray)
    client = ProbeClient(settings, on_task=handler, version=settings.version)
    try:
        await _drive(client, stop)
    finally:
        await client.close()
        xray.terminate_all()


async def _drive(client: ProbeClient, stop: asyncio.Event) -> None:
    """Run the client and stop event concurrently until either signals."""
    runner = asyncio.create_task(client.run_forever(), name="probe-ws")
    stopper = asyncio.create_task(stop.wait(), name="probe-stop")
    try:
        done, _pending = await asyncio.wait(
            {runner, stopper},
            return_when=asyncio.FIRST_COMPLETED,
        )
    finally:
        await client.close()
        if not runner.done():
            runner.cancel()
            try:
                await runner
            except asyncio.CancelledError:
                pass
        if not stopper.done():
            stopper.cancel()


def run() -> None:
    """Synchronous entry point used by ``xspeed-probe`` and ``python -m``."""
    settings = get_settings()
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logger.info(
        "xspeed-probe starting (alias=%r, region=%r)",
        settings.probe_alias,
        settings.region_code,
    )
    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        stop = asyncio.Event()
        _install_signal_handlers(loop, stop)
        loop.run_until_complete(_amain(stop))
    finally:
        loop.run_until_complete(loop.shutdown_asyncgens())
        loop.close()
        logger.info("xspeed-probe stopped")


if __name__ == "__main__":
    run()


__all__ = ["run", "run_probe"]
