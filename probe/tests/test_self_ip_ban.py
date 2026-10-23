"""Tests for the self-IP speedtest ban (DAT-459).

The probe must refuse to run a speedtest whose destination resolves to
its own public IP. The comparison happens in :func:`main._destination_targets_self_ip`
before xray is even spawned.
"""

from __future__ import annotations

from unittest.mock import patch

from xspeed_probe.main import _destination_targets_self_ip


def test_no_match_when_ip_unknown() -> None:
    assert _destination_targets_self_ip("https://speed.example.com/__down", None) is False


def test_no_match_when_url_invalid() -> None:
    assert _destination_targets_self_ip("", "203.0.113.1") is False


def test_no_match_when_destination_unresolvable() -> None:
    with patch(
        "xspeed_probe.main.socket.getaddrinfo",
        side_effect=__import__("socket").gaierror,
    ):
        assert (
            _destination_targets_self_ip("https://no-such-host.invalid/__down", "203.0.113.1")
            is False
        )


def test_match_when_destination_resolves_to_self_ip() -> None:
    fake_ip = "203.0.113.42"

    def fake_getaddrinfo(host: str, _port: object, *_: object) -> list[tuple]:
        return [(2, 1, 6, "", (fake_ip, 0))]

    with patch(
        "xspeed_probe.main.socket.getaddrinfo",
        side_effect=fake_getaddrinfo,
    ):
        assert _destination_targets_self_ip("https://self.example.com/__down", fake_ip) is True


def test_no_match_when_destination_resolves_to_other_ip() -> None:
    def fake_getaddrinfo(host: str, _port: object, *_: object) -> list[tuple]:
        return [(2, 1, 6, "", ("198.51.100.10", 0))]

    with patch(
        "xspeed_probe.main.socket.getaddrinfo",
        side_effect=fake_getaddrinfo,
    ):
        assert (
            _destination_targets_self_ip("https://other.example.com/__down", "203.0.113.42")
            is False
        )


def test_no_match_when_own_ip_string_is_malformed() -> None:
    assert _destination_targets_self_ip("https://speed.example.com/__down", "not-an-ip") is False


__all__ = [
    "test_match_when_destination_resolves_to_self_ip",
    "test_no_match_when_destination_resolves_to_other_ip",
    "test_no_match_when_destination_unresolvable",
    "test_no_match_when_ip_unknown",
    "test_no_match_when_own_ip_string_is_malformed",
    "test_no_match_when_url_invalid",
]
