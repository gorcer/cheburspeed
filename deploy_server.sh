#!/usr/bin/env bash
# deploy_server.sh — push code from local /var/www/cheburspeed to df3.
#
# This is the local-side counterpart to ./update.sh (which lives on the
# remote host). Use it after editing code locally: it rsyncs the
# changed tree over SSH, rebuilds the server + frontend images on df3,
# restarts the docker-compose stack, and runs the database migrations.
#
# Required env (or .env on local):
#   no remote-specific env needed; the script reads credentials from
#   ~/.ssh/config (Host df3) and the existing .env.prod on df3.
#
# Flags:
#   --no-build     reuse existing docker images (skip the rebuild step)
#   --no-migrate   skip the alembic step
#   --nginx-reload only reload nginx (after manually dropping a config
#                  change into /etc/nginx/sites-available/cheburspeed.conf)
#   --init         first-time bootstrap: create the ACME stub site,
#                  issue the cert, and enable the full site
#   --status       only print remote stack + nginx state, do not change
#   --help         this message
#
# Examples:
#   ./deploy_server.sh
#   ./deploy_server.sh --no-build
#   ./deploy_server.sh --nginx-reload
#   ./deploy_server.sh --init

set -euo pipefail

REMOTE_HOST="${REMOTE_HOST:-df3}"
REMOTE_DIR="/var/www/cheburspeed"
LOCAL_DIR="$(cd "$(dirname "$0")" && pwd)"
COMPOSE_FILE="docker-compose.df3.yml"
ENV_FILE=".env.prod"

BUILD=1
MIGRATE=1
RELOAD_NGINX=0
INIT=0
STATUS_ONLY=0

say()  { printf '\033[1;36m== %s ==\033[0m\n' "$*"; }
warn() { printf '\033[1;33mWARN:\033[0m %s\n' "$*" >&2; }
fail() { printf '\033[1;31mFAIL:\033[0m %s\n' "$*" >&2; exit 1; }

while [[ $# -gt 0 ]]; do
    case "$1" in
        --no-build)    BUILD=0 ;;
        --no-migrate)  MIGRATE=0 ;;
        --nginx-reload) RELOAD_NGINX=1 ;;
        --init)        INIT=1 ;;
        --status)      STATUS_ONLY=1 ;;
        --help|-h|--help)
            sed -n '2,30p' "$0"; exit 0 ;;
        *) fail "unknown flag: $1" ;;
    esac
    shift
done

# ---- preflight --------------------------------------------------------
command -v rsync >/dev/null || fail "rsync not in PATH"
command -v ssh   >/dev/null || fail "ssh not in PATH"
[[ -d "$LOCAL_DIR/.git" ]] || fail "not a git repository: $LOCAL_DIR"

say "0. preflight"
BRANCH="$(git -C "$LOCAL_DIR" rev-parse --abbrev-ref HEAD)"
HEAD="$(git -C "$LOCAL_DIR" rev-parse --short HEAD)"
echo "   local dir:  $LOCAL_DIR"
echo "   local HEAD: $HEAD ($BRANCH)"
echo "   remote:     $REMOTE_HOST:$REMOTE_DIR"

ssh -o ConnectTimeout=5 "$REMOTE_HOST" "true" \
    || fail "cannot ssh into $REMOTE_HOST — check ~/.ssh/config"

if [[ "$STATUS_ONLY" -eq 1 ]]; then
    say "remote status"
    ssh "$REMOTE_HOST" "
        echo '--- compose ps ---'
        docker compose -f $REMOTE_DIR/$COMPOSE_FILE --env-file $REMOTE_DIR/$ENV_FILE ps
        echo '--- nginx ---'
        sudo nginx -t 2>&1 | tail -2
        sudo systemctl is-active nginx
        echo '--- certificate ---'
        sudo certbot certificates -d che.dataforest.app 2>&1 | grep -E 'Certificate Name|Expiry Date|Domains'
        echo '--- public HTTPS ---'
        curl -sI -o /dev/null -w 'https://che.dataforest.app/ -> %{http_code}\\n' https://che.dataforest.app/
    "
    exit 0
fi

# ---- 1. rsync ----------------------------------------------------------
say "1. rsync $LOCAL_DIR  →  $REMOTE_HOST:$REMOTE_DIR"

EXCLUDES=(
    "--exclude=.git"
    "--exclude=.venv"
    "--exclude='**/.venv'"
    "--exclude=__pycache__"
    "--exclude='**/__pycache__'"
    "--exclude=node_modules"
    "--exclude='**/node_modules'"
    "--exclude=.mypy_cache"
    "--exclude='**/.mypy_cache'"
    "--exclude=.ruff_cache"
    "--exclude='**/.ruff_cache'"
    "--exclude=.pytest_cache"
    "--exclude='**/.pytest_cache'"
    "--exclude=.svelte-kit"
    "--exclude='**/.svelte-kit'"
    "--exclude=build"
    "--exclude='**/build'"
    "--exclude=dist"
    "--exclude=.env.prod"     # never overwrite secrets on remote
)

# Use --update so destination-mtime wins; --delete-after keeps the
# tree clean (the local repo is the source of truth).
rsync -az --update --delete-after "${EXCLUDES[@]}" \
    -e "ssh -o ConnectTimeout=10" \
    "$LOCAL_DIR/" "$REMOTE_HOST:$REMOTE_DIR/"

# ---- 2. init? ----------------------------------------------------------
if [[ "$INIT" -eq 1 ]]; then
    say "2. first-time bootstrap on $REMOTE_HOST"
    ssh -t "$REMOTE_HOST" "
        set -euo pipefail
        cd $REMOTE_DIR

        sudo mkdir -p /var/www/letsencrypt/.well-known/acme-challenge
        sudo chown -R www-data:www-data /var/www/letsencrypt

        # Tiny ACME stub so the first cert can be issued without the
        # full 443 site referencing missing material.
        cat > /tmp/acme-stub.conf <<'STUB'
server {
    listen 80;
    listen [::]:80;
    server_name che.dataforest.app;
    location ^~ /.well-known/acme-challenge/ {
        root /var/www/letsencrypt;
        default_type text/plain;
        allow all;
    }
    location / { return 301 https://\\\$host\\\$request_uri; }
}
STUB
        sudo cp $REMOTE_DIR/nginx/cheburspeed-upstreams.conf /etc/nginx/conf.d/cheburspeed-upstreams.conf
        sudo cp /tmp/acme-stub.conf /etc/nginx/sites-available/cheburspeed.acme.conf
        sudo ln -sf /etc/nginx/sites-available/cheburspeed.acme.conf /etc/nginx/sites-enabled/cheburspeed.acme.conf
        sudo rm -f /etc/nginx/sites-enabled/cheburspeed.conf
        sudo nginx -t && sudo systemctl reload nginx

        sudo certbot certonly --webroot -w /var/www/letsencrypt \\
            --email ops@gorcer.ru --agree-tos --no-eff-email \\
            -d che.dataforest.app

        sudo rm -f /etc/nginx/sites-enabled/cheburspeed.acme.conf
        sudo cp $REMOTE_DIR/nginx/cheburspeed.conf /etc/nginx/sites-available/cheburspeed.conf
        sudo ln -sf /etc/nginx/sites-available/cheburspeed.conf /etc/nginx/sites-enabled/cheburspeed.conf
        sudo nginx -t && sudo systemctl reload nginx
    "
    BUILD=1
    MIGRATE=1
fi

# ---- 3. nginx reload-only path ----------------------------------------
if [[ "$RELOAD_NGINX" -eq 1 && "$BUILD" -eq 0 && "$MIGRATE" -eq 0 && "$INIT" -eq 0 ]]; then
    say "3. sync nginx config + reload only"
    ssh "$REMOTE_HOST" "
        set -euo pipefail
        sudo cp $REMOTE_DIR/nginx/cheburspeed-upstreams.conf /etc/nginx/conf.d/cheburspeed-upstreams.conf
        sudo cp $REMOTE_DIR/nginx/cheburspeed.conf          /etc/nginx/sites-available/cheburspeed.conf
        sudo ln -sf /etc/nginx/sites-available/cheburspeed.conf /etc/nginx/sites-enabled/cheburspeed.conf
        sudo nginx -t && sudo systemctl reload nginx
        echo 'nginx reloaded'
    "
    exit 0
fi

# ---- 4. build + restart stack -----------------------------------------
say "2. build + restart compose stack on $REMOTE_HOST"
ssh "$REMOTE_HOST" "
    set -euo pipefail
    cd $REMOTE_DIR

    if ! [[ -f $ENV_FILE ]]; then
        echo 'FATAL: missing $ENV_FILE on remote — copy it from the local box first' >&2
        exit 1
    fi

    if [[ '$BUILD' == '1' ]]; then
        docker compose -f $COMPOSE_FILE --env-file $ENV_FILE up -d --build
    else
        docker compose -f $COMPOSE_FILE --env-file $ENV_FILE up -d
    fi
    docker compose -f $COMPOSE_FILE --env-file $ENV_FILE ps
"

# ---- 5. migrations ----------------------------------------------------
if [[ "$MIGRATE" -eq 1 ]]; then
    say "3. alembic upgrade head"
    DB_URL=$(grep '^DATABASE_URL=' "$LOCAL_DIR/$ENV_FILE" 2>/dev/null | cut -d= -f2- || true)
    [[ -z "$DB_URL" ]] && DB_URL=$(grep '^DATABASE_URL=' "$LOCAL_DIR/.env" 2>/dev/null | cut -d= -f2- || true)
    if [[ -z "$DB_URL" ]]; then
        warn "DATABASE_URL not present in local .env / .env.prod — migrations will rely on .env.prod on remote"
    else
        echo "   using DATABASE_URL from local env (postgres creds from remote .env.prod override at runtime)"
    fi

    ssh "$REMOTE_HOST" "
        set -euo pipefail
        cd $REMOTE_DIR
        set -a; . ./\$ENV_FILE; set +a
        docker run --rm --network=host \\
            --env-file $ENV_FILE \\
            --env DATABASE_URL=\"\$DATABASE_URL\" \\
            -v $REMOTE_DIR/server/alembic.ini:/app/alembic.ini \\
            -v $REMOTE_DIR/server/alembic:/app/alembic \\
            cheburspeed-server sh -c '/app/.venv/bin/alembic upgrade head'
    "
else
    say "3. migrations skipped (--no-migrate)"
fi

# ---- 6. health check ---------------------------------------------------
say "4. health check"
curl -fsS -o /dev/null -w "   https://che.dataforest.app/            -> %{http_code}\n" \
    https://che.dataforest.app/ || true
JWT=$(curl -sS https://che.dataforest.app/api/v1/admin/login \
    -X POST -H 'Content-Type: application/json' \
    -d '{"email":"admin@xspeed.local","password":"admin1234"}' \
    | python3 -c 'import json,sys;print(json.load(sys.stdin).get("access_token",""))' 2>/dev/null || true)
if [[ -n "$JWT" ]]; then
    PROBE=$(curl -sS -H "Authorization: Bearer $JWT" \
        https://che.dataforest.app/api/v1/admin/probes \
        | python3 -c 'import json,sys
d=json.load(sys.stdin)
for p in d.get("items",[]):
    print(f"   probe id={p[\"id\"]} alias={p[\"alias\"]!r} region={p[\"region_code\"]} last_seen={p[\"last_seen_at\"]} ip={p[\"ip\"]}")
' 2>/dev/null || true)
    [[ -n "$PROBE" ]] && printf '%s\n' "$PROBE"
fi

say "done"
