"""AmneziaWG config parser and userspace tunnel process manager.

The probe receives a task with ``protocol='amneziawg'`` whose ``link``
is the official AmneziaVPN INI config (``[Interface]``/``[Peer]``
sections). There is no ``amneziawg`` support in ``xray-core``, so the
tunnel is provided by ``awgsocks`` — a small Go helper shipped in the
probe image that wires ``amneziawg-go`` to a gVisor userspace network
stack and exposes it as a SOCKS5 proxy on ``127.0.0.1:1080``.

Why userspace and not a kernel TUN: probes run unprivileged
(``--network=host``, uid 1000, no ``/dev/net/tun``, no ``NET_ADMIN``),
so a TUN device is not an option. gVisor gives us TCP/UDP/ICMP inside
the process with no privileges at all.

This module mirrors :mod:`xspeed_probe.xray`: parse the payload, write
a JSON config with owner-only permissions, spawn the helper, wait for
its SOCKS port, and tear everything down deterministically.
"""

from __future__ import annotations

import asyncio
import atexit
import base64
import binascii
import logging
import os
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from xspeed_probe.xray import write_config

logger = logging.getLogger(__name__)

DEFAULT_MTU = 1420

# AmneziaVPN exports Curve25519 keys base64-encoded (same as wg-quick)
# while amneziawg-go's UAPI parses them as hex, so the probe converts
# on the way in. A raw 32-byte key is always 32 bytes once decoded.
KEY_LENGTH_BYTES = 32

_ORPHAN_CONFIG_GLOBS = ("awg-*.json", "awg-*.log")

# Obfuscation knobs understood by ``amneziawg-go``'s UAPI. The three
# ``int`` fields are plain counters, ``h*`` accept a ``lo-hi`` range and
# ``i*`` carry the packet-chain spec; all are forwarded verbatim because
# every value changes the bytes on the wire and therefore must match
# the server side exactly.
_JUNK_INT_FIELDS = ("jc", "jmin", "jmax")
_PADDING_FIELDS = ("s1", "s2", "s3", "s4")
_HEADER_FIELDS = ("h1", "h2", "h3", "h4")
_IPACKET_FIELDS = ("i1", "i2", "i3", "i4", "i5")

_OBFUSCATION_FIELDS = _JUNK_INT_FIELDS + _PADDING_FIELDS + _HEADER_FIELDS + _IPACKET_FIELDS


class AwgConfigError(ValueError):
    """Raised when an AmneziaWG INI config cannot be turned into a tunnel."""


@dataclass(slots=True)
class AwgPeer:
    """A single ``[Peer]`` block of an AmneziaWG config."""

    public_key: str
    endpoint: str
    preshared_key: str = ""
    allowed_ips: list[str] = field(default_factory=list)
    persistent_keepalive: str = ""


@dataclass(slots=True)
class AwgConfig:
    """Parsed ``[Interface]`` block plus its ``[Peer]`` blocks."""

    private_key: str
    addresses: list[str]
    peers: list[AwgPeer]
    dns: list[str] = field(default_factory=list)
    mtu: int = DEFAULT_MTU
    listen_port: int = 0
    obfuscation: dict[str, Any] = field(default_factory=dict)


def _split_list(raw: str) -> list[str]:
    """Split a comma-separated INI value, dropping empty items."""
    return [item.strip() for item in raw.split(",") if item.strip()]


def _decode_key(value: str, field_name: str, *, required: bool = True) -> str:
    """Return the hex form of a base64-encoded Curve25519 key.

    ``amneziawg-go``'s UAPI only accepts hex, while the AmneziaVPN client
    exports base64 — so every key is decoded here, once, and the rest of
    the pipeline works in the encoding the device expects. An empty
    value is rejected unless the key is optional.
    """
    if not value:
        if required:
            raise AwgConfigError(f"amneziawg config has an empty {field_name}")
        return ""
    try:
        raw = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise AwgConfigError(f"amneziawg {field_name} is not valid base64: {exc}") from exc
    if len(raw) != KEY_LENGTH_BYTES:
        raise AwgConfigError(
            f"amneziawg {field_name} decodes to {len(raw)} bytes, expected {KEY_LENGTH_BYTES}",
        )
    return raw.hex()


def _parse_ini(text: str) -> dict[str, dict[str, str]]:
    """Parse ``key = value`` INI text into ``{section: {key: value}}``.

    AmneziaVPN exports the config with spaces around ``=`` and, on
    Windows clients, with CRLF line endings, so both are normalised
    before parsing. Blank lines and ``#``/``;`` comments are skipped.
    A key that appears twice keeps its last value, matching how
    ``wg-quick`` reads the same file.
    """
    sections: dict[str, dict[str, str]] = {}
    current: dict[str, str] | None = None
    body = text.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    for raw_line in body.split("\n"):
        line = raw_line.strip()
        if not line or line.startswith(("#", ";")):
            continue
        if line.startswith("[") and line.endswith("]"):
            name = line[1:-1].strip().lower()
            current = sections.setdefault(name, {})
            continue
        if current is None or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key:
            current[key.lower()] = value.strip()
    return sections


def _require_interface_value(interface: dict[str, str], key: str) -> str:
    value = interface.get(key.lower(), "")
    if not value:
        raise AwgConfigError(f"amneziawg config is missing [Interface] {key}")
    return value


def _parse_obfuscation(interface: dict[str, str]) -> dict[str, Any]:
    """Collect the AWG obfuscation knobs present in ``[Interface]``."""
    parsed: dict[str, Any] = {}
    for name in _JUNK_INT_FIELDS + _PADDING_FIELDS:
        raw = interface.get(name)
        if raw is None or raw == "":
            continue
        try:
            parsed[name] = int(raw)
        except ValueError as exc:
            raise AwgConfigError(f"amneziawg [Interface] {name} is not a number: {raw!r}") from exc
    for name in _HEADER_FIELDS + _IPACKET_FIELDS:
        raw = interface.get(name)
        if raw:
            parsed[name] = raw
    return parsed


def _parse_peers(peers: dict[str, str]) -> AwgPeer:
    """Turn a single ``[Peer]`` block into an :class:`AwgPeer`."""
    public_key = peers.get("publickey", "")
    endpoint = peers.get("endpoint", "")
    if not public_key:
        raise AwgConfigError("amneziawg config is missing [Peer] PublicKey")
    if not endpoint:
        raise AwgConfigError("amneziawg config is missing [Peer] Endpoint")
    if ":" not in endpoint:
        raise AwgConfigError(f"amneziawg [Peer] Endpoint must be host:port, got {endpoint!r}")
    return AwgPeer(
        public_key=_decode_key(public_key, "[Peer] PublicKey"),
        endpoint=endpoint,
        preshared_key=_decode_key(
            peers.get("presharedkey", ""),
            "[Peer] PresharedKey",
            required=False,
        ),
        allowed_ips=_split_list(peers.get("allowedips", "")),
        persistent_keepalive=peers.get("persistentkeepalive", ""),
    )


def parse_awg_config(text: str) -> AwgConfig:
    """Parse an AmneziaVPN INI config into an :class:`AwgConfig`.

    Raises :class:`AwgConfigError` when the payload could never produce
    a working tunnel — no ``[Interface]``, no private key, no address, or
    no ``[Peer]`` endpoint. The result is intentionally permissive about
    everything else: unknown keys are ignored and every obfuscation knob
    is forwarded verbatim so a future AWG release keeps working without
    a probe update.
    """
    sections = _parse_ini(text)
    interface = sections.get("interface")
    if interface is None:
        raise AwgConfigError("amneziawg config has no [Interface] section")
    private_key = _require_interface_value(interface, "PrivateKey")
    addresses = _split_list(_require_interface_value(interface, "Address"))
    if not addresses:
        raise AwgConfigError("amneziawg [Interface] Address is empty")
    peer_block = sections.get("peer")
    if peer_block is None:
        raise AwgConfigError("amneziawg config has no [Peer] section")
    mtu_raw = interface.get("mtu", "")
    mtu = DEFAULT_MTU
    if mtu_raw:
        try:
            mtu = int(mtu_raw)
        except ValueError as exc:
            raise AwgConfigError(f"amneziawg [Interface] MTU is not a number: {mtu_raw!r}") from exc
        if mtu <= 0:
            raise AwgConfigError(f"amneziawg [Interface] MTU must be positive, got {mtu}")
    listen_port = 0
    port_raw = interface.get("listenport", "")
    if port_raw:
        try:
            listen_port = int(port_raw)
        except ValueError as exc:
            raise AwgConfigError(
                f"amneziawg [Interface] ListenPort is not a number: {port_raw!r}",
            ) from exc
        if not 1 <= listen_port <= 65535:
            raise AwgConfigError(f"amneziawg [Interface] ListenPort out of range: {listen_port}")
    return AwgConfig(
        private_key=_decode_key(private_key, "[Interface] PrivateKey"),
        addresses=addresses,
        peers=[_parse_peers(peer_block)],
        dns=_split_list(interface.get("dns", "")),
        mtu=mtu,
        listen_port=listen_port,
        obfuscation=_parse_obfuscation(interface),
    )


def build_awg_helper_config(config: AwgConfig) -> dict[str, Any]:
    """Return the JSON payload consumed by the ``awgsocks`` helper.

    The shape mirrors ``amneziawg-go``'s UAPI one-to-one so the helper
    stays a dumb translator: the only value it has to derive on its own
    is the peer's endpoint, because ``amneziawg-go`` accepts IP literals
    only (``netip.ParseAddrPort``) while AmneziaVPN hands out hostnames.
    """
    interface: dict[str, Any] = {
        "private_key": config.private_key,
        "addresses": config.addresses,
        "dns": config.dns,
        "mtu": config.mtu,
        "listen_port": config.listen_port,
    }
    interface.update(config.obfuscation)
    peers = [
        {
            "public_key": peer.public_key,
            "endpoint": peer.endpoint,
            "preshared_key": peer.preshared_key,
            "allowed_ips": peer.allowed_ips,
            "persistent_keepalive": peer.persistent_keepalive,
        }
        for peer in config.peers
    ]
    return {"interface": interface, "peers": peers}


def _purge_orphan_configs(config_dir: Path) -> None:
    """Best-effort cleanup of configs left behind by a SIGKILL'd helper."""
    try:
        for pattern in _ORPHAN_CONFIG_GLOBS:
            for path in config_dir.glob(pattern):
                try:
                    path.unlink()
                except OSError:
                    logger.debug("failed to remove orphan config %s", path)
    except OSError:
        logger.debug("failed to enumerate orphan configs in %s", config_dir)


def _reap_awg(
    proc: subprocess.Popen[bytes],
    stderr_handle: Any,
    stderr_path: Path,
    task_id: int,
) -> None:
    """Wait for ``proc`` to exit, escalating to ``SIGKILL`` if needed.

    Mirrors :func:`xspeed_probe.xray._reap_xray`: runs in a daemon
    thread so the asyncio loop stays free, and only removes the stderr
    log once the writer has actually exited. The log path is passed in
    explicitly because a file object built by :func:`os.fdopen` reports
    the raw descriptor as its ``name``, not a path.
    """
    try:
        proc.wait(timeout=5.0)
    except subprocess.TimeoutExpired:
        proc.kill()
        try:
            proc.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            logger.warning(
                "awgsocks did not exit after SIGKILL (task=%s, pid=%s)",
                task_id,
                proc.pid,
            )
    if stderr_handle is not None:
        try:
            stderr_handle.close()
        except OSError:
            logger.exception("failed to close awgsocks stderr log (task=%s)", task_id)
    try:
        stderr_path.unlink(missing_ok=True)
    except OSError:
        logger.exception("failed to remove %s", stderr_path)


class AwgManager:
    """Track running ``awgsocks`` subprocesses and clean them up on shutdown."""

    def __init__(
        self,
        *,
        socks_host: str = "127.0.0.1",
        socks_port: int = 1080,
        config_dir: Path | str = "/tmp",  # noqa: S108 - ephemeral awg configs
        awgproxy_binary: str = "/usr/local/bin/awgsocks",
        ready_timeout: float = 15.0,
        poll_interval: float = 0.1,
    ) -> None:
        # Same reasoning as XrayManager (DAT-459, item d): the config on
        # disk holds the tunnel's private key, so it is created
        # owner-only and never leaves the probe.
        try:
            os.umask(0o077)
        except OSError:
            logger.debug("could not tighten umask for awgsocks config files")
        self._socks_host = socks_host
        self._socks_port = socks_port
        self._config_dir = Path(config_dir)
        self._binary = awgproxy_binary
        self._ready_timeout = ready_timeout
        self._poll_interval = poll_interval
        self._processes: dict[int, subprocess.Popen[bytes]] = {}
        self._configs: dict[int, Path] = {}
        self._stderr_logs: dict[int, Path] = {}
        self._stderr_files: dict[int, Any] = {}
        atexit.register(_purge_orphan_configs, self._config_dir)

    @property
    def socks_url(self) -> str:
        return f"socks5://{self._socks_host}:{self._socks_port}"

    def start(
        self,
        task_id: int,
        link: str,
        *,
        network_interface: str | None = None,
    ) -> Path:
        """Spawn ``awgsocks`` for ``task_id`` using the INI config ``link``.

        ``network_interface`` is the Linux NIC the tunnel's outgoing UDP
        must be pinned to (the ``NETWORK_INTERFACE`` setting). Unlike
        xray's ``sendThrough`` it needs no IP literal: the helper applies
        ``SO_BINDTODEVICE`` to its own socket.

        Returns the path to the on-disk JSON config. Raises
        :class:`AwgConfigError` when the config is malformed and
        :class:`FileNotFoundError` when the helper binary is missing.
        """
        config = parse_awg_config(link)
        helper_config = build_awg_helper_config(config)
        config_path = self._config_dir / f"awg-{task_id}.json"
        write_config(helper_config, config_path)
        try:
            os.chmod(config_path, 0o600)
        except OSError:
            logger.debug("could not chmod 0600 on %s", config_path)
        stderr_path = self._config_dir / f"awg-{task_id}.log"
        stderr_path.unlink(missing_ok=True)
        stderr_fd = os.open(
            str(stderr_path),
            os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
            0o600,
        )
        stderr_file = os.fdopen(stderr_fd, "w", encoding="utf-8")
        cmd = [
            self._binary,
            "-config",
            str(config_path),
            "-socks",
            f"{self._socks_host}:{self._socks_port}",
        ]
        if network_interface:
            cmd += ["-bind-device", network_interface]
        # The argv carries no secrets: the private key lives inside the
        # config file, not in the command line.
        logger.info("starting awgsocks for task %s: %s", task_id, " ".join(cmd))
        proc = subprocess.Popen(  # noqa: S603 - argv built from validated settings
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
        """Poll the SOCKS port until ``awgsocks`` accepts a connection.

        Returns ``True`` when the port is open within ``ready_timeout``;
        ``False`` if the helper exits early or the port never opens. A
        handshake with the tunnel endpoint can take a couple of seconds
        on a mobile link, so the default timeout is more generous than
        xray's.
        """
        deadline = time.monotonic() + self._ready_timeout
        proc = self._processes.get(task_id)
        if proc is None:
            return False
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                self._log_startup_failure(task_id, proc.returncode)
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
            "awgsocks did not open SOCKS port within %ss (task=%s)",
            self._ready_timeout,
            task_id,
        )
        return False

    def terminate(self, task_id: int) -> None:
        """Stop ``awgsocks`` for ``task_id`` and remove its on-disk config.

        Same contract as :meth:`xspeed_probe.xray.XrayManager.terminate`:
        signalling is synchronous, waiting happens on a background
        thread, and the stderr log is removed only once the writer is
        gone.
        """
        proc = self._processes.pop(task_id, None)
        stderr_handle = self._stderr_files.pop(task_id, None)
        stderr_path = self._stderr_logs.pop(task_id, None)
        if proc is not None and proc.poll() is None:
            proc.terminate()
            threading.Thread(
                target=_reap_awg,
                args=(proc, stderr_handle, stderr_path, task_id),
                daemon=True,
                name=f"xspeed-awg-reap-{task_id}",
            ).start()
        elif stderr_handle is not None:
            try:
                stderr_handle.close()
            except OSError:
                logger.exception("failed to close awgsocks stderr log (task=%s)", task_id)
            if stderr_path is not None:
                try:
                    stderr_path.unlink(missing_ok=True)
                except OSError:
                    logger.exception("failed to remove %s", stderr_path)
        config_path = self._configs.pop(task_id, None)
        if config_path is not None:
            try:
                config_path.unlink(missing_ok=True)
            except OSError:
                logger.exception("failed to remove %s", config_path)

    def terminate_all(self) -> None:
        """Stop every ``awgsocks`` started through this manager."""
        for task_id in list(self._processes):
            self.terminate(task_id)

    def _log_startup_failure(self, task_id: int, returncode: int | None) -> None:
        """Log the helper's stdout when it exited before the SOCKS port came up."""
        stderr_path = self._stderr_logs.get(task_id)
        if stderr_path is None or not stderr_path.exists():
            logger.warning(
                "awgsocks exited before becoming ready (task=%s, code=%s)",
                task_id,
                returncode,
            )
            return
        try:
            tail = stderr_path.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            logger.warning(
                "awgsocks exited before becoming ready (task=%s, code=%s) "
                "and its log could not be read",
                task_id,
                returncode,
                exc_info=True,
            )
            return
        logger.warning(
            "awgsocks exited before becoming ready (task=%s, code=%s): %s",
            task_id,
            returncode,
            tail or "<empty log>",
        )


__all__ = [
    "DEFAULT_MTU",
    "AwgConfig",
    "AwgConfigError",
    "AwgManager",
    "AwgPeer",
    "build_awg_helper_config",
    "parse_awg_config",
]
