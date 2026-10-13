"""Network interface availability check.

If the operator pins ``NETWORK_INTERFACE`` we must verify the named
interface actually exists on the host before launching xray — a missing
interface means the speedtest result would go out the wrong NIC and is
therefore useless.
"""

from __future__ import annotations

import logging
import socket

logger = logging.getLogger(__name__)


class NetworkInterfaceUnavailableError(Exception):
    """Raised when the configured ``NETWORK_INTERFACE`` is not present."""


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
