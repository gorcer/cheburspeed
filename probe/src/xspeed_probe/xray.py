"""xray-core process manager and vless URI parser.

The probe parses a ``vless://`` URI handed out by the server, turns it
into a minimal xray JSON configuration with a SOCKS5 inbound on
``127.0.0.1:1080``, and spawns ``xray run -c <config>`` as a subprocess.

A small ``XrayManager`` keeps track of running processes so we can
terminate them deterministically on graceful shutdown and clean up the
on-disk configs we wrote for each task.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shlex
import socket
import subprocess
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

logger = logging.getLogger(__name__)


class VlessUriError(ValueError):
    """Raised when a vless:// URI cannot be parsed into a config."""


@dataclass(slots=True)
class VlessEndpoint:
    """The minimum fields needed to dial a remote vless endpoint."""

    uuid: str
    host: str
    port: int
    security: str
    sni: str
    flow: str
    type_: str
    path: str
    host_header: str
    raw_query: dict[str, list[str]]

    @classmethod
    def from_uri(cls, uri: str) -> VlessEndpoint:
        """Parse a ``vless://UUID@HOST:PORT?...#NAME`` URI.

        Raises :class:`VlessUriError` when the URI is malformed. We use
        :mod:`urllib.parse` (no third-party deps) and decode
        percent-encoded query values so that realistic operator-supplied
        URLs — with spaces, slashes and query characters in SNI/host
        headers — round-trip correctly.
        """
        if not uri.startswith("vless://"):
            raise VlessUriError(f"not a vless URI: {uri[:32]!r}")
        parsed = urlparse(uri)
        if parsed.scheme != "vless":
            raise VlessUriError(f"unsupported scheme: {parsed.scheme!r}")
        uuid = unquote(parsed.username or "")
        if not uuid:
            raise VlessUriError("missing UUID in vless URI")
        host = parsed.hostname or ""
        if not host:
            raise VlessUriError("missing host in vless URI")
        try:
            port = parsed.port
        except ValueError as exc:
            raise VlessUriError(f"invalid port: {exc}") from exc
        if port is None:
            raise VlessUriError("missing port in vless URI")
        decoded_query = {
            key: [unquote(v) for v in values]
            for key, values in parse_qs(parsed.query, keep_blank_values=True).items()
        }
        return cls(
            uuid=uuid,
            host=host,
            port=port,
            security=(decoded_query.get("security") or ["none"])[0].lower(),
            sni=(decoded_query.get("sni") or [host])[0],
            flow=(decoded_query.get("flow") or [""])[0],
            type_=(decoded_query.get("type") or ["tcp"])[0].lower(),
            path=decoded_query.get("path", [""])[0],
            host_header=(decoded_query.get("host") or [""])[0],
            raw_query=decoded_query,
        )


def build_xray_config(
    endpoint: VlessEndpoint,
    *,
    socks_host: str = "127.0.0.1",
    socks_port: int = 1080,
) -> dict[str, Any]:
    """Return the xray JSON config that dials ``endpoint`` via SOCKS5.

    The shape mirrors what ``xray-core`` expects at
    https://xtls.github.io/config/ — a single vless outbound pointed at
    the remote server and a single SOCKS5 inbound that the probe's
    HTTP client then dials through.
    """
    stream_settings: dict[str, Any] = {"network": endpoint.type_ or "tcp"}
    if endpoint.type_ == "ws":
        ws_settings: dict[str, Any] = {}
        if endpoint.path:
            ws_settings["path"] = endpoint.path
        if endpoint.host_header:
            ws_settings["host"] = endpoint.host_header
        stream_settings["wsSettings"] = ws_settings
    if endpoint.security in ("tls", "reality"):
        tls_settings: dict[str, Any] = {
            "serverName": endpoint.sni,
            "allowInsecure": False,
        }
        if endpoint.security == "reality":
            tls_settings["alpn"] = ["h2", "http/1.1"]
        stream_settings["security"] = endpoint.security
        stream_settings["tlsSettings"] = tls_settings
    return {
        "log": {"loglevel": "warning"},
        "inbounds": [
            {
                "tag": "probe-socks",
                "listen": socks_host,
                "port": socks_port,
                "protocol": "socks",
                "settings": {
                    "auth": "noauth",
                    "udp": False,
                },
            },
        ],
        "outbounds": [
            {
                "tag": "vless-out",
                "protocol": "vless",
                "settings": {
                    "vnext": [
                        {
                            "address": endpoint.host,
                            "port": endpoint.port,
                            "users": [
                                {
                                    "id": endpoint.uuid,
                                    "encryption": "none",
                                    "flow": endpoint.flow or "",
                                },
                            ],
                        },
                    ],
                },
                "streamSettings": stream_settings,
            },
        ],
    }


def write_config(
    config: dict[str, Any],
    path: Path,
) -> None:
    """Atomically write ``config`` as JSON to ``path``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(config, indent=2), encoding="utf-8")
    os.replace(tmp, path)


class XrayManager:
    """Track running ``xray`` subprocesses and clean them up on shutdown."""

    def __init__(
        self,
        *,
        socks_host: str = "127.0.0.1",
        socks_port: int = 1080,
        config_dir: Path | str = "/tmp",  # noqa: S108 - ephemeral xray configs
        xray_binary: str = "xray",
        ready_timeout: float = 10.0,
        poll_interval: float = 0.1,
    ) -> None:
        self._socks_host = socks_host
        self._socks_port = socks_port
        self._config_dir = Path(config_dir)
        self._xray_binary = xray_binary
        self._ready_timeout = ready_timeout
        self._poll_interval = poll_interval
        self._processes: dict[int, subprocess.Popen[bytes]] = {}
        self._configs: dict[int, Path] = {}

    @property
    def socks_url(self) -> str:
        return f"socks5://{self._socks_host}:{self._socks_port}"

    def start(self, task_id: int, link: str) -> Path:
        """Spawn xray for ``task_id`` using the vless ``link`` from the server.

        Returns the path to the on-disk JSON config so tests can clean
        it up explicitly. Raises :class:`VlessUriError` if the link is
        malformed and :class:`FileNotFoundError` if xray is missing.
        """
        endpoint = VlessEndpoint.from_uri(link)
        config = build_xray_config(
            endpoint,
            socks_host=self._socks_host,
            socks_port=self._socks_port,
        )
        config_path = self._config_dir / f"xray-{task_id}.json"
        write_config(config, config_path)
        cmd = [self._xray_binary, "run", "-c", str(config_path)]
        logger.info(
            "starting xray for task %s: %s",
            task_id,
            _safe_command(cmd),
        )
        proc = subprocess.Popen(  # noqa: S603 - input is parsed vless URI, validated above
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self._processes[task_id] = proc
        self._configs[task_id] = config_path
        return config_path

    async def wait_ready(self, task_id: int) -> bool:
        """Poll the SOCKS port until xray accepts a TCP connection.

        Returns ``True`` when the port is open within ``ready_timeout``;
        ``False`` if xray exits early or the port never opens.
        """
        deadline = time.monotonic() + self._ready_timeout
        proc = self._processes.get(task_id)
        if proc is None:
            return False
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                logger.warning(
                    "xray exited before becoming ready (task=%s, code=%s)",
                    task_id,
                    proc.returncode,
                )
                return False
            try:
                reader, writer = await asyncio.open_connection(
                    self._socks_host,
                    self._socks_port,
                )
            except OSError:
                await asyncio.sleep(self._poll_interval)
                continue
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass
            return True
        logger.warning(
            "xray did not open SOCKS port within %ss (task=%s)",
            self._ready_timeout,
            task_id,
        )
        return False

    def terminate(self, task_id: int) -> None:
        """Stop xray for ``task_id`` and remove its on-disk config."""
        proc = self._processes.pop(task_id, None)
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                logger.warning("xray did not exit on SIGTERM, killing (task=%s)", task_id)
                proc.kill()
                proc.wait(timeout=2.0)
        config_path = self._configs.pop(task_id, None)
        if config_path is not None:
            try:
                config_path.unlink(missing_ok=True)
            except OSError:
                logger.exception("failed to remove %s", config_path)

    def terminate_all(self) -> None:
        """Stop every xray started through this manager."""
        task_ids = list(self._processes)
        for task_id in task_ids:
            self.terminate(task_id)

    @contextmanager
    def task(self, task_id: int, link: str) -> Iterator[Path]:
        """Context manager that starts xray and guarantees cleanup."""
        config_path = self.start(task_id, link)
        try:
            yield config_path
        finally:
            self.terminate(task_id)


def _safe_command(cmd: list[str]) -> str:
    """Render a subprocess command for logging without leaking secrets.

    The vless UUID is the only secret we may accidentally include; the
    first positional argument after ``run`` is the config file path so
    we just render ``argv[0..3]`` and the rest as ellipsis.
    """
    if not cmd:
        return ""
    head = " ".join(shlex.quote(part) for part in cmd[:3])
    return head + " …" if len(cmd) > 3 else head


def can_connect(host: str, port: int, timeout: float = 0.1) -> bool:
    """Best-effort non-blocking check used by tests and the CLI."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False
