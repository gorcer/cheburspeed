#!/usr/bin/env bash
# probe.sh — manage the CheburSpeed probe container in this folder.
#
#   ./probe.sh create        build image (if missing) and start a fresh container
#   ./probe.sh start         start an already-existing, stopped container
#   ./probe.sh status        container state + last logs + server-side registration
#   ./probe.sh stop          stop the container (kept on disk; revives via `start`)
#   ./probe.sh restart       docker restart (stop + start, no image rebuild)
#   ./probe.sh remove        destroy the container (the old destructive `stop`)
#   ./probe.sh logs [N]      tail the last N log lines (default 50), follow
#   ./probe.sh help          this message
#
# Configuration comes from .env (project root). All PROBE_* variables are
# honoured; defaults below apply when a value is missing. Required is
# PROBE_TOKEN — without it the container still starts, but the server
# rejects the WebSocket handshake with HTTP 403.
#
# Flags for `create`:
#   --no-build    skip `docker build` even if the image is missing locally
#   --pull        `docker pull` $PROBE_IMAGE instead of building
#
# Command semantics follow plain docker: `stop` pauses, `start` revives,
# `remove` destroys. `create` is the full "docker run" — replaces whatever
# container of the same name already exists.

set -euo pipefail

SCRIPT_PATH="$(readlink -f "${BASH_SOURCE[0]}")"
PROJECT_ROOT="$(cd "$(dirname "$SCRIPT_PATH")" && pwd)"
cd "$PROJECT_ROOT"

say()  { printf '\033[1;36m== %s ==\033[0m\n' "$*"; }
warn() { printf '\033[1;33mWARN:\033[0m %s\n' "$*" >&2; }
fail() { printf '\033[1;31mFAIL:\033[0m %s\n' "$*" >&2; exit 1; }
ok()   { printf '\033[1;32mOK:\033[0m %s\n' "$*"; }

require_docker() {
    command -v docker >/dev/null || fail "docker not in PATH"
}

# ---- load .env without overriding already-exported vars ---------------
if [[ -f .env ]]; then
    set -a; . ./.env; set +a
fi

# ---- defaults (overridable via .env above) -----------------------------
: "${PROBE_IMAGE:=xspeed-probe:dev}"
: "${PROBE_REGION:=default}"
: "${PROBE_CONTAINER:=xspeed-probe-${PROBE_REGION,,}}"
: "${PROBE_ALIAS:=$PROBE_REGION}"
: "${PROBE_SERVER_URL:=ws://localhost:8001/ws}"
: "${PROBE_SPEEDTEST_URL:=https://speed.cloudflare.com/__down?bytes=10000000}"
# ``ws://`` is rejected by the probe in production (DAT-459, item g)
# because the bearer token would otherwise travel in clear text.
# Local docker-compose uses plain HTTP on a forwarded port, so the
# probe image refuses to start without an explicit escape hatch.
export XSPEED_ALLOW_INSECURE_WS="${XSPEED_ALLOW_INSECURE_WS:-1}"
: "${LOG_LEVEL:=INFO}"
: "${ADMIN_API_URL:=http://localhost:8001/api/v1/admin}"
: "${ADMIN_EMAIL:=admin@xspeed.local}"
: "${ADMIN_PASSWORD:=admin1234}"

# ---- subcommands -------------------------------------------------------
cmd_create() {
    local build=1 pull=0
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --no-build) build=0 ;;
            --pull)     pull=1  ;;
            *) fail "unknown flag for create: $1" ;;
        esac
        shift
    done
    require_docker

    if [[ -z "${PROBE_TOKEN:-}" ]]; then
        warn "PROBE_TOKEN is empty in .env — the probe will start but the server will reject WS (HTTP 403)."
        PROBE_TOKEN="${PROBE_TOKEN:-unset}"
    fi

    say "create probe"
    cat <<-INFO
		   alias:        $PROBE_ALIAS
		   region:       $PROBE_REGION
		   server URL:   $PROBE_SERVER_URL
		   speedtest:    $PROBE_SPEEDTEST_URL
		   image:        $PROBE_IMAGE
		   container:    $PROBE_CONTAINER
		INFO

    if [[ "$pull" -eq 1 ]]; then
        say "docker pull"
        docker pull "$PROBE_IMAGE"
    elif [[ "$build" -eq 1 ]] || ! docker image inspect "$PROBE_IMAGE" >/dev/null 2>&1; then
        say "docker build"
        docker build -t "$PROBE_IMAGE" -f probe/Dockerfile probe/
    else
        echo "   image present, skip build"
    fi

    if docker container inspect "$PROBE_CONTAINER" >/dev/null 2>&1; then
        echo "   removing existing container $PROBE_CONTAINER"
        docker rm -f "$PROBE_CONTAINER" >/dev/null
    fi

    docker run -d \
        --name "$PROBE_CONTAINER" \
        --network=host \
        --restart unless-stopped \
        -e SERVER_URL="$PROBE_SERVER_URL" \
        -e XSPEED_ALLOW_INSECURE_WS="${XSPEED_ALLOW_INSECURE_WS:-1}" \
        -e PROBE_TOKEN="$PROBE_TOKEN" \
        -e PROBE_ALIAS="$PROBE_ALIAS" \
        -e REGION_CODE="$PROBE_REGION" \
        -e SPEEDTEST_URL="$PROBE_SPEEDTEST_URL" \
        -e LOG_LEVEL="$LOG_LEVEL" \
        "$PROBE_IMAGE" >/dev/null

    ok "started $PROBE_CONTAINER"
    echo
    cmd_status
}

cmd_start() {
    require_docker
    if ! docker container inspect "$PROBE_CONTAINER" >/dev/null 2>&1; then
        warn "container $PROBE_CONTAINER does not exist — run './probe.sh create' first"
        return 1
    fi
    if docker ps --filter "name=^${PROBE_CONTAINER}$" --format '{{.Names}}' | grep -q .; then
        echo "$PROBE_CONTAINER is already running"
        return 0
    fi
    say "start $PROBE_CONTAINER"
    docker start "$PROBE_CONTAINER" >/dev/null
    ok "started"
    echo
    cmd_status
}

cmd_stop() {
    require_docker
    if ! docker container inspect "$PROBE_CONTAINER" >/dev/null 2>&1; then
        echo "$PROBE_CONTAINER is not present"
        return 0
    fi
    if ! docker ps --filter "name=^${PROBE_CONTAINER}$" --format '{{.Names}}' | grep -q .; then
        echo "$PROBE_CONTAINER is already stopped (kept on disk; './probe.sh start' revives it)"
        return 0
    fi
    say "stop $PROBE_CONTAINER (container kept on disk; './probe.sh start' revives it)"
    docker stop "$PROBE_CONTAINER"
    ok "stopped"
}

cmd_remove() {
    require_docker
    if ! docker container inspect "$PROBE_CONTAINER" >/dev/null 2>&1; then
        echo "$PROBE_CONTAINER is not present"
        return 0
    fi
    say "remove $PROBE_CONTAINER"
    docker rm -f "$PROBE_CONTAINER"
    ok "removed"
}

cmd_status() {
    require_docker

    say "container"
    if ! docker container inspect "$PROBE_CONTAINER" >/dev/null 2>&1; then
        warn "container $PROBE_CONTAINER does not exist"
        warn "run './probe.sh create' to start it"
        _probe_admin_lookup || true
        return 0
    fi
    docker ps -a --filter "name=^${PROBE_CONTAINER}$" --format \
        "table {{.Names}}\t{{.Status}}\t{{.Image}}\t{{.Ports}}"

    if docker ps --filter "name=^${PROBE_CONTAINER}$" --format '{{.Names}}' | grep -q .; then
        echo
        say "last 20 log lines"
        docker logs --tail 20 "$PROBE_CONTAINER" 2>&1 || true
    else
        warn "container exists but is not running"
    fi

    echo
    _probe_admin_lookup || true
}

_probe_admin_lookup() {
    say "server-side probe lookup"
    if [[ -z "${PROBE_TOKEN:-}" || "${PROBE_TOKEN:-unset}" == "unset" ]]; then
        echo "   PROBE_TOKEN not set — skipping admin lookup"
        return 0
    fi
    local jwt
    if ! jwt="$(curl -sS --max-time 3 -X POST "$ADMIN_API_URL/login" \
            -H 'Content-Type: application/json' \
            --data "{\"email\":\"$ADMIN_EMAIL\",\"password\":\"$ADMIN_PASSWORD\"}" 2>/dev/null \
        | python3 -c 'import json,sys;print(json.load(sys.stdin).get("access_token",""))' 2>/dev/null)" \
        || [[ -z "$jwt" ]]; then
        warn "admin API not reachable at $ADMIN_API_URL — skipped"
        return 0
    fi
    local list_json
    if ! list_json="$(curl -sS --max-time 3 "$ADMIN_API_URL/probes?limit=200" \
            -H "Authorization: Bearer $jwt" 2>/dev/null)"; then
        warn "could not fetch /admin/probes"
        return 0
    fi
    TOKEN="$PROBE_TOKEN" ALIAS="$PROBE_ALIAS" REGION="$PROBE_REGION" \
    python3 - <<'PY' "$list_json" || warn "could not parse /admin/probes response"
import json, os, sys
raw = sys.stdin.read() if not sys.stdin.isatty() else ""
data = json.loads(sys.argv[1]) if sys.argv[1:] else json.loads(raw)
token = os.environ.get("TOKEN", "").strip()
alias = os.environ.get("ALIAS", "").strip()
region = os.environ.get("REGION", "").strip()
items = (data or {}).get("items", [])
if not items:
    print("   server has no registered probes")
    sys.exit(0)
matches = [p for p in items if p.get("token") == token] if token and token != "unset" else []
if not matches:
    matches = [p for p in items
               if (alias and p.get("alias") == alias)
               or (region and p.get("region_code") == region)]
if not matches:
    print("   (no probe in /admin/probes matches this token / alias / region)")
    sys.exit(0)
for p in matches:
    print(f"   id={p['id']} alias={p['alias']!r} region={p['region_code']} "
          f"last_seen={p.get('last_seen_at')} ip={p.get('ip')}")
PY
}

cmd_restart() {
    require_docker
    if ! docker container inspect "$PROBE_CONTAINER" >/dev/null 2>&1; then
        warn "container $PROBE_CONTAINER does not exist — run './probe.sh create' first"
        return 1
    fi
    say "restart $PROBE_CONTAINER"
    docker restart "$PROBE_CONTAINER"
    ok "restarted"
    echo
    cmd_status
}

cmd_logs() {
    require_docker
    docker container inspect "$PROBE_CONTAINER" >/dev/null 2>&1 \
        || fail "container $PROBE_CONTAINER does not exist"
    local n="${1:-50}"
    docker logs --tail "$n" -f "$PROBE_CONTAINER"
}

cmd_help() {
    sed -n '2,15p' "${BASH_SOURCE[0]}"
}

# ---- dispatch ----------------------------------------------------------
cmd="${1:-help}"; shift || true
case "$cmd" in
    create)  cmd_create "$@" ;;
    start)   cmd_start "$@" ;;
    stop)    cmd_stop "$@" ;;
    status)  cmd_status "$@" ;;
    restart) cmd_restart "$@" ;;
    remove|rm) cmd_remove "$@" ;;
    logs)    cmd_logs "$@" ;;
    help|-h|--help) cmd_help ;;
    *) cmd_help; fail "unknown command: $cmd" ;;
esac
