from __future__ import annotations

from xspeed_probe.netif import ensure_interface_available, is_interface_available


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
