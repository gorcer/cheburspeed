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
import atexit
import json
import logging
import os
import shlex
import socket
import subprocess
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

logger = logging.getLogger(__name__)

_ORPHAN_CONFIG_GLOBS = ("xray-*.json", "xray-*.log")


def _purge_orphan_configs(config_dir: Path) -> None:
    """Best-effort cleanup of configs left behind by SIGKILL'd xray runs."""
    try:
        for pattern in _ORPHAN_CONFIG_GLOBS:
            for path in config_dir.glob(pattern):
                try:
                    path.unlink()
                except OSError:
                    logger.debug("failed to remove orphan config %s", path)
    except OSError:
        logger.debug("failed to enumerate orphan configs in %s", config_dir)


def _reap_xray(
    proc: subprocess.Popen[bytes],
    stderr_handle: Any,
    task_id: int,
) -> None:
    """Wait for ``proc`` to exit, escalating to ``SIGKILL`` if needed.

    Runs in a daemon thread so the asyncio event loop stays free
    while xray tears down. Closes the stderr capture file and
    removes its on-disk copy after the process has actually exited,
    so we never yank the file from under a still-running writer.
    """
    try:
        proc.wait(timeout=5.0)
    except subprocess.TimeoutExpired:
        proc.kill()
        try:
            proc.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            logger.warning(
                "xray did not exit after SIGKILL (task=%s, pid=%s)",
                task_id,
                proc.pid,
            )
    if stderr_handle is not None:
        try:
            stderr_handle.close()
        except OSError:
            logger.exception("failed to close xray stderr log (task=%s)", task_id)
        path = getattr(stderr_handle, "name", None)
        if path:
            try:
                Path(path).unlink(missing_ok=True)
            except OSError:
                logger.exception("failed to remove %s", path)


class VlessUriError(ValueError):
    """Raised when a vless:// URI cannot be parsed into a config."""


def _first(raw_query: dict[str, list[str]], key: str, default: str = "") -> str:
    values = raw_query.get(key)
    if not values:
        return default
    return values[0]


def _parse_extra_json(raw: str) -> dict[str, Any] | None:
    """Decode the ``extra=...`` query value of a vless URI.

    Operators commonly encode it as a URL-encoded JSON object
    (e.g. ``{"mode":"auto","xPaddingBytes":"100-1000"}``). An empty or
    malformed value is reported as ``None`` rather than raising so that
    call sites can decide whether the field is mandatory.
    """
    if not raw:
        return None
    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(decoded, dict):
        return None
    return decoded


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
    raw_query: dict[str, list[str]] = field(default_factory=dict)
    mode: str = ""
    extra: dict[str, Any] | None = None
    pbk: str = ""
    sid: str = ""
    spx: str = ""
    fp: str = ""

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
        type_ = _first(decoded_query, "type", "tcp").lower()
        host_header = _first(decoded_query, "host")
        sni = _first(decoded_query, "sni") or host
        security = _first(decoded_query, "security", "none").lower()
        mode = _first(decoded_query, "mode")
        extra_raw = _first(decoded_query, "extra")
        extra = _parse_extra_json(extra_raw) if extra_raw else None
        return cls(
            uuid=uuid,
            host=host,
            port=port,
            security=security,
            sni=sni,
            flow=_first(decoded_query, "flow"),
            type_=type_,
            path=_first(decoded_query, "path"),
            host_header=host_header,
            raw_query=decoded_query,
            mode=mode,
            extra=extra,
            pbk=_first(decoded_query, "pbk"),
            sid=_first(decoded_query, "sid"),
            spx=_first(decoded_query, "spx"),
            fp=_first(decoded_query, "fp"),
        )


def build_xray_config(
    endpoint: VlessEndpoint,
    *,
    socks_host: str = "127.0.0.1",
    socks_port: int = 1080,
    send_through: str | None = None,
    network_interface: str | None = None,
) -> dict[str, Any]:
    """Return the xray JSON config that dials ``endpoint`` via SOCKS5.

    The shape mirrors what ``xray-core`` expects at
    https://xtls.github.io/config/ — a single vless outbound pointed at
    the remote server and a single SOCKS5 inbound that the probe's
    HTTP client then dials through.

    When ``send_through`` is provided, it is forwarded as the
    ``sendThrough`` field on the vless outbound. When
    ``network_interface`` is provided, it is forwarded to xray's
    ``sockopt.interface``. Together they bind the outbound socket to
    the configured NIC and its current IPv4, so Linux cannot send it
    through a higher-priority default route on another interface.
    An empty string or ``None`` produces the previous default
    behaviour, where xray uses the kernel's default route.

    Supports the transports operators actually use:

    * ``network: tcp`` (no transport-specific block).
    * ``network: ws`` with optional ``path`` and ``Host`` header.
    * ``network: xhttp`` (the modern HTTP-based transport) with
      ``mode``, ``path``, optional ``Host`` and an arbitrary
      ``extra`` object (``xPaddingBytes`` etc.).

    Security is split cleanly between TLS and REALITY — xray rejects
    a config that mixes them, so we emit ``tlsSettings`` for ``tls``
    and ``realitySettings`` for ``reality`` (with no
    ``tlsSettings`` at all, since REALITY owns the TLS handshake
    itself).
    """
    stream_settings: dict[str, Any] = {"network": endpoint.type_ or "tcp"}
    if endpoint.type_ == "ws":
        ws_settings: dict[str, Any] = {}
        if endpoint.path:
            ws_settings["path"] = endpoint.path
        if endpoint.host_header:
            ws_settings["host"] = endpoint.host_header
        stream_settings["wsSettings"] = ws_settings
    elif endpoint.type_ == "xhttp":
        xhttp_settings: dict[str, Any] = {}
        if endpoint.path:
            xhttp_settings["path"] = endpoint.path
        if endpoint.host_header:
            xhttp_settings["host"] = endpoint.host_header
        if endpoint.mode:
            xhttp_settings["mode"] = endpoint.mode
        if endpoint.extra:
            stripped = {key: value for key, value in endpoint.extra.items() if key != "mode"}
            if stripped:
                xhttp_settings["extra"] = stripped
        stream_settings["xhttpSettings"] = xhttp_settings
    if endpoint.security == "tls":
        stream_settings["security"] = "tls"
        stream_settings["tlsSettings"] = {
            "serverName": endpoint.sni,
            "allowInsecure": False,
        }
    elif endpoint.security == "reality":
        reality_settings: dict[str, Any] = {
            "serverName": endpoint.sni,
        }
        if endpoint.fp:
            reality_settings["fingerprint"] = endpoint.fp
        if endpoint.sid:
            reality_settings["shortId"] = endpoint.sid
        if endpoint.pbk:
            reality_settings["password"] = endpoint.pbk
        if endpoint.spx:
            reality_settings["spiderX"] = endpoint.spx
        stream_settings["security"] = "reality"
        stream_settings["realitySettings"] = reality_settings
    vless_outbound: dict[str, Any] = {
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
    }
    if send_through:
        vless_outbound["sendThrough"] = send_through
    if network_interface:
        stream_settings["sockopt"] = {"interface": network_interface}
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
        "outbounds": [vless_outbound],
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
        # Tighten the umask to 0o077 so the on-disk vless config and
        # stderr log are created with owner-only permissions (DAT-459,
        # item d). The change is local to this process so other
        # components on the host are unaffected.
        try:
            os.umask(0o077)
        except OSError:
            logger.debug("could not tighten umask for xray config files")
        self._socks_host = socks_host
        self._socks_port = socks_port
        self._config_dir = Path(config_dir)
        self._xray_binary = xray_binary
        self._ready_timeout = ready_timeout
        self._poll_interval = poll_interval
        self._processes: dict[int, subprocess.Popen[bytes]] = {}
        self._configs: dict[int, Path] = {}
        self._stderr_logs: dict[int, Path] = {}
        self._stderr_files: dict[int, Any] = {}
        # Cleanup hook for SIGKILL scenarios where the process is
        # killed before terminate() can remove the on-disk artifacts.
        atexit.register(_purge_orphan_configs, self._config_dir)

    @property
    def socks_url(self) -> str:
        return f"socks5://{self._socks_host}:{self._socks_port}"

    def start(
        self,
        task_id: int,
        link: str,
        *,
        send_through: str | None = None,
        network_interface: str | None = None,
    ) -> Path:
        """Spawn xray for ``task_id`` using the vless ``link`` from the server.

        ``send_through`` is an optional IPv4 literal and
        ``network_interface`` is the corresponding Linux interface
        name. The probe resolves the IPv4 right before each task so a
        DHCP lease change is picked up automatically. Both ``None``
        values preserve xray's default-route behaviour.

        Returns the path to the on-disk JSON config so tests can clean
        it up explicitly. Raises :class:`VlessUriError` if the link is
        malformed and :class:`FileNotFoundError` if xray is missing.
        """
        endpoint = VlessEndpoint.from_uri(link)
        config = build_xray_config(
            endpoint,
            socks_host=self._socks_host,
            socks_port=self._socks_port,
            send_through=send_through,
            network_interface=network_interface,
        )
        config_path = self._config_dir / f"xray-{task_id}.json"
        write_config(config, config_path)
        try:
            os.chmod(config_path, 0o600)
        except OSError:
            logger.debug("could not chmod 0600 on %s", config_path)
        stderr_path = self._config_dir / f"xray-{task_id}.log"
        stderr_path.unlink(missing_ok=True)
        stderr_fd = os.open(
            str(stderr_path),
            os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
            0o600,
        )
        stderr_file = os.fdopen(stderr_fd, "w", encoding="utf-8")
        cmd = [self._xray_binary, "run", "-c", str(config_path)]
        logger.info(
            "starting xray for task %s: %s",
            task_id,
            _safe_command(cmd),
        )
        proc = subprocess.Popen(  # noqa: S603 - input is parsed vless URI, validated above
            cmd,
            stdout=stderr_file,
            stderr=subprocess.STDOUT,
        )
        self._processes[task_id] = proc
        self._configs[task_id] = config_path
        self._stderr_logs[task_id] = stderr_path
        self._stderr_files[task_id] = stderr_file
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
                self._log_xray_failure(task_id, proc.returncode)
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
        """Stop xray for ``task_id`` and remove its on-disk config.

        Sending the kill signal is synchronous, but waiting for xray to
        actually exit is performed in a background thread so the
        asyncio event loop that called us is never blocked. xray
        occasionally takes a few hundred milliseconds to honour
        ``SIGTERM``; on the off chance it ignores it entirely we
        escalate to ``SIGKILL`` after five seconds. Any leftover
        process is left for ``terminate_all`` or process exit to
        clean up.

        The stderr capture file is left on disk until the next start
        of the same task — closing it here while xray is still
        writing would either corrupt the stream or leak the file
        descriptor. The next ``start`` removes any stale copy.
        """
        proc = self._processes.pop(task_id, None)
        stderr_handle = self._stderr_files.pop(task_id, None)
        if proc is not None and proc.poll() is None:
            proc.terminate()
            threading.Thread(
                target=_reap_xray,
                args=(proc, stderr_handle, task_id),
                daemon=True,
                name=f"xspeed-reap-{task_id}",
            ).start()
        elif stderr_handle is not None:
            try:
                stderr_handle.close()
            except OSError:
                logger.exception("failed to close xray stderr log (task=%s)", task_id)
        config_path = self._configs.pop(task_id, None)
        if config_path is not None:
            try:
                config_path.unlink(missing_ok=True)
            except OSError:
                logger.exception("failed to remove %s", config_path)
        self._stderr_logs.pop(task_id, None)

    def terminate_all(self) -> None:
        """Stop every xray started through this manager."""
        task_ids = list(self._processes)
        for task_id in task_ids:
            self.terminate(task_id)

    def _log_xray_failure(self, task_id: int, returncode: int | None) -> None:
        """Log xray's stderr when it exited before the SOCKS port came up."""
        stderr_path = self._stderr_logs.get(task_id)
        if stderr_path is None or not stderr_path.exists():
            logger.warning(
                "xray exited before becoming ready (task=%s, code=%s)",
                task_id,
                returncode,
            )
            return
        try:
            tail = stderr_path.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            logger.warning(
                "xray exited before becoming ready (task=%s, code=%s) "
                "and its log could not be read",
                task_id,
                returncode,
                exc_info=True,
            )
            return
        logger.warning(
            "xray exited before becoming ready (task=%s, code=%s): %s",
            task_id,
            returncode,
            tail or "<empty log>",
        )

    @contextmanager
    def task(
        self,
        task_id: int,
        link: str,
        *,
        send_through: str | None = None,
        network_interface: str | None = None,
    ) -> Iterator[Path]:
        """Context manager that starts xray and guarantees cleanup."""
        config_path = self.start(
            task_id,
            link,
            send_through=send_through,
            network_interface=network_interface,
        )
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
