#!/usr/bin/env bash
# CheburSpeed update script.
#
#   1. git pull --ff-only origin dev (with auto-stash of local edits)
#   2. docker compose up -d --build  (server, frontend, nginx)
#   3. alembic upgrade head          (idempotent)
#   4. probe image rebuild + container restart (if PROBE_* env vars set)
#   5. health check + status summary
#
# Usage:
#   ./update.sh
#   PROBE_SKIP=1 ./update.sh            # skip probe step
#   PROBE_REGION=SP ./update.sh         # override region
#
# Safe to re-run. Idempotent.

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_ROOT"

COMPOSE=(docker compose)
PROBE_IMAGE="xspeed-probe:dev"
PROBE_SKIP="${PROBE_SKIP:-0}"

# ---- pretty output -------------------------------------------------------
say()  { printf '\033[1;36m== %s ==\033[0m\n' "$*"; }
warn() { printf '\033[1;33mWARN:\033[0m %s\n' "$*" >&2; }
fail() { printf '\033[1;31mFAIL:\033[0m %s\n' "$*" >&2; exit 1; }

# ---- 0. preflight --------------------------------------------------------
say "0. preflight"
git rev-parse --git-dir >/dev/null 2>&1 || fail "not a git repository: $PROJECT_ROOT"
BRANCH="$(git rev-parse --abbrev-ref HEAD)"
[[ "$BRANCH" == "agent/devops-senior/69e2a6a4859b" || "$BRANCH" == "dev" ]] \
    || warn "running on branch '$BRANCH' (expected dev or agent/devops-senior/69e2a6a4859b)"
echo "   branch: $BRANCH"
echo "   remote: $(git remote get-url origin 2>/dev/null || echo none)"

command -v docker >/dev/null || fail "docker not in PATH"

# ---- 1. git pull ---------------------------------------------------------
say "1. git pull --ff-only origin dev"
STASHED=0
if ! git diff --quiet HEAD 2>/dev/null || ! git diff --cached --quiet 2>/dev/null; then
    warn "local changes detected — will stash before pull"
    git stash push -m "update.sh auto-stash @ $(date -Iseconds)"
    STASHED=1
fi
if ! git pull --ff-only origin dev; then
    warn "fast-forward pull failed (likely non-ff). Resolve manually with 'git pull' or 'git rebase origin/dev'."
    [[ "$STASHED" -eq 1 ]] && git stash pop
    exit 1
fi
if [[ "$STASHED" -eq 1 ]]; then
    if ! git stash pop; then
        warn "stash pop failed — resolve manually with 'git stash list' / 'git stash pop'"
    fi
fi
NEW_HEAD="$(git rev-parse --short HEAD)"
echo "   HEAD now at $NEW_HEAD: $(git log -1 --pretty=%s)"

# ---- 2. rebuild + restart stack ------------------------------------------
say "2. docker compose up -d --build"

# Resolve the compose project name this stack will use so we can spot
# stale containers left over from a previous deployment under a different
# `-p` flag or created without compose labels at all.
COMPOSE_PROJECT="$("${COMPOSE[@]}" config --format json 2>/dev/null | python3 -c "
import json, sys
print(json.load(sys.stdin).get('name', '') or '')
" 2>/dev/null || true)"
[[ -z "$COMPOSE_PROJECT" ]] && COMPOSE_PROJECT="xspeed"
echo "   compose project: $COMPOSE_PROJECT"

# Pre-clean: drop any pre-existing container the stack would otherwise
# fail to create because its `container_name` collides with a stale
# container from another (or no) compose project.
while IFS= read -r CNAME; do
    [[ -z "$CNAME" ]] && continue
    if ! docker ps -a --format '{{.Names}}' | grep -Fxq "$CNAME"; then
        continue
    fi
    OWNING="$(docker inspect --format '{{ index .Config.Labels "com.docker.compose.project" }}' "$CNAME" 2>/dev/null || true)"
    if [[ "$OWNING" == "$COMPOSE_PROJECT" ]]; then
        continue
    fi
    warn "removing stale container $CNAME (compose project: '${OWNING:-<none>}', expected '$COMPOSE_PROJECT')"
    docker rm -f "$CNAME" >/dev/null 2>&1 || warn "failed to remove $CNAME — 'docker compose up' may fail"
done < <("${COMPOSE[@]}" config --format json 2>/dev/null | python3 -c "
import json, sys
d = json.load(sys.stdin)
for svc in (d.get('services') or {}).values():
    n = svc.get('container_name')
    if n:
        print(n)
" 2>/dev/null)

"${COMPOSE[@]}" up -d --build

# wait for healthchecks to settle
echo "   waiting for healthchecks…"
ATTEMPTS=0
until "${COMPOSE[@]}" ps --format json 2>/dev/null | python3 -c "
import json, sys
ok = True
for line in sys.stdin:
    line = line.strip()
    if not line: continue
    s = json.loads(line)
    h = (s.get('Health') or '').lower()
    if h and h != 'healthy':
        ok = False; break
sys.exit(0 if ok else 1)
" 2>/dev/null; do
    ATTEMPTS=$((ATTEMPTS+1))
    [[ $ATTEMPTS -ge 30 ]] && { warn "healthcheck timeout (30s) — continuing anyway"; break; }
    sleep 1
done

# ---- 3. migrations -------------------------------------------------------
say "3. alembic upgrade head"
"${COMPOSE[@]}" exec -T server alembic upgrade head

# ---- 4. probe (optional) -------------------------------------------------
if [[ "$PROBE_SKIP" == "1" ]]; then
    say "4. probe step skipped (PROBE_SKIP=1)"
elif [[ -f .env ]]; then
    # shellcheck disable=SC1091
    set -a; . ./.env; set +a
    if [[ -z "${PROBE_TOKEN:-}" ]]; then
        warn "PROBE_TOKEN not set in .env — skipping probe step"
    else
        say "4. probe (region=${PROBE_REGION:-PK})"
        PROBE_REGION="${PROBE_REGION:-PK}"
        PROBE_ALIAS="${PROBE_ALIAS:-$PROBE_REGION}"
        PROBE_SERVER_URL="${PROBE_SERVER_URL:-ws://localhost:8001/ws}"
        PROBE_SPEEDTEST_URL="${PROBE_SPEEDTEST_URL:-https://speed.cloudflare.com/__down?bytes=10000000}"
        CONTAINER_NAME="xspeed-probe-${PROBE_REGION,,}"

        docker build -t "$PROBE_IMAGE" -f probe/Dockerfile probe/
        docker rm -f "$CONTAINER_NAME" 2>/dev/null || true
        # Forward ``PROBE_NETWORK_INTERFACE`` (DAT-461) when set so the
        # probe pins xray's vless outbound to the named interface's
        # IPv4. The variable is omitted entirely when empty to keep
        # the legacy behaviour (kernel default route).
        netif_args=()
        if [[ -n "${PROBE_NETWORK_INTERFACE:-}" ]]; then
            netif_args=(-e NETWORK_INTERFACE="$PROBE_NETWORK_INTERFACE")
        fi
        docker run -d \
            --name "$CONTAINER_NAME" \
            --network=host \
            --restart unless-stopped \
            -e SERVER_URL="$PROBE_SERVER_URL" \
            -e PROBE_TOKEN="$PROBE_TOKEN" \
            -e PROBE_ALIAS="$PROBE_ALIAS" \
            -e REGION_CODE="$PROBE_REGION" \
            -e SPEEDTEST_URL="$PROBE_SPEEDTEST_URL" \
            -e LOG_LEVEL="${LOG_LEVEL:-INFO}" \
            "${netif_args[@]}" \
            "$PROBE_IMAGE"
        echo "   started $CONTAINER_NAME"
    fi
else
    warn ".env not found — skipping probe step"
fi

# ---- 5. health check -----------------------------------------------------
say "5. health check"
curl -fsS -o /dev/null -w "   http://localhost:8085/        -> %{http_code}\n" http://localhost:8085/   || true
curl -fsS -o /dev/null -w "   http://localhost:8001/healthz -> %{http_code}\n" http://localhost:8001/healthz || true

echo
echo "--- stack ---"
"${COMPOSE[@]}" ps --format "table {{.Names}}\t{{.Status}}\t{{.Image}}"
echo
echo "--- probe ---"
docker ps --filter "name=xspeed-probe" --format "table {{.Names}}\t{{.Status}}\t{{.Image}}" 2>/dev/null || true

say "done"