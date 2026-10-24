#!/usr/bin/env bash
# install-probe-native.sh — install a CheburSpeed probe without Docker.
#
# Use this on hosts that have no container runtime but do have Python
# >= 3.12 (DAT-612 — the Orenburg node 193.124.44.72, which already
# runs a 3x-ui/xray panel and PostgreSQL).
#
# The script:
#   1. installs the missing OS packages (unzip, python3-venv);
#   2. installs xray-core into /usr/local/bin/xray — the probe spawns
#      it itself to build the local SOCKS5 tunnel, so it must be the
#      SAME version the Docker image ships, otherwise xhttp links are
#      rejected with "unknown transport protocol: xhttp";
#   3. unpacks the probe sources into /opt/xspeed-probe and installs
#      them into a dedicated venv;
#   4. installs the systemd unit and the env file.
#
# Usage:
#   ./install-probe-native.sh --source /path/to/cheburspeed [options]
#
# Options:
#   --source DIR     probe source tree (must contain probe/pyproject.toml)
#   --xray FILE      xray binary to install (default: download
#                     $XRAY_VERSION from the Xray-core releases)
#   --xray-version V xray-core version to download (default: 26.10.17)
#   --env-file FILE  env file to install as /etc/xspeed-probe.env
#                     (default: infra/probe-native/xspeed-probe.env.example)
#   --no-start       install everything but do not enable/start the unit
#
# The probe source and the xray binary can also be fetched by the
# caller and passed in — that is what the Orenburg deployment did
# (source from the repo tarball, xray extracted from the
# teddysun/xray:26.10.17 image so the version matches byte for byte).

set -euo pipefail

INSTALL_DIR="/opt/xspeed-probe"
UNIT_PATH="/etc/systemd/system/xspeed-probe.service"
ENV_PATH="/etc/xspeed-probe.env"
XRAY_VERSION="26.10.17"
XRAY_BINARY=""
SOURCE_DIR=""
ENV_FILE=""
START=1

say()  { printf '\033[1;36m== %s ==\033[0m\n' "$*"; }
warn() { printf '\033[1;33mWARN:\033[0m %s\n' "$*" >&2; }
fail() { printf '\033[1;31mFAIL:\033[0m %s\n' "$*" >&2; exit 1; }
ok()   { printf '\033[1;32mOK:\033[0m %s\n' "$*"; }

while [[ $# -gt 0 ]]; do
    case "$1" in
        --source)        SOURCE_DIR="${2:-}"; shift 2 ;;
        --xray)          XRAY_BINARY="${2:-}"; shift 2 ;;
        --xray-version)  XRAY_VERSION="${2:-}"; shift 2 ;;
        --env-file)      ENV_FILE="${2:-}"; shift 2 ;;
        --no-start)      START=0; shift ;;
        --help|-h)       sed -n '2,30p' "$0"; exit 0 ;;
        *) fail "unknown flag: $1" ;;
    esac
done

[[ $EUID -eq 0 ]] || fail "must run as root"

# ---- 1. OS packages ----------------------------------------------------
say "1. OS packages"
if command -v apt-get >/dev/null; then
    DEBIAN_FRONTEND=noninteractive apt-get update -qq
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
        --no-install-recommends unzip python3-venv ca-certificates
else
    warn "apt-get not found — install unzip and python3-venv manually"
fi

# ---- 2. xray-core ------------------------------------------------------
say "2. xray-core"
if [[ -n "$XRAY_BINARY" ]]; then
    [[ -f "$XRAY_BINARY" ]] || fail "xray binary not found: $XRAY_BINARY"
    install -m 0755 "$XRAY_BINARY" /usr/local/bin/xray
else
    tmp="$(mktemp -d)"
    trap 'rm -rf "$tmp"' EXIT
    url="https://github.com/XTLS/Xray-core/releases/download/v${XRAY_VERSION}/Xray-linux-64.zip"
    say "download $url"
    curl -fsSL --max-time 180 "$url" -o "$tmp/xray.zip" \
        || fail "download failed — pass --xray FILE with a pre-fetched binary"
    unzip -o -q "$tmp/xray.zip" xray -d "$tmp"
    install -m 0755 "$tmp/xray" /usr/local/bin/xray
fi
xray version 2>&1 | head -1

# ---- 3. probe sources + venv -------------------------------------------
say "3. probe sources"
[[ -n "$SOURCE_DIR" ]] || fail "--source DIR is required (the repo checkout)"
PROBE_DIR="$SOURCE_DIR/probe"
[[ -f "$PROBE_DIR/pyproject.toml" ]] || fail "no probe/pyproject.toml under $SOURCE_DIR"

install -d -m 0755 "$INSTALL_DIR"
tar -C "$SOURCE_DIR" -cf - probe | tar -C "$INSTALL_DIR" -xf - --strip-components=1
ok "sources unpacked into $INSTALL_DIR"

python3 -m venv "$INSTALL_DIR/venv"
"$INSTALL_DIR/venv/bin/pip" install --quiet --upgrade pip
"$INSTALL_DIR/venv/bin/pip" install --quiet "$INSTALL_DIR"
ok "venv ready: $($INSTALL_DIR/venv/bin/python -V)"

# ---- 4. systemd unit + env ---------------------------------------------
say "4. systemd"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
install -m 0644 "$SCRIPT_DIR/xspeed-probe.service" "$UNIT_PATH"

if [[ -n "$ENV_FILE" ]]; then
    install -m 0600 "$ENV_FILE" "$ENV_PATH"
    ok "env installed from $ENV_FILE"
else
    warn "no --env-file given — install $ENV_PATH by hand before starting"
fi

systemctl daemon-reload

if [[ "$START" -eq 1 ]]; then
    systemctl enable --now xspeed-probe
    ok "xspeed-probe started"
else
    warn "not started (--no-start)"
fi

say "5. check"
systemctl status xspeed-probe --no-pager -l | head -12 || true
journalctl -u xspeed-probe -n 20 --no-pager || true
