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


def test_config_xhttp_reality_emits_xhttp_and_reality_settings() -> None:
    endpoint = VlessEndpoint.from_uri(
        "vless://1e1e631e-8574-4f06-a2c0-ad5bcfd524ce@rugate.dataforest.app:443"
        "?extra=%7B%22mode%22%3A%22auto%22%2C%22xPaddingBytes%22%3A%22100-1000%22%7D"
        "&fp=chrome&mode=auto&path=%2F"
        "&pbk=GWBfwfckJglpT4lgnu9zuK0mWxW-ppAG8PoQtF2i824"
        "&security=reality&sid=c1c2c3c4&sni=amd.com"
        "&spx=%2Fb3fe975b6bfba74&type=xhttp"
    )
    config = build_xray_config(endpoint)
    stream_settings = config["outbounds"][0]["streamSettings"]

    assert stream_settings["network"] == "xhttp"
    xhttp = stream_settings["xhttpSettings"]
    assert xhttp["path"] == "/"
    assert xhttp["mode"] == "auto"
    assert xhttp["extra"] == {"xPaddingBytes": "100-1000"}

    assert stream_settings["security"] == "reality"
    reality = stream_settings["realitySettings"]
    assert reality == {
        "serverName": "amd.com",
        "fingerprint": "chrome",
        "shortId": "c1c2c3c4",
        "password": "GWBfwfckJglpT4lgnu9zuK0mWxW-ppAG8PoQtF2i824",
        "spiderX": "/b3fe975b6bfba74",
    }
    assert "tlsSettings" not in stream_settings


def test_config_reality_without_optional_fields_omits_them() -> None:
    endpoint = VlessEndpoint.from_uri(
        "vless://11111111-2222-3333-4444-555555555555@example.com:443"
        "?security=reality&type=tcp&pbk=PBK"
    )
    config = build_xray_config(endpoint)
    reality = config["outbounds"][0]["streamSettings"]["realitySettings"]
    assert reality == {
        "serverName": "example.com",
        "password": "PBK",
    }
