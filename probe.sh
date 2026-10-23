#!/usr/bin/env bash
# probe.sh — manage the CheburSpeed probe container in this folder.
#
#   ./probe.sh create        build image (if missing) and start a fresh container
#   ./probe.sh start         start an already-existing, stopped container
#   ./probe.sh status        container state + last logs + server-side registration
#   ./probe.sh stop          stop the container (kept on disk; revives via `start`)
#   ./probe.sh restart       docker restart (stop + start, no image rebuild)
#   ./probe.sh remove        destroy the container (the old destructive `stop`)
#   ./probe.sh update-all    rebuild the image once and recreate every probe of
#                           this folder (`.env` + sibling `.env.<name>` configs)
#   ./probe.sh logs [N]      tail the last N log lines (default 50), follow
#   ./probe.sh help          this message
#
# Configuration comes from .env (project root). All PROBE_* variables are
# honoured; defaults below apply when a value is missing. Required is
# PROBE_TOKEN — without it the container still starts, but the server
# rejects the WebSocket handshake with HTTP 403.
#
# Flags for `create` / `update-all`:
#   --no-build    skip `docker build` even if the image is missing locally
#   --pull        `docker pull` $PROBE_IMAGE instead of building
#
# Global flag (before the command; `create` accepts it as well):
#   --env-file F  take the config of another probe from F instead of `.env`
#                 (e.g. `./probe.sh --env-file .env.mobile status`). Values
#                 inherited from the environment are dropped first, so a
#                 sibling config can never inherit another probe's token.
#                 `update-all` walks every probe config and ignores it.
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

# ---- global options -----------------------------------------------------
ENV_FILE=".env"
ENV_FILE_EXPLICIT=0
while [[ $# -gt 0 ]]; do
    case "$1" in
        --env-file)
            [[ $# -ge 2 ]] || fail "--env-file requires a path"
            ENV_FILE="$2"; ENV_FILE_EXPLICIT=1; shift 2 ;;
        --env-file=*)
            ENV_FILE="${1#*=}"; ENV_FILE_EXPLICIT=1; shift ;;
        *)
            break ;;
    esac
done

# ---- config -------------------------------------------------------------
# Variables that describe ONE probe. They are cleared before another
# config file is loaded so a sibling config (e.g. `.env.mobile`) never
# inherits the wired probe's token or alias by omission.
PROBE_CONFIG_VARS=(
    PROBE_IMAGE
    PROBE_CONTAINER
    PROBE_ALIAS
    PROBE_REGION
    PROBE_TOKEN
    PROBE_SERVER_URL
    PROBE_SPEEDTEST_URL
    PROBE_NETWORK_INTERFACE
)

_reset_probe_env() {
    local name
    for name in "${PROBE_CONFIG_VARS[@]}"; do
        unset "$name"
    done
}

# Load KEY=VALUE pairs from $1. Without ``force`` the values already
# present in the environment win — the shell-level ``set -a`` exports
# every newly-assigned variable, so values that came in through the
# environment (e.g. when this script is invoked with
# ``PROBE_CONTAINER=... ./probe.sh create`` or after
# ``set -a; . .env.mobile``) keep their precedence over the defaults
# baked into .env. An explicit ``--env-file`` loads with ``force=1``.
_load_env_file() {
    local file="$1" force="${2:-0}"
    [[ -f "$file" ]] || return 0
    local key value
    while IFS='=' read -r key value; do
        # skip comments and blank lines
        [[ -z "$key" || "$key" =~ ^[[:space:]]*# ]] && continue
        # honour already-set vars so a sibling config (e.g. .env.mobile)
        # wins over .env
        if [[ "$force" -eq 1 || -z "${!key+x}" ]]; then
            export "$key=$value"
        fi
    done < <(grep -E '^[A-Za-z_][A-Za-z0-9_]*=' "$file")
}

# ---- defaults (overridable via .env above) -----------------------------
_apply_defaults() {
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
    # Optional outbound network interface. When set the probe pins xray's
    # vless outbound to that interface's IPv4 (DAT-461). The variable is
    # unset by default so the existing single-probe deployment keeps
    # working without any change to .env.
    : "${PROBE_NETWORK_INTERFACE:=}"
}

# Switch to another probe config (`--env-file`): drop everything the
# current config brought in, then let the new file win outright.
_load_config() {
    local file="$1"
    [[ -f "$file" ]] || fail "probe config not found: $file"
    _reset_probe_env
    _load_env_file "$file" 1
    _apply_defaults
}

if [[ "$ENV_FILE_EXPLICIT" -eq 1 ]]; then
    _load_config "$ENV_FILE"
else
    _load_env_file "$ENV_FILE"
    _apply_defaults
fi

# Every probe managed from this folder: `.env` is the wired probe, every
# sibling `.env.<name>` declares one more (`.env.mobile` — DAT-461).
# Compose templates (`.env.prod.example`) and per-machine overrides
# (`.env.local`) are not probe configs and are skipped.
probe_config_files() {
    local f
    [[ -f .env ]] && printf '%s\n' .env
    for f in .env.*; do
        [[ -f "$f" ]] || continue
        case "$f" in
            *.example|*.local|.env.prod) continue ;;
        esac
        printf '%s\n' "$f"
    done
    return 0
}

_probe_check_token() {
    if [[ -z "${PROBE_TOKEN:-}" ]]; then
        warn "PROBE_TOKEN is empty in $ENV_FILE — the probe will start but the server will reject WS (HTTP 403)."
        PROBE_TOKEN="${PROBE_TOKEN:-unset}"
    fi
}

# ``build`` always rebuilds, ``auto`` only when the image is missing
# (``--no-build``), ``pull`` fetches the published image instead.
_probe_build_image() {
    case "$1" in
        pull)
            say "docker pull"
            docker pull "$PROBE_IMAGE"
            ;;
        build)
            say "docker build"
            docker build -t "$PROBE_IMAGE" -f probe/Dockerfile probe/
            ;;
        auto)
            if ! docker image inspect "$PROBE_IMAGE" >/dev/null 2>&1; then
                say "docker build (image $PROBE_IMAGE missing)"
                docker build -t "$PROBE_IMAGE" -f probe/Dockerfile probe/
            else
                echo "   image $PROBE_IMAGE present, skip build"
            fi
            ;;
    esac
}

_probe_print_config() {
    cat <<-INFO
		   alias:        $PROBE_ALIAS
		   region:       $PROBE_REGION
		   server URL:   $PROBE_SERVER_URL
		   speedtest:    $PROBE_SPEEDTEST_URL
		   image:        $PROBE_IMAGE
		   container:    $PROBE_CONTAINER
		   net iface:    ${PROBE_NETWORK_INTERFACE:-<default>}
		INFO
}

_probe_run_container() {
    if docker container inspect "$PROBE_CONTAINER" >/dev/null 2>&1; then
        echo "   removing existing container $PROBE_CONTAINER"
        docker rm -f "$PROBE_CONTAINER" >/dev/null
    fi

    # Forward ``PROBE_NETWORK_INTERFACE`` to the container as
    # ``NETWORK_INTERFACE`` so the probe pins xray's outbound to that
    # NIC's IPv4 (DAT-461). The env is omitted when the variable is
    # empty to keep the legacy behaviour (kernel default route).
    local netif_args=()
    if [[ -n "${PROBE_NETWORK_INTERFACE:-}" ]]; then
        netif_args=(-e NETWORK_INTERFACE="$PROBE_NETWORK_INTERFACE")
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
        "${netif_args[@]}" \
        "$PROBE_IMAGE" >/dev/null
}

# ---- subcommands -------------------------------------------------------
cmd_create() {
    local build=1 pull=0
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --no-build) build=0 ;;
            --pull)     pull=1  ;;
            --env-file)
                [[ $# -ge 2 ]] || fail "--env-file requires a path"
                ENV_FILE="$2"; _load_config "$ENV_FILE"; shift ;;
            --env-file=*)
                ENV_FILE="${1#*=}"; _load_config "$ENV_FILE"; shift ;;
            *) fail "unknown flag for create: $1" ;;
        esac
        shift
    done
    require_docker
    _probe_check_token

    say "create probe"
    _probe_print_config

    if [[ "$pull" -eq 1 ]]; then
        _probe_build_image pull
    elif [[ "$build" -eq 1 ]]; then
        _probe_build_image build
    else
        _probe_build_image auto
    fi

    _probe_run_container
    ok "started $PROBE_CONTAINER"
    echo
    cmd_status
}

# One command for the whole host: build the image once, then recreate the
# container of every probe declared in this folder — `.env` (wired) plus
# each sibling `.env.<name>` (e.g. `.env.mobile` for the USB-tethered
# probe, DAT-461). All probes share the same local image, so a single
# build keeps every container on the same code.
cmd_update_all() {
    local build=1 pull=0
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --no-build) build=0 ;;
            --pull)     pull=1  ;;
            *) fail "unknown flag for update-all: $1" ;;
        esac
        shift
    done
    require_docker

    local -a configs=()
    local cfg
    while IFS= read -r cfg; do
        configs+=("$cfg")
    done < <(probe_config_files)

    [[ "${#configs[@]}" -gt 0 ]] \
        || fail "no probe config in $PROJECT_ROOT — expected .env or a sibling .env.<name>"

    say "update all probes (${#configs[@]}: ${configs[*]})"

    if [[ "$pull" -eq 1 ]]; then
        _probe_build_image pull
    elif [[ "$build" -eq 1 ]]; then
        _probe_build_image build
    else
        _probe_build_image auto
    fi

    local rc=0
    for cfg in "${configs[@]}"; do
        printf '\n'
        if "$SCRIPT_PATH" --env-file "$cfg" create --no-build; then
            ok "probe from $cfg is up"
        else
            warn "probe from $cfg failed to start — see the log above"
            rc=1
        fi
    done

    printf '\n'
    say "all probes"
    docker ps -a --filter "name=xspeed-probe" --format \
        "table {{.Names}}\t{{.Status}}\t{{.Image}}"
    return "$rc"
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
    awk 'NR == 1 { next } /^#/ { sub(/^# ?/, ""); print; next } { exit }' \
        "${BASH_SOURCE[0]}"
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
    update-all) cmd_update_all "$@" ;;
    logs)    cmd_logs "$@" ;;
    help|-h|--help) cmd_help ;;
    *) cmd_help; fail "unknown command: $cmd" ;;
esac
