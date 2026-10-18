from __future__ import annotations

import pytest

from xspeed_probe.netif import (
    InterfaceNoIpError,
    NetworkInterfaceUnavailableError,
    ensure_interface_available,
    is_interface_available,
    resolve_interface_ip,
)


def test_ensure_interface_available_with_none() -> None:
    assert ensure_interface_available(None) is True
    assert ensure_interface_available("") is True


def test_ensure_interface_available_for_real_loopback() -> None:
    # `lo` exists on every Unix host the probe is allowed to run on.
    assert is_interface_available("lo") is True
    assert ensure_interface_available("lo") is True


def test_ensure_interface_available_returns_false_for_missing() -> None:
    assert is_interface_available("definitely-not-an-interface-xyz") is False
    assert ensure_interface_available("definitely-not-an-interface-xyz") is False


def test_resolve_interface_ip_for_loopback_rejects_non_routable() -> None:
    # ``lo`` always exists but xray rejects loopback for sendThrough,
    # so the helper must raise :class:`InterfaceNoIpError`.
    with pytest.raises(InterfaceNoIpError):
        resolve_interface_ip("lo")


def test_resolve_interface_ip_for_missing_raises() -> None:
    with pytest.raises(NetworkInterfaceUnavailableError):
        resolve_interface_ip("definitely-not-an-interface-xyz")


def test_resolve_interface_ip_returns_routable_ipv4() -> None:
    # Use the SIOCGIFADDR ioctl directly via the public helper — the
    # assumption is that some non-loopback interface on the CI host
    # actually has an IPv4. This is true for our dev boxes; CI runners
    # usually expose ``eth0`` with a 169.254/16 link-local, which the
    # helper explicitly rejects. Skip in that case.
    import socket

    found = False
    for _idx, name in socket.if_nameindex():
        if name in {"lo", "docker0"} or name.startswith(("veth", "br-")):
            continue
        try:
            ip = resolve_interface_ip(name)
        except InterfaceNoIpError:
            continue
        assert ip.count(".") == 3
        found = True
        break
    if not found:
        pytest.skip("no routable IPv4 interface on this host; CI link-local only")
