from __future__ import annotations

import pytest

from xspeed_probe.xray import VlessEndpoint, VlessUriError


def test_parse_minimal_uri() -> None:
    endpoint = VlessEndpoint.from_uri(
        "vless://11111111-2222-3333-4444-555555555555@example.com:443"
    )
    assert endpoint.uuid == "11111111-2222-3333-4444-555555555555"
    assert endpoint.host == "example.com"
    assert endpoint.port == 443
    assert endpoint.security == "none"
    assert endpoint.flow == ""
    assert endpoint.type_ == "tcp"


def test_parse_uri_with_security_and_ws() -> None:
    endpoint = VlessEndpoint.from_uri(
        "vless://00000000-0000-0000-0000-000000000000@vps.example.com:8443"
        "?security=tls&sni=cdn.example.com&type=ws&path=%2Fws&host=cdn.example.com"
        "&flow=xtls-rprx-vision#my-server"
    )
    assert endpoint.uuid == "00000000-0000-0000-0000-000000000000"
    assert endpoint.host == "vps.example.com"
    assert endpoint.port == 8443
    assert endpoint.security == "tls"
    assert endpoint.sni == "cdn.example.com"
    assert endpoint.type_ == "ws"
    assert endpoint.path == "/ws"
    assert endpoint.host_header == "cdn.example.com"
    assert endpoint.flow == "xtls-rprx-vision"


def test_parse_uri_rejects_other_schemes() -> None:
    with pytest.raises(VlessUriError, match="not a vless URI"):
        VlessEndpoint.from_uri("trojan://x@y:1")
    with pytest.raises(VlessUriError, match="not a vless URI"):
        VlessEndpoint.from_uri("ss://x@y:1")


def test_parse_uri_requires_uuid() -> None:
    with pytest.raises(VlessUriError, match="missing UUID"):
        VlessEndpoint.from_uri("vless://@example.com:443")


def test_parse_uri_requires_host() -> None:
    with pytest.raises(VlessUriError, match="missing host"):
        VlessEndpoint.from_uri("vless://00000000-0000-0000-0000-000000000000@:443")


def test_parse_uri_requires_port() -> None:
    with pytest.raises(VlessUriError, match="missing port"):
        VlessEndpoint.from_uri("vless://00000000-0000-0000-0000-000000000000@example.com")
