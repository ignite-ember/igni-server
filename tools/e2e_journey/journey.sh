#!/usr/bin/env bash
#
# The free base's cross-repo journey: igni server, its portal, the igni
# CLI and a registered service with an extension, against a real
# identity provider and a stub model.
#
#   tools/e2e_journey/journey.sh up     Postgres, Redis, Dex; migrate; stub model, API, portal, Team Notes
#   tools/e2e_journey/journey.sh run    up (always fresh), then the journey; results in $RUN/results.{md,json}
#   tools/e2e_journey/journey.sh down   stop everything this script started; containers removed with -v
#   tools/e2e_journey/journey.sh all    up, run, down — what CI runs
#   tools/e2e_journey/journey.sh service  which service the steps would use: starter, fixture or none
#
# Checkouts it expects beside this one (override with the variables):
#   IGNI_CLI      ../igni                     the CLI, with `uv sync` run in it
#   IGNI_SDK      ../igni-sdk                 igni's SDKs, built into wheels
#
# And in portal/: `npm ci`, then `npx playwright install chromium` (what CI's
# "Install the portal and Chromium" step does).
#   IGNI_STARTER  ../igni__extension-starter  optional: the service steps use
#                                             its Team Notes when it is there
#
# The service steps (7a-7d, and 8d's erasure call) need a registered
# service that ships an extension. JOURNEY_SERVICE picks it:
#   starter   the extension starter's Team Notes (the default when it is checked out)
#   fixture   service_fixture/ here, built with igni-service from the igni
#             checkout (the default otherwise — CI, where the starter has no remote)
#   none      no service; the service steps skip (NO_SERVICE=1 says the same)
#
# Sign-in is real: Dex runs as the deployment's OIDC provider and the
# browser steps go portal -> Dex's login page -> callback, in Chromium.
# The password and one-time-code sign-ins are steps of their own; their
# codes are read from the API log (LOG_SIGN_IN_CODES, development only).
#
# Ports (override by env): Postgres 55501, Redis 56501, API 17801, portal
# 5201, Dex 55561, stub model 18101, the service 18201. Chosen to stay off
# the default 7777/3000 stack and the ports the test suite uses.
#
# Nothing is written into a checkout except $RUN (default
# tools/e2e_journey/run, ignored): the server's .env is a copy of
# dist.env there, the CLI's HOME is $RUN/work/home, the service's bundle
# (and the fixture's virtualenv) are built there.
set -euo pipefail

E2E="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVER="$(cd "$E2E/../.." && pwd)"
RUN="${E2E_RUN_DIR:-$E2E/run}"
LOGS="$RUN/logs"
CLI="${IGNI_CLI:-$SERVER/../igni}"
SDK="${IGNI_SDK:-$SERVER/../igni-sdk}"
STARTER="${IGNI_STARTER:-$SERVER/../igni__extension-starter}"

PG_PORT="${PG_PORT:-55501}"
REDIS_PORT="${REDIS_PORT:-56501}"
API_PORT="${API_PORT:-17801}"
PORTAL_PORT="${PORTAL_PORT:-5201}"
DEX_PORT="${DEX_PORT:-55561}"
STUB_PORT="${STUB_PORT:-18101}"
SVC_PORT="${SVC_PORT:-18201}"

NAME="${JOURNEY_NAME:-igni-journey}"
PG="$NAME-pg"
REDIS="$NAME-redis"
DEX="$NAME-dex"
PG_IMAGE=postgres:17.6-alpine
REDIS_IMAGE=redis:7.4-alpine
DEX_IMAGE=ghcr.io/dexidp/dex:v2.45.0

# Throwaway credentials for containers that live for one run.
PG_PASSWORD=e2e-only-password
SCIM_TOKEN=e2e-scim-token-0123456789abcdef
OIDC_CLIENT_ID=igni-e2e
OIDC_CLIENT_SECRET=e2e-dex-client-secret
SSO_LABEL="Acme SSO"
FIXTURE_NAME="Journey service"

SPY="$SERVER/.venv/bin/python"
API="http://localhost:$API_PORT"
PORTAL="http://localhost:$PORTAL_PORT"
ISSUER="http://localhost:$DEX_PORT/dex"

say() { printf '\033[36m→ %s\033[0m\n' "$*"; }
die() {
  printf '\033[31m✗ %s\033[0m\n' "$*" >&2
  exit 1
}
wait_http() {
  for _ in $(seq 1 "${2:-60}"); do
    curl -fsS -o /dev/null "$1" 2>/dev/null && return 0
    sleep 1
  done
  die "not answering: $1"
}
start_bg() {
  local name=$1
  shift
  nohup "$@" >"$LOGS/$name.log" 2>&1 &
  echo $! >"$RUN/$name.pid"
}
FIXTURE="$E2E/service_fixture"

# starter, fixture or none: see the top of this file.
service_kind() {
  local kind="${JOURNEY_SERVICE:-}"
  [[ "${NO_SERVICE:-}" == 1 ]] && kind=none
  if [[ -z "$kind" ]]; then
    if [[ -d "$STARTER" ]]; then kind=starter; else kind=fixture; fi
  fi
  case "$kind" in
  starter) [[ -d "$STARTER" ]] || die "JOURNEY_SERVICE=starter, and no extension starter at $STARTER" ;;
  fixture | none) ;;
  *) die "JOURNEY_SERVICE must be starter, fixture or none, not $kind" ;;
  esac
  echo "$kind"
}

# igni's service and extension SDKs, as wheels built from the igni-sdk
# checkout: what both services' bundles install, and what the fixture runs on.
sdk_wheels() {
  rm -rf "$RUN/sdk-wheels"
  [[ -d "$SDK/packages/igni-service" ]] || die "no igni-sdk checkout at $SDK (IGNI_SDK)"
  (cd "$SDK" && uv build -q --wheel --out-dir "$RUN/sdk-wheels" packages/igni-service &&
    uv build -q --wheel --out-dir "$RUN/sdk-wheels" packages/igni-extension)
}

# Waits for a service to upload its extension, or say it was refused.
extension_uploaded() {
  for _ in $(seq 1 60); do
    grep -q "uploaded extension\|refused extension" "$1" 2>/dev/null && break
    sleep 1
  done
  grep -E "uploaded extension|refused extension" "$1" | tail -1 ||
    say "the service did not upload its extension (see $1)"
}

# A domain per run: the organisation claims it and Dex knows its people.
# Written for Dex here and handed to the journey.
dex_config() {
  local domain
  domain="acme-$(od -An -N3 -tx1 /dev/urandom | tr -d ' \n').com"
  echo "$domain" >"$RUN/domain"
  "$SPY" -m tools.e2e_journey.dex_config --domain "$domain" --issuer "$ISSUER" \
    --client-id "$OIDC_CLIENT_ID" --client-secret "$OIDC_CLIENT_SECRET" \
    --redirect-uri "$API/v1/auth/oidc/callback" --out "$RUN/dex/config.yaml"
}

dex_ready() {
  for _ in $(seq 1 60); do
    curl -fsS "$ISSUER/.well-known/openid-configuration" 2>/dev/null | grep -q "\"issuer\": *\"$ISSUER\"" && return 0
    sleep 1
  done
  docker logs --tail 40 "$DEX" >&2 || true
  die "Dex never served its discovery document at $ISSUER"
}

server_env() {
  cp "$SERVER/dist.env" "$RUN/server/.env"
  local key
  key="$(python3 -c 'import base64, os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())')"
  "$SPY" -m tools.e2e_journey.server_env "$RUN/server/.env" \
    APP_ENV=dev \
    POSTGRES_HOST=127.0.0.1 POSTGRES_PORT="$PG_PORT" POSTGRES_USER=igni POSTGRES_DB=igni POSTGRES_PASSWORD="$PG_PASSWORD" \
    REDIS_HOST=127.0.0.1 REDIS_PORT="$REDIS_PORT" SECRET_ENCRYPTION_KEY="$key" \
    PUBLIC_BASE_URL="$API" PORTAL_BASE_URL="$PORTAL" OAUTH_REDIRECT_BASE_URL="$API" OAUTH_FRONTEND_BASE_URL="$PORTAL" \
    LOG_SIGN_IN_CODES=true SCIM_TOKEN="$SCIM_TOKEN" OAUTH_PROVIDERS='[]' EMAIL_ENABLED=false \
    OIDC_ISSUER="$ISSUER" OIDC_CLIENT_ID="$OIDC_CLIENT_ID" OIDC_CLIENT_SECRET="$OIDC_CLIENT_SECRET" \
    OIDC_DISPLAY_NAME="$SSO_LABEL" \
    INFERENCE_BACKENDS="[{\"id\":\"e2e-stub\",\"base_url\":\"http://127.0.0.1:$STUB_PORT/v1\",\"model\":\"e2e-stub-model\",\"max_concurrent\":8,\"pools\":[\"chat\"]}]"
}

team_notes() {
  say "Team Notes (extension starter) :$SVC_PORT — bundle built into $RUN with the CLI checkout's SDK wheels"
  if [[ ! -f "$RUN/teamnotes-bundle.tar.gz" ]]; then
    sdk_wheels
    (cd "$STARTER" && UV_NO_SOURCES=1 .venv/bin/igni-service-bundle --extension extension \
      --find-links "$RUN/sdk-wheels" --out "$RUN/teamnotes-bundle.tar.gz" >"$LOGS/bundle.log" 2>&1) ||
      {
        tail "$LOGS/bundle.log"
        die "the Team Notes bundle did not build"
      }
  fi
  rm -f "$RUN/teamnotes/data/"*.db
  # A Fernet key made for the run, like the server's.
  local key
  key="$(python3 -c 'import base64, os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())')"
  (cd "$RUN/teamnotes" &&
    IGNI_SERVER_URL="$API" PUBLIC_BASE_URL="http://teamnotes.localhost:$SVC_PORT" SERVICE_NAME="Team Notes" \
      IGNI_KEY_ENCRYPTION_KEY="$key" \
      IGNI_KEY_DATABASE_URL="sqlite+aiosqlite:///$RUN/teamnotes/data/igni.db" \
      TEAMNOTES_DATABASE_URL="sqlite+aiosqlite:///$RUN/teamnotes/data/teamnotes.db" \
      IGNI_EXTENSION_BUNDLE="$RUN/teamnotes-bundle.tar.gz" \
      start_bg teamnotes "$STARTER/.venv/bin/python" -m uvicorn --factory teamnotes_service.main:create_app \
      --host 127.0.0.1 --port "$SVC_PORT")
  extension_uploaded "$LOGS/teamnotes.log"
}

# The journey's own service (service_fixture/): igni-service and its
# extension, in a virtualenv of its own under $RUN. Same steps as Team
# Notes — register, ship the extension, answer export and erasure.
fixture_service() {
  say "the journey's service fixture :$SVC_PORT — igni-service from the CLI checkout, in $RUN/fixture"
  local venv="$RUN/fixture/.venv"
  if [[ ! -x "$venv/bin/igni-service-bundle" || ! -f "$RUN/fixture-bundle.tar.gz" ]]; then
    sdk_wheels
    rm -rf "$venv" "$RUN/fixture-bundle.tar.gz"
    uv venv -q "$venv"
    # pip: the bundle downloads wheels with it, and a uv-made venv has none.
    uv pip install -q --python "$venv/bin/python" --find-links "$RUN/sdk-wheels" \
      igni-service uvicorn aiosqlite pip >"$LOGS/fixture-install.log" 2>&1 ||
      {
        tail "$LOGS/fixture-install.log"
        die "the service fixture's environment did not install"
      }
  fi
  if [[ ! -f "$RUN/fixture-bundle.tar.gz" ]]; then
    "$venv/bin/igni-service-bundle" --extension "$FIXTURE/extension" --find-links "$RUN/sdk-wheels" \
      --out "$RUN/fixture-bundle.tar.gz" >"$LOGS/bundle.log" 2>&1 ||
      {
        tail "$LOGS/bundle.log"
        die "the service fixture's bundle did not build"
      }
  fi
  rm -f "$RUN/fixture/"*.db
  local key
  key="$(python3 -c 'import base64, os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())')"
  IGNI_SERVER_URL="$API" PUBLIC_BASE_URL="http://127.0.0.1:$SVC_PORT" SERVICE_NAME="$FIXTURE_NAME" \
    IGNI_KEY_ENCRYPTION_KEY="$key" IGNI_KEY_DATABASE_URL="sqlite+aiosqlite:///$RUN/fixture/igni.db" \
    IGNI_EXTENSION_BUNDLE="$RUN/fixture-bundle.tar.gz" \
    start_bg service "$venv/bin/python" -m uvicorn --app-dir "$FIXTURE" --factory service:create_app \
    --host 127.0.0.1 --port "$SVC_PORT"
  extension_uploaded "$LOGS/service.log"
}

up() {
  local kind
  kind="$(service_kind)" || exit 1
  [[ -x "$SPY" ]] || die "no server venv at $SERVER/.venv — run \`uv sync\` there"
  [[ -x "$CLI/.venv/bin/igni" ]] || die "no igni CLI at $CLI (IGNI_CLI) with \`uv sync\` run in it"
  [[ -d "$SERVER/portal/node_modules/@playwright/test" ]] || die "portal dependencies missing — \`npm ci\` in portal/"
  node "$E2E/chromium_ready.mjs" "$SERVER/portal" || die "the browser steps need Chromium"
  down >/dev/null 2>&1 || true
  rm -rf "$LOGS" "$RUN/work" "$RUN/shots" "$RUN/results.json" "$RUN/results.md"
  mkdir -p "$LOGS" "$RUN/server" "$RUN/teamnotes/data" "$RUN/work" "$RUN/dex" "$RUN/shots"

  say "Postgres :$PG_PORT, Redis :$REDIS_PORT, Dex :$DEX_PORT"
  docker run -d --name "$PG" -p "127.0.0.1:$PG_PORT:5432" -e POSTGRES_USER=igni \
    -e POSTGRES_PASSWORD="$PG_PASSWORD" -e POSTGRES_DB=igni "$PG_IMAGE" >/dev/null
  docker run -d --name "$REDIS" -p "127.0.0.1:$REDIS_PORT:6379" "$REDIS_IMAGE" >/dev/null
  (cd "$SERVER" && dex_config)
  # Readable by Dex's own user inside the container.
  chmod 0755 "$RUN/dex" && chmod 0644 "$RUN/dex/config.yaml"
  docker run -d --name "$DEX" -p "127.0.0.1:$DEX_PORT:5556" -v "$RUN/dex:/etc/dex/e2e:ro" \
    "$DEX_IMAGE" dex serve /etc/dex/e2e/config.yaml >/dev/null
  # Probe only once initialisation is complete; see tools/restore-drill.sh.
  for _ in $(seq 1 90); do
    docker logs "$PG" 2>&1 | grep -q 'init process complete' && break
    sleep 1
  done
  for _ in $(seq 1 30); do
    docker exec "$PG" pg_isready -U igni -d igni >/dev/null 2>&1 && break
    sleep 1
  done
  dex_ready

  say "server .env from dist.env (README: cp dist.env .env, then edit)"
  (cd "$SERVER" && server_env)

  say "migrate: the shared plane, then every organisation's database"
  (cd "$RUN/server" && PYTHONPATH="$SERVER" "$SPY" -m alembic -c "$SERVER/alembic.ini" upgrade head >"$LOGS/migrate.log" 2>&1 &&
    PYTHONPATH="$SERVER" "$SPY" -m app.db.sql.tenant_migrations >>"$LOGS/migrate.log" 2>&1) ||
    {
      tail -20 "$LOGS/migrate.log"
      die "migrations did not apply"
    }

  say "stub OpenAI-compatible model :$STUB_PORT"
  : >"$LOGS/stub.jsonl"
  STUB_LOG="$LOGS/stub.jsonl" start_bg stub python3 "$E2E/stub_openai.py" --port "$STUB_PORT"
  wait_http "http://127.0.0.1:$STUB_PORT/v1/models" 20

  say "API :$API_PORT"
  (cd "$RUN/server" && PYTHONPATH="$SERVER" start_bg api "$SPY" -m uvicorn app.main:app --host 127.0.0.1 --port "$API_PORT")
  wait_http "http://127.0.0.1:$API_PORT/ready" 90

  say "portal (vite dev) :$PORTAL_PORT -> API"
  (cd "$SERVER/portal" && IGNI_API_URL="http://127.0.0.1:$API_PORT" start_bg portal npx vite --port "$PORTAL_PORT" --strictPort --host 127.0.0.1)
  wait_http "http://127.0.0.1:$PORTAL_PORT/" 60

  case "$kind" in
  starter) team_notes ;;
  fixture) fixture_service ;;
  none) say "no service (JOURNEY_SERVICE=none or NO_SERVICE=1): the service steps skip" ;;
  esac
  say "up"
}

run() {
  # Always on a fresh stack: the journey's first sign-up becomes the
  # deployment's first account (super admin), which only an empty
  # database allows.
  up
  local service_args=()
  case "$(service_kind)" in
  starter) service_args=(--service-log "$LOGS/teamnotes.log" --service-name "Team Notes" --extension-id example.teamnotes) ;;
  fixture) service_args=(--service-log "$LOGS/service.log" --service-name "$FIXTURE_NAME" --extension-id journey.fixture) ;;
  none) service_args=(--service-log "") ;;
  esac
  (cd "$SERVER" && "$SPY" -m tools.e2e_journey.journey \
    --api "$API" --portal "$PORTAL" --portal-dir "$SERVER/portal" --stub "http://127.0.0.1:$STUB_PORT" \
    --domain "$(cat "$RUN/domain")" --sso-label "$SSO_LABEL" --scim-token "$SCIM_TOKEN" \
    --api-log "$LOGS/api.log" --stub-log "$LOGS/stub.jsonl" "${service_args[@]}" \
    --igni-python "$CLI/.venv/bin/python" --igni-bin "$CLI/.venv/bin/igni" --work "$RUN/work" --out "$RUN")
}

down() {
  for n in teamnotes service portal api stub; do
    if [[ -f "$RUN/$n.pid" ]]; then
      pid="$(cat "$RUN/$n.pid")"
      pkill -P "$pid" 2>/dev/null || true
      kill "$pid" 2>/dev/null || true
      rm -f "$RUN/$n.pid"
    fi
  done
  docker rm -f -v "$PG" "$REDIS" "$DEX" >/dev/null 2>&1 || true
  say "down"
}

case "${1:-all}" in
up) up ;;
run) run ;;
down) down ;;
service) service_kind ;;
all)
  rc=0
  run || rc=$?
  down
  exit $rc
  ;;
*) die "usage: $0 up|run|down|all|service" ;;
esac
