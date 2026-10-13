from __future__ import annotations

from xspeed_probe.xray import VlessEndpoint, build_xray_config


def test_config_has_socks_inbound_and_vless_outbound() -> None:
    endpoint = VlessEndpoint.from_uri(
        "vless://11111111-2222-3333-4444-555555555555@vps.example.com:443"
        "?security=tls&sni=cdn.example.com&type=ws&path=%2Fws"
    )
    config = build_xray_config(endpoint, socks_host="127.0.0.1", socks_port=1080)

    inbounds = config["inbounds"]
    assert len(inbounds) == 1
    inbound = inbounds[0]
    assert inbound["protocol"] == "socks"
    assert inbound["listen"] == "127.0.0.1"
    assert inbound["port"] == 1080
    assert inbound["settings"]["auth"] == "noauth"

    outbounds = config["outbounds"]
    assert len(outbounds) == 1
    outbound = outbounds[0]
    assert outbound["protocol"] == "vless"
    vnext = outbound["settings"]["vnext"][0]
    assert vnext["address"] == "vps.example.com"
    assert vnext["port"] == 443
    user = vnext["users"][0]
    assert user["id"] == "11111111-2222-3333-4444-555555555555"

    stream_settings = outbound["streamSettings"]
    assert stream_settings["network"] == "ws"
    assert stream_settings["security"] == "tls"
    assert stream_settings["tlsSettings"]["serverName"] == "cdn.example.com"
    assert stream_settings["wsSettings"]["path"] == "/ws"


def test_config_tcp_no_security_keeps_minimum_stream_settings() -> None:
    endpoint = VlessEndpoint.from_uri(
        "vless://11111111-2222-3333-4444-555555555555@example.com:443"
    )
    config = build_xray_config(endpoint)
    outbound = config["outbounds"][0]
    assert outbound["streamSettings"] == {"network": "tcp"}
