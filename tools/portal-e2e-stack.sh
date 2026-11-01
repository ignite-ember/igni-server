#!/usr/bin/env bash
#
# Bring up a throwaway API on 127.0.0.1:7777 (E2E_API_PORT to change it) for the portal's functional
# E2E specs, which talk to a real API through the portal's dev proxy.
#
#   tools/portal-e2e-stack.sh up     # build, migrate, start; waits until ready
#   tools/portal-e2e-stack.sh down   # remove everything it started
#
# The API runs as a development deployment with PORTAL_AUTH_BYPASS, which
# signs the browser in as the bypass identity and creates its organisation
# on first use. That is only acceptable because everything here is created
# for the run and removed after it; the startup checks refuse the same
# setting anywhere but APP_ENV=dev.
#
# The image is the one the product ships (containers/be/Dockerfile), and it
# migrates the way the chart's migrate job does: the shared plane, then
# every organisation's database.
set -euo pipefail

NET=igni-e2e
PG=e2e-pg
REDIS=e2e-redis
API=e2e-api
IMAGE=igni-api:e2e
PGIMAGE=postgres:17.6-alpine
PASSWORD=e2e-only-password
PORT="${E2E_API_PORT:-7777}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

fail() { printf '\n\033[31m✗ %s\033[0m\n' "$*" >&2; exit 1; }

down() {
  docker rm -f "$API" "$REDIS" "$PG" >/dev/null 2>&1 || true
  docker network rm "$NET" >/dev/null 2>&1 || true
}

up() {
  down
  # A key made for this run: a fixed one in a public file is one somebody
  # copies into a real deployment.
  local key
  key="$(python3 -c 'import base64, os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())')"

  docker network create "$NET" >/dev/null
  docker build -q -f "${ROOT}/containers/be/Dockerfile" -t "$IMAGE" "$ROOT" >/dev/null

  docker run -d --name "$PG" --network "$NET" \
    -e POSTGRES_USER=igni -e POSTGRES_PASSWORD="$PASSWORD" -e POSTGRES_DB=igni "$PGIMAGE" >/dev/null
  docker run -d --name "$REDIS" --network "$NET" redis:7.4-alpine >/dev/null

  # Probe only once initialisation is complete; see tools/restore-drill.sh.
  for _ in $(seq 1 180); do
    docker logs "$PG" 2>&1 | grep -q 'PostgreSQL init process complete' && break
    sleep 1
  done
  for _ in $(seq 1 60); do
    docker exec "$PG" pg_isready -U igni -d igni >/dev/null 2>&1 && break
    sleep 1
  done
  docker exec "$PG" pg_isready -U igni -d igni >/dev/null 2>&1 || {
    docker logs --tail 60 "$PG" >&2 || true
    fail "Postgres never became ready"
  }

  local env=(
    -e APP_ENV=dev -e PORTAL_AUTH_BYPASS=1
    -e POSTGRES_HOST="$PG" -e POSTGRES_USER=igni -e POSTGRES_PASSWORD="$PASSWORD" -e POSTGRES_DB=igni
    -e REDIS_HOST="$REDIS" -e SECRET_ENCRYPTION_KEY="$key"
    -e PUBLIC_BASE_URL=http://localhost:7777
  )
  docker run --rm --network "$NET" "${env[@]}" "$IMAGE" alembic upgrade head >/dev/null ||
    fail "the shared migrations did not apply"
  docker run --rm --network "$NET" "${env[@]}" "$IMAGE" python -m app.db.sql.tenant_migrations >/dev/null ||
    fail "the organisation migrations did not apply"

  docker run -d --name "$API" --network "$NET" -p "127.0.0.1:${PORT}:7777" "${env[@]}" "$IMAGE" >/dev/null
  for _ in $(seq 1 90); do
    curl -fsS --max-time 2 http://127.0.0.1:${PORT}/health >/dev/null 2>&1 && break
    sleep 1
  done
  curl -fsS --max-time 5 http://127.0.0.1:${PORT}/health >/dev/null 2>&1 || {
    docker logs --tail 80 "$API" >&2 || true
    fail "the API did not start"
  }
  echo "API ready on http://127.0.0.1:${PORT}"
}

case "${1:-}" in
  up) up ;;
  down) down ;;
  *) echo "usage: $0 up|down" >&2; exit 2 ;;
esac
