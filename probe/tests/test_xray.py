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


def test_parse_uri_with_reality_and_xhttp() -> None:
    endpoint = VlessEndpoint.from_uri(
        "vless://1e1e631e-8574-4f06-a2c0-ad5bcfd524ce@rugate.dataforest.app:443"
        "?encryption=none"
        "&extra=%7B%22mode%22%3A%22auto%22%2C%22xPaddingBytes%22%3A%22100-1000%22%7D"
        "&fp=chrome&mode=auto&path=%2F"
        "&pbk=GWBfwfckJglpT4lgnu9zuK0mWxW-ppAG8PoQtF2i824"
        "&security=reality&sid=c1c2c3c4&sni=amd.com"
        "&spx=%2Fb3fe975b6bfba74&type=xhttp"
    )
    assert endpoint.uuid == "1e1e631e-8574-4f06-a2c0-ad5bcfd524ce"
    assert endpoint.host == "rugate.dataforest.app"
    assert endpoint.port == 443
    assert endpoint.security == "reality"
    assert endpoint.sni == "amd.com"
    assert endpoint.type_ == "xhttp"
    assert endpoint.mode == "auto"
    assert endpoint.path == "/"
    assert endpoint.extra == {"mode": "auto", "xPaddingBytes": "100-1000"}
    assert endpoint.pbk == "GWBfwfckJglpT4lgnu9zuK0mWxW-ppAG8PoQtF2i824"
    assert endpoint.sid == "c1c2c3c4"
    assert endpoint.spx == "/b3fe975b6bfba74"
    assert endpoint.fp == "chrome"


def test_parse_uri_extra_malformed_returns_none() -> None:
    endpoint = VlessEndpoint.from_uri(
        "vless://00000000-0000-0000-0000-000000000000@example.com:443?extra=not-json&type=xhttp"
    )
    assert endpoint.extra is None


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
