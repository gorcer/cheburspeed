"""Tests for the AmneziaWG config parser and the ``awgsocks`` manager."""

from __future__ import annotations

import json
import os
import stat
import subprocess
from pathlib import Path

import pytest

from xspeed_probe.awg import (
    AwgConfigError,
    AwgManager,
    build_awg_helper_config,
    parse_awg_config,
)

# Shape of a real AmneziaVPN export: base64 keys, spaces around "=", an
# empty line between the sections. The key material is throwaway.
AMNEZIA_EXPORT = """[Interface]
PrivateKey = cNx0N+nxLtxHhbHG1M1tM8pDvDNnOWGbkpHFrTnHqAA=
Address = 10.8.40.106/32
DNS = 1.1.1.1,1.0.0.1
Jc = 3
Jmin = 10
Jmax = 50
S1 = 150
S2 = 224
S3 = 0
S4 = 0
H1 = 1232863036
H2 = 1441878557
H3 = 636923766
H4 = 214107436

[Peer]
PublicKey = fgI6VqMCIvjErRo2vL6XQhDqfBiK+FTxLBRLxMCCZVU=
PresharedKey = pkcDjyh0jW8RFTLvhEEeMk4SPT7WUEPGyN/ZXGQE2Vk=
Endpoint = in.oneyearsolution.com:40974
AllowedIPs = 0.0.0.0/0, ::/0
PersistentKeepalive = 25
"""


def test_parse_amnezia_export() -> None:
    config = parse_awg_config(AMNEZIA_EXPORT)
    assert config.addresses == ["10.8.40.106/32"]
    assert config.dns == ["1.1.1.1", "1.0.0.1"]
    assert config.mtu == 1420
    assert config.listen_port == 0
    assert config.obfuscation == {
        "jc": 3,
        "jmin": 10,
        "jmax": 50,
        "s1": 150,
        "s2": 224,
        "s3": 0,
        "s4": 0,
        "h1": "1232863036",
        "h2": "1441878557",
        "h3": "636923766",
        "h4": "214107436",
    }
    (peer,) = config.peers
    assert peer.endpoint == "in.oneyearsolution.com:40974"
    assert peer.allowed_ips == ["0.0.0.0/0", "::/0"]
    assert peer.persistent_keepalive == "25"


def test_keys_are_decoded_from_base64_to_hex() -> None:
    """amneziawg-go's UAPI parses hex; AmneziaVPN exports base64."""
    config = parse_awg_config(AMNEZIA_EXPORT)
    assert len(config.private_key) == 64
    assert int(config.private_key, 16) >= 0
    (peer,) = config.peers
    assert len(peer.public_key) == 64
    assert len(peer.preshared_key) == 64


def test_helper_config_is_hex_and_keeps_endpoint_hostname() -> None:
    """The endpoint stays a hostname — the Go helper resolves it."""
    payload = build_awg_helper_config(parse_awg_config(AMNEZIA_EXPORT))
    interface = payload["interface"]
    assert interface["private_key"] == parse_awg_config(AMNEZIA_EXPORT).private_key
    assert interface["jc"] == 3
    assert interface["h4"] == "214107436"
    assert "i1" not in interface
    (peer,) = payload["peers"]
    assert peer["endpoint"] == "in.oneyearsolution.com:40974"
    assert peer["allowed_ips"] == ["0.0.0.0/0", "::/0"]
    assert peer["persistent_keepalive"] == "25"


PRIVATE_KEY_LINE = "PrivateKey = cNx0N+nxLtxHhbHG1M1tM8pDvDNnOWGbkpHFrTnHqAA=\n"
ADDRESS_LINE = "Address = 10.8.40.106/32\n"
ENDPOINT_LINE = "Endpoint = in.oneyearsolution.com:40974\n"


def test_parse_accepts_crlf_and_bom() -> None:
    """Windows clients export CRLF; some editors add a BOM."""
    text = "﻿" + AMNEZIA_EXPORT.replace("\n", "\r\n")
    config = parse_awg_config(text)
    assert config.addresses == ["10.8.40.106/32"]
    (peer,) = config.peers
    assert peer.endpoint == "in.oneyearsolution.com:40974"


def test_parse_honours_explicit_mtu_and_listen_port() -> None:
    text = AMNEZIA_EXPORT.replace(
        "DNS = 1.1.1.1,1.0.0.1\n",
        "DNS = 1.1.1.1,1.0.0.1\nMTU = 1280\nListenPort = 51820\n",
    )
    config = parse_awg_config(text)
    assert config.mtu == 1280
    assert config.listen_port == 51820


def test_parse_skips_comments_and_blank_lines() -> None:
    text = "# exported by AmneziaVPN\n\n; second comment\n" + AMNEZIA_EXPORT
    config = parse_awg_config(text)
    assert config.peers[0].endpoint == "in.oneyearsolution.com:40974"


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (
            "[Interface]\n"
            "PrivateKey = cNx0N+nxLtxHhbHG1M1tM8pDvDNnOWGbkpHFrTnHqAA=\n"
            "Address = 10.8.40.106/32\n",
            "no \\[Peer\\] section",
        ),
        (
            "[Peer]\nPublicKey = fgI6VqMCIvjErRo2vL6XQhDqfBiK+FTxLBRLxMCCZVU=\nEndpoint = h:1\n",
            "no \\[Interface\\] section",
        ),
        (
            AMNEZIA_EXPORT.replace(PRIVATE_KEY_LINE, ""),
            "PrivateKey",
        ),
        (
            AMNEZIA_EXPORT.replace(ADDRESS_LINE, ""),
            "Address",
        ),
        (
            AMNEZIA_EXPORT.replace(ENDPOINT_LINE, ""),
            "Endpoint",
        ),
        (
            AMNEZIA_EXPORT.replace(
                "Endpoint = in.oneyearsolution.com:40974",
                "Endpoint = in.oneyearsolution.com",
            ),
            "host:port",
        ),
        (
            AMNEZIA_EXPORT.replace(
                PRIVATE_KEY_LINE,
                "PrivateKey = not-base64!!\n",
            ),
            "not valid base64",
        ),
        (
            AMNEZIA_EXPORT.replace(
                PRIVATE_KEY_LINE,
                "PrivateKey = c2hvcnQ=\n",
            ),
            "expected 32",
        ),
    ],
)
def test_parse_rejects_broken_configs(mutation: str, message: str) -> None:
    with pytest.raises(AwgConfigError, match=message):
        parse_awg_config(mutation)


PRESHARED_KEY_LINE = "PresharedKey = pkcDjyh0jW8RFTLvhEEeMk4SPT7WUEPGyN/ZXGQE2Vk=\n"


def test_preshared_key_is_optional() -> None:
    text = AMNEZIA_EXPORT.replace(PRESHARED_KEY_LINE, "")
    config = parse_awg_config(text)
    assert config.peers[0].preshared_key == ""


def _stub_binary(tmp_path: Path) -> Path:
    """Write an executable that idles, so ``start`` has something to spawn."""
    stub = tmp_path / "awgsocks-stub"
    stub.write_text("#!/bin/sh\nexec sleep 30\n", encoding="utf-8")
    stub.chmod(0o755)
    return stub


def test_start_writes_owner_only_config_and_removes_it(tmp_path: Path) -> None:
    manager = AwgManager(
        socks_host="127.0.0.1",
        socks_port=1080,
        config_dir=tmp_path,
        awgproxy_binary=str(_stub_binary(tmp_path)),
        ready_timeout=0.2,
    )
    manager.start(7, AMNEZIA_EXPORT, network_interface="usbphone0")

    config_path = tmp_path / "awg-7.json"
    assert config_path.exists()
    mode = stat.S_IMODE(os.stat(config_path).st_mode)
    assert mode == 0o600, f"expected 0600, got {oct(mode)}"
    stored = json.loads(config_path.read_text())
    assert stored["peers"][0]["endpoint"] == "in.oneyearsolution.com:40974"
    assert stored["interface"]["jc"] == 3

    manager.terminate(7)
    assert not config_path.exists()


def test_start_builds_expected_command(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, list[str]] = {}
    real_popen = subprocess.Popen

    def fake_popen(cmd: list[str], **kwargs: object) -> subprocess.Popen[bytes]:
        captured["cmd"] = cmd
        kwargs.pop("stdout", None)
        kwargs.pop("stderr", None)
        return real_popen(  # noqa: S607 - fixed path, argv not user input
            ["/bin/true"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    manager = AwgManager(
        socks_host="127.0.0.1",
        socks_port=1080,
        config_dir=tmp_path,
        awgproxy_binary="/usr/local/bin/awgsocks",
        ready_timeout=0.2,
    )
    manager.start(3, AMNEZIA_EXPORT, network_interface="usbphone0")
    assert captured["cmd"] == [
        "/usr/local/bin/awgsocks",
        "-config",
        str(tmp_path / "awg-3.json"),
        "-socks",
        "127.0.0.1:1080",
        "-bind-device",
        "usbphone0",
    ]

    manager.start(4, AMNEZIA_EXPORT)
    assert captured["cmd"] == [
        "/usr/local/bin/awgsocks",
        "-config",
        str(tmp_path / "awg-4.json"),
        "-socks",
        "127.0.0.1:1080",
    ]


def test_start_propagates_config_errors(tmp_path: Path) -> None:
    manager = AwgManager(config_dir=tmp_path, awgproxy_binary="/bin/true")
    with pytest.raises(AwgConfigError):
        manager.start(1, "[Interface]\nPrivateKey = aa\n")


def test_socks_url_matches_the_shared_proxy_port() -> None:
    manager = AwgManager(socks_host="127.0.0.1", socks_port=1080)
    assert manager.socks_url == "socks5://127.0.0.1:1080"
