"""Network interface availability check.

If the operator pins ``NETWORK_INTERFACE`` we must verify the named
interface actually exists on the host before launching xray — a missing
interface means the speedtest result would go out the wrong NIC and is
therefore useless.

For an existing pinned interface we additionally resolve its primary
IPv4 address with :func:`resolve_interface_ip` so the probe can pass it
to xray's ``sendThrough`` field. xray requires an IP literal there, not
an interface name, so the kernel helper is unavoidable.
"""

from __future__ import annotations

import fcntl
import ipaddress
import logging
import socket

logger = logging.getLogger(__name__)

# ``SIOCGIFADDR`` from ``<linux/sockios.h>``. Hardcoded because pulling in
# ``linux`` / ``socket`` constants via the ``fcntl`` helper module is
# overkill — the value is stable since the 1990s and identical across
# architectures.
_SIOCGIFADDR = 0x8915
_IFREQ_NAME_LEN = 16


class NetworkInterfaceUnavailableError(Exception):
    """Raised when the configured ``NETWORK_INTERFACE`` is not present."""


class InterfaceNoIpError(Exception):
    """Raised when the interface exists but has no usable IPv4 address."""


def list_interfaces() -> list[str]:
    """Return the names of all network interfaces visible to the kernel."""
    return [name for _index, name in socket.if_nameindex()]


def is_interface_available(name: str) -> bool:
    """Return ``True`` iff ``name`` is a network interface on this machine."""
    if not name:
        return False
    try:
        available = {n for _idx, n in socket.if_nameindex()}
    except OSError:
        logger.exception("failed to enumerate network interfaces")
        return False
    return name in available


def ensure_interface_available(name: str | None) -> bool:
    """Validate ``name`` if it is provided.

    * When ``name`` is ``None`` or empty the check is a no-op and
      ``True`` is returned — the operator simply did not pin a NIC.
    * When ``name`` is set but missing the function logs a warning and
      returns ``False`` so the caller can abort the task with the
      ``netif_unavailable`` error code without crashing the process.
    """
    if not name:
        return True
    if is_interface_available(name):
        return True
    logger.warning(
        "NETWORK_INTERFACE=%s not found on host; available=%s",
        name,
        list_interfaces(),
    )
    return False


def _siocgifaddr(name: str) -> str:
    """Return the first IPv4 address assigned to ``name`` via SIOCGIFADDR.

    Uses a datagram socket because the kernel does not require any
    permissions for the SIOCGIFADDR ioctl and a SOCK_DGRAM socket can be
    opened on every Unix we run on (the probe runs in a container with
    ``--network=host``). The result is parsed through
    :func:`socket.inet_ntoa` so a malformed ioctl reply surfaces as a
    clearly-attributable :class:`OSError`.
    """
    if not name:
        raise NetworkInterfaceUnavailableError("empty interface name")
    if len(name) >= _IFREQ_NAME_LEN:
        raise NetworkInterfaceUnavailableError(
            f"interface name too long for SIOCGIFADDR ({len(name)} >= {_IFREQ_NAME_LEN})",
        )
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # ``struct ifreq`` (Linux): 16 bytes of name (NUL-padded) then
        # a sockaddr_un for the address. For SIOCGIFADDR the address
        # family is AF_INET and the IPv4 lives at bytes 20..24.
        buffer = name.encode("ascii").ljust(_IFREQ_NAME_LEN, b"\x00") + b"\x00" * 16
        result = fcntl.ioctl(sock.fileno(), _SIOCGIFADDR, buffer)
    except OSError as exc:
        # ENODEV ("No such device") surfaces here when the operator
        # pins a name that exists in ``if_nameindex`` but has no IP at
        # all (e.g. a freshly-plugged USB NIC waiting for DHCP). Promote
        # that specific case to :class:`InterfaceNoIpError` so callers
        # can report a distinct error code.
        if exc.errno == 99:  # ENODEV
            raise InterfaceNoIpError(
                f"interface {name!r} has no IPv4 address (SIOCGIFADDR: {exc})",
            ) from exc
        raise NetworkInterfaceUnavailableError(
            f"SIOCGIFADDR failed for {name!r}: {exc}",
        ) from exc
    finally:
        sock.close()
    try:
        ip = socket.inet_ntoa(result[20:24])
    except OSError:
        # ``inet_ntoa`` raises OSError for malformed input; surface it
        # with the same semantic as a missing IP.
        raise InterfaceNoIpError(
            f"interface {name!r} returned a non-IPv4 SIOCGIFADDR payload",
        ) from None
    return ip


def resolve_interface_ip(name: str) -> str:
    """Return the IPv4 address to bind xray's ``sendThrough`` to.

    ``name`` is the operator-pinned ``NETWORK_INTERFACE``. The function
    fails loudly if the interface is missing, has no IPv4, or only has
    a loopback / link-local address — xray rejects any of those for
    ``sendThrough``, so the caller should mark the task as
    ``netif_unavailable`` / ``netif_no_ip`` instead of letting xray dial
    out through the wrong NIC.
    """
    if not is_interface_available(name):
        available = list_interfaces()
        raise NetworkInterfaceUnavailableError(
            f"interface {name!r} not found; available: {', '.join(available)}",
        )
    try:
        ip = _siocgifaddr(name)
    except InterfaceNoIpError:
        raise
    except NetworkInterfaceUnavailableError:
        raise
    except OSError as exc:
        raise NetworkInterfaceUnavailableError(
            f"failed to query IPv4 for {name!r}: {exc}",
        ) from exc
    try:
        parsed = ipaddress.ip_address(ip)
    except ValueError as exc:
        raise InterfaceNoIpError(
            f"interface {name!r} returned a non-IPv4 address {ip!r}",
        ) from exc
    if parsed.is_loopback or parsed.is_link_local:
        raise InterfaceNoIpError(
            f"interface {name!r} only has a non-routable address {ip!r}; "
            "xray sendThrough requires a routable IPv4",
        )
    return ip
