#!/usr/bin/env bash
#
# Prove a backup this product takes can be restored.
#
#   tools/restore-drill.sh
#
# F5. §39 did this once, by hand, with a `pg_dump` typed at a prompt —
# and DP-10 has said "Verified" ever since on the strength of that one
# afternoon. Two things were missing. It was never repeated, so nothing
# would notice the day a migration, an image change or a new extension
# broke it; and it never used **the artefact the product actually
# ships**. `containers/backup.sh` is what the compose stack runs as a
# service and what the Helm chart's CronJob runs as `once`, and it had
# never been executed by anything.
#
# So this drill uses the shipped script to make the dumps and the shipped
# API image to migrate, seed and boot. Nothing here types `pg_dump`.
#
# Every organisation has its own database (`<db>_t<n>`), and a backup run
# is one archive for the shared plane plus one per organisation. The seed
# provisions two organisations the way the application does — one active,
# one deleted (detached, its data kept) — and the drill restores the
# shared archive and each organisation's archive into fresh databases.
#
# What it asserts, in order:
#
#   1. the shipped backup script produces one complete run it is willing
#      to vouch for: a shared archive, one archive per organisation —
#      the detached one included — a MANIFEST naming them, and no archive
#      of the tenant template;
#   2. each archive restores into an empty database with no errors;
#   3. each restored database is the same database: schema counts, row
#      counts, a stored credential and its JSON byte for byte, non-ASCII
#      text in an organisation's own data, and `alembic_version` — and
#      an organisation's archive holds that organisation's rows and none
#      of the other's;
#   4. they still *behave* the same: `alembic upgrade head` applies
#      nothing to the shared plane, the append-only trigger on
#      `audit_events` still refuses an UPDATE in the shared plane *and*
#      in an organisation's database (LG-2 is a control, and a restore
#      that silently dropped it would leave the register wrong), and the
#      unique index on `tenants.number` still refuses to give a second
#      organisation a number already taken — which would be the same
#      database;
#   5. the application boots against the restored shared plane and
#      `/ready` reports both dependencies — checked for its contents, not
#      its status code;
#   6. the script's own refusals work: a canary table that is not there
#      is refused; an organisation whose database cannot be dumped fails
#      the whole run; a registry row pointing at the template does not
#      get the template dumped; and a failed run leaves `.last-success`
#      alone, which is what the compose healthcheck reads to go red.
#
# Point 6 matters as much as the rest. A verification step that cannot
# fail is decoration, and this is a script whose whole job is to notice.
set -euo pipefail

NET=igni-drill
PG=drill-pg
API=drill-api
REDIS=drill-redis
IMAGE=igni-api:drill
PGIMAGE=postgres:17.6-alpine
PASSWORD=drill-only-password
BACKUPS="$(mktemp -d -t igni-drill-XXXXXX)"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${HERE}/.." && pwd)"
PORT=18888

pass=0
step() { printf '\n\033[1m→ %s\033[0m\n' "$*"; }
ok() { printf '    \033[32m✓\033[0m %s\n' "$*"; pass=$((pass + 1)); }
fail() { printf '\n\033[31m✗ %s\033[0m\n' "$*" >&2; exit 1; }

for tool in docker curl; do command -v "$tool" >/dev/null || fail "$tool is not installed"; done

# The two organisations, read from the seed so there is one place they
# are written down.
seed_constant() { sed -n "s/^$1 = '\(.*\)'\$/\1/p" "${HERE}/restore_drill_seed.py"; }
ACTIVE_ORG="$(seed_constant ACTIVE_ORG)"
DETACHED_ORG="$(seed_constant DETACHED_ORG)"
[[ -n "$ACTIVE_ORG" && -n "$DETACHED_ORG" ]] || fail "could not read the organisations from restore_drill_seed.py"

cleanup() {
  local code=$?
  docker rm -f "$API" "$REDIS" "$PG" >/dev/null 2>&1 || true
  docker network rm "$NET" >/dev/null 2>&1 || true
  rm -rf "$BACKUPS"
  if [[ $code -eq 0 ]]; then
    printf '\n\033[32m✓ %d checks passed: the shipped backup script produces restorable archives, one per organisation\033[0m\n' "$pass"
  fi
  exit $code
}
trap cleanup EXIT

psql_on() { local database="$1"; shift; docker exec -i "$PG" psql -qtA -U igni -d "$database" "$@"; }
psql_source() { psql_on igni "$@"; }
psql_restored() { psql_on restored "$@"; }

# `docker run` with the API image, wired to a named database.
api_run() {
  local database="$1"; shift
  docker run --rm --network "$NET" \
    -e APP_ENV=dev \
    -e POSTGRES_HOST="$PG" -e POSTGRES_USER=igni -e POSTGRES_PASSWORD="$PASSWORD" \
    -e POSTGRES_DB="$database" \
    -v "${ROOT}/tools/restore_drill_seed.py:/app/restore_drill_seed.py:ro" \
    "$IMAGE" "$@"
}

# The shipped script, in the database's own image, as the chart runs it:
# `once`. As the invoking user rather than root, the way the chart runs
# it as uid 70 — and so the archives it writes are readable here.
# Extra `docker run` arguments (an `-e` override) go before the image.
backup_run() {
  docker run --rm --network "$NET" --user "$(id -u):$(id -g)" \
    -v "${ROOT}/containers/backup.sh:/scripts/backup.sh:ro" \
    -v "${BACKUPS}:/backups" \
    -e POSTGRES_HOST="$PG" -e POSTGRES_USER=igni -e POSTGRES_PASSWORD="$PASSWORD" -e POSTGRES_DB=igni \
    "$@" "$PGIMAGE" /bin/sh /scripts/backup.sh once
}

# Complete runs, newest last. `.part` directories are not runs.
runs() { find "$BACKUPS" -mindepth 1 -maxdepth 1 -type d -name 'igni-*' ! -name '*.part' | sort; }

schema_query="SELECT
  (SELECT count(*) FROM information_schema.tables WHERE table_schema='public') || '/' ||
  (SELECT count(*) FROM pg_indexes WHERE schemaname='public') || '/' ||
  (SELECT count(*) FROM information_schema.table_constraints WHERE constraint_schema='public') || '/' ||
  (SELECT count(*) FROM information_schema.triggers WHERE trigger_schema='public') || '/' ||
  (SELECT count(DISTINCT t.typname) FROM pg_type t JOIN pg_enum e ON e.enumtypid=t.oid) || '/' ||
  (SELECT count(*) FROM pg_enum)"
rows_query="SELECT
  (SELECT count(*) FROM users) || '/' || (SELECT count(*) FROM organizations) || '/' ||
  (SELECT count(*) FROM org_memberships) || '/' || (SELECT count(*) FROM org_domains) || '/' ||
  (SELECT count(*) FROM inference_nodes) || '/' || (SELECT count(*) FROM tenants) || '/' ||
  (SELECT count(*) FROM audit_events)"
org_rows_query="SELECT
  (SELECT count(*) FROM org_groups) || '/' || (SELECT count(*) FROM org_group_members) || '/' ||
  (SELECT count(*) FROM notifications) || '/' || (SELECT count(*) FROM chat_usage) || '/' ||
  (SELECT count(*) FROM audit_events)"
# The credential and the JSON beside it, which carries a non-ASCII model
# name: a restore that mangled the encoding changes this and not the
# row count.
secret_query="SELECT md5(api_key || models::text) FROM inference_nodes WHERE node_id = 'drill-node'"
# The same, for an organisation's own data: every notification's title,
# one of which is not ASCII.
org_text_query="SELECT md5(coalesce(string_agg(id || ':' || coalesce(title, ''), ',' ORDER BY id), '')) FROM notifications"

step "Building the image the product ships"
docker network create "$NET" >/dev/null 2>&1 || true
docker rm -f "$PG" "$API" "$REDIS" >/dev/null 2>&1 || true
docker build -q -f "${ROOT}/containers/be/Dockerfile" -t "$IMAGE" "$ROOT" >/dev/null

step "A shared plane and two organisations at head, with something in each"
docker run -d --name "$PG" --network "$NET" \
  -e POSTGRES_USER=igni -e POSTGRES_PASSWORD="$PASSWORD" -e POSTGRES_DB=igni \
  "$PGIMAGE" >/dev/null
# A fresh cluster starts twice: once to initialise, then for real. Probe
# only after the entrypoint says initialisation is complete. Probing every
# second through the first server's shutdown is what stalled it on a CI
# runner (the log ended mid-shutdown, after the checkpoint), while the same
# drill passed locally.
for _ in $(seq 1 180); do
  docker logs "$PG" 2>&1 | grep -q 'PostgreSQL init process complete' && break
  sleep 1
done
for _ in $(seq 1 60); do
  docker exec "$PG" pg_isready -U igni -d igni >/dev/null 2>&1 && break
  sleep 1
done
if ! docker exec "$PG" pg_isready -U igni -d igni >/dev/null 2>&1; then
  docker inspect -f 'state={{.State.Status}} exit={{.State.ExitCode}} oom={{.State.OOMKilled}}' "$PG" >&2 || true
  docker logs --tail 60 "$PG" >&2 || true
  fail "Postgres never became ready"
fi

# The migrate job's two steps, in its order: the shared plane, then the
# template every organisation's database is copied from.
api_run igni alembic upgrade head >/dev/null || fail "the shared migrations did not apply"
api_run igni python -m app.db.sql.tenant_migrations >/dev/null 2>&1 || fail "the tenant migrations did not apply"
api_run igni python restore_drill_seed.py || fail "the seed did not run"
ok "migrated and seeded with the shipped image"

ACTIVE_DB="$(psql_source -c "SELECT database FROM tenants WHERE org_id = '${ACTIVE_ORG}' AND state = 'active'")"
DETACHED_DB="$(psql_source -c "SELECT database FROM tenants WHERE org_id = '${DETACHED_ORG}' AND state = 'detached'")"
[[ -n "$ACTIVE_DB" ]] || fail "the active organisation is not registered as active"
[[ -n "$DETACHED_DB" ]] || fail "the deleted organisation is not registered as detached"
[[ "$(psql_source -c "SELECT count(*) FROM pg_database WHERE datname = 'igni_tenant_template'")" == "1" ]] ||
  fail "there is no tenant template, so the check that it is not backed up would prove nothing"
ok "organisations provisioned by the application: ${ACTIVE_DB} (active), ${DETACHED_DB} (detached); template present"

# Everything the restore will be compared against, read once.
before_schema=$(psql_source -c "$schema_query")
before_rows=$(psql_source -c "$rows_query")
before_secret=$(psql_source -c "$secret_query")
before_alembic=$(psql_source -c "SELECT version_num FROM alembic_version")
before_org_schema=$(psql_on "$ACTIVE_DB" -c "$schema_query")
before_org_rows=$(psql_on "$ACTIVE_DB" -c "$org_rows_query")
before_org_text=$(psql_on "$ACTIVE_DB" -c "$org_text_query")
before_org_alembic=$(psql_on "$ACTIVE_DB" -c "SELECT version_num FROM alembic_version")
before_detached_rows=$(psql_on "$DETACHED_DB" -c "$org_rows_query")
[[ "$before_org_rows" != "$before_detached_rows" ]] ||
  fail "both organisations hold the same counts, so an archive holding the wrong one would compare equal"

step "The backup, taken by the script the product ships"
backup_run || fail "containers/backup.sh failed"

run=$(runs | tail -1)
[[ -n "$run" ]] || fail "the script reported success and produced no run directory"
[[ "$(runs | wc -l | tr -d ' ')" == "1" ]] || fail "one run produced more than one run directory"
[[ -f "${BACKUPS}/.last-success" ]] || fail "no .last-success was written, so the healthcheck can never go green"
# `.part` is renamed only after the archive is verified, and the run
# directory only after every archive in it is, so a leftover one means
# the script counted something it had not checked.
[[ -z "$(find "$BACKUPS" -name '*.part')" ]] || fail "a .part survived a successful run"

shared_dump="${run}/shared-igni.dump"
active_dump="${run}/org-${ACTIVE_ORG}-${ACTIVE_DB}.dump"
detached_dump="${run}/org-${DETACHED_ORG}-${DETACHED_DB}.dump"
for archive in "$shared_dump" "$active_dump" "$detached_dump" "${run}/MANIFEST"; do
  [[ -s "$archive" ]] || fail "$(basename "$archive") is missing from $(basename "$run"): $(ls "$run")"
done
[[ "$(find "$run" -name '*.dump' | wc -l | tr -d ' ')" == "3" ]] ||
  fail "expected three archives (shared and two organisations), found: $(ls "$run")"
ok "$(basename "$run"): the shared plane and one archive per organisation"

[[ -z "$(find "$BACKUPS" -name '*tenant_template*')" ]] || fail "the tenant template was backed up"
ok "the tenant template was not backed up"

T=$'\t'

grep -q "^org${T}${DETACHED_ORG}${T}[0-9]*${T}detached${T}${DETACHED_DB}${T}" "${run}/MANIFEST" ||
  fail "the MANIFEST does not record the detached organisation: $(cat "${run}/MANIFEST")"
grep -q "^org${T}${ACTIVE_ORG}${T}[0-9]*${T}active${T}${ACTIVE_DB}${T}" "${run}/MANIFEST" ||
  fail "the MANIFEST does not record the active organisation: $(cat "${run}/MANIFEST")"
ok "the MANIFEST names both organisations, the detached one included"

step "Restoring each archive into an empty database"
restore_into() {
  local database="$1" archive="$2"
  psql_source -c "CREATE DATABASE ${database} OWNER igni" >/dev/null
  docker cp "$archive" "$PG:/tmp/${database}.dump" >/dev/null
  docker exec "$PG" pg_restore -U igni -d "$database" --exit-on-error "/tmp/${database}.dump" ||
    fail "pg_restore refused $(basename "$archive"), which the backup script vouched for"
  ok "$(basename "$archive") → ${database}, with --exit-on-error"
}
restore_into restored "$shared_dump"
restore_into restored_org "$active_dump"
restore_into restored_detached "$detached_dump"

step "Is each one the same database?"
compare() {
  local what="$1" expected="$2" actual="$3"
  [[ "$expected" == "$actual" ]] || fail "$what differs: source ${expected}, restored ${actual}"
  ok "$what: ${actual}"
}
compare "shared: tables/indexes/constraints/triggers/enums/labels" "$before_schema" \
  "$(psql_restored -c "$schema_query")"
compare "shared: rows (users/orgs/members/domains/nodes/tenants/audit)" "$before_rows" \
  "$(psql_restored -c "$rows_query")"
compare "shared: the stored credential and its JSON, byte for byte (md5)" "$before_secret" \
  "$(psql_restored -c "$secret_query")"
compare "shared: alembic_version" "$before_alembic" \
  "$(psql_restored -c "SELECT version_num FROM alembic_version")"

compare "organisation: tables/indexes/constraints/triggers/enums/labels" "$before_org_schema" \
  "$(psql_on restored_org -c "$schema_query")"
compare "organisation: rows (groups/members/notifications/usage/audit)" "$before_org_rows" \
  "$(psql_on restored_org -c "$org_rows_query")"
compare "organisation: notification text, byte for byte (md5)" "$before_org_text" \
  "$(psql_on restored_org -c "$org_text_query")"
compare "organisation: alembic_version (tenant tree)" "$before_org_alembic" \
  "$(psql_on restored_org -c "SELECT version_num FROM alembic_version")"
compare "detached organisation: rows (groups/members/notifications/usage/audit)" "$before_detached_rows" \
  "$(psql_on restored_detached -c "$org_rows_query")"

# Isolation, in the backups as in the database: one organisation's
# archive holds nothing of the other's.
leaked=$(psql_on restored_org -c "SELECT
  (SELECT count(*) FROM audit_events WHERE org_id = '${DETACHED_ORG}') +
  (SELECT count(*) FROM org_groups WHERE org_id = '${DETACHED_ORG}')")
[[ "$leaked" == "0" ]] || fail "the active organisation's archive holds ${leaked} row(s) of the other organisation"
leaked=$(psql_on restored_detached -c "SELECT
  (SELECT count(*) FROM audit_events WHERE org_id = '${ACTIVE_ORG}') +
  (SELECT count(*) FROM org_groups WHERE org_id = '${ACTIVE_ORG}')")
[[ "$leaked" == "0" ]] || fail "the detached organisation's archive holds ${leaked} row(s) of the other organisation"
ok "each organisation's archive holds its own rows and none of the other's"

step "Does it still behave the same?"
applied=$(api_run restored alembic upgrade head 2>&1 | grep -c 'Running upgrade' || true)
[[ "$applied" == "0" ]] || fail "the restored database is not at head: ${applied} migration(s) applied"
ok "alembic upgrade head applies nothing to the shared plane"

refusal=$(psql_restored -c "UPDATE audit_events SET status_code = 500 WHERE id = 'drill-audit-0'" 2>&1 || true)
grep -q 'append-only' <<<"$refusal" ||
  fail "the append-only trigger on the shared audit_events did not survive the restore (LG-2): ${refusal}"
ok "shared audit_events is still append-only"

refusal=$(psql_on restored_org -c "UPDATE audit_events SET status_code = 500 WHERE id = 'drill-org-a-audit-0'" 2>&1 || true)
grep -q 'append-only' <<<"$refusal" ||
  fail "the append-only trigger on the organisation's audit_events did not survive the restore (LG-2): ${refusal}"
ok "the organisation's audit_events is still append-only"

# pg_restore loads the rows before it builds the indexes, so an index
# that failed to come back leaves every row in place and every count
# above equal. Only an insert that should be refused shows it.
taken=$(psql_restored -c "SELECT number FROM tenants WHERE org_id = '${ACTIVE_ORG}'")
duplicate=$(psql_restored -c "INSERT INTO tenants (org_id, number, database, state, created_at)
  VALUES ('drill-second-org', ${taken}, 'igni_t_again', 'active', now())" 2>&1 || true)
grep -q 'idx_tenants_number' <<<"$duplicate" ||
  fail "a second organisation was given tenant number ${taken} — the unique index did not survive the restore: ${duplicate}"
ok "tenants.number is still unique"

step "Does the application come up on it?"
docker run -d --name "$REDIS" --network "$NET" redis:7.4-alpine >/dev/null
# A throwaway encryption key, required even with APP_ENV=dev, made for
# this run. The drill's rows hold no signing key, so the server generates
# one under this.
drill_key="$(python3 -c 'import base64, os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())')"
docker run -d --name "$API" --network "$NET" -p "${PORT}:7777" \
  -e APP_ENV=dev -e POSTGRES_HOST="$PG" -e POSTGRES_USER=igni -e POSTGRES_PASSWORD="$PASSWORD" \
  -e POSTGRES_DB=restored -e REDIS_HOST="$REDIS" \
  -e SECRET_ENCRYPTION_KEY="$drill_key" \
  "$IMAGE" >/dev/null
for _ in $(seq 1 60); do
  curl -fsS --max-time 2 "http://127.0.0.1:${PORT}/health" >/dev/null 2>&1 && break
  sleep 1
done
curl -fsS --max-time 5 "http://127.0.0.1:${PORT}/health" >/dev/null 2>&1 ||
  fail "the application did not start against the restored database"
ready=$(curl -fsS --max-time 5 "http://127.0.0.1:${PORT}/ready")
grep -q '"postgres": *"ok"' <<<"$ready" || fail "/ready does not report postgres as ok: ${ready}"
grep -q '"redis": *"ok"' <<<"$ready" || fail "/ready does not report redis as ok: ${ready}"
ok "/ready reports both dependencies against the restored database"

step "And can the script refuse a bad backup?"
# A registry row pointing at the template. The application never writes
# one; if something did, the template must still not be dumped. This run
# is otherwise good, so it succeeds — before the failing ones below, so
# .last-success is in its final state when they are checked.
#
# And a database named like an organisation's that the registry does not
# know — what a crash between creating and registering one leaves. Not
# backed up, since nothing routes to it, but it must be in the log.
psql_source -c "INSERT INTO tenants (org_id, number, database, state, created_at)
  VALUES ('drill-template-row', 98, 'igni_tenant_template', 'active', now())" >/dev/null
psql_source -c "CREATE DATABASE igni_t50 OWNER igni" >/dev/null
sleep 1
log=$(backup_run 2>&1) || fail "a registry row pointing at the template failed the run instead of being skipped: ${log}"
[[ -z "$(find "$BACKUPS" -name '*tenant_template*')" ]] ||
  fail "a registry row pointing at the template got the template dumped"
grep -q 'WARNING: the tenants row for drill-template-row points at the template' <<<"$log" ||
  fail "the template row was skipped without a word in the log: ${log}"
ok "a registry row pointing at the template does not get it dumped, and says so"
grep -q 'not in tenants, not backed up: igni_t50' <<<"$log" ||
  fail "an unregistered organisation-shaped database went unmentioned: ${log}"
ok "an unregistered organisation-shaped database is named in the log"
psql_source -c "DELETE FROM tenants WHERE org_id = 'drill-template-row'" >/dev/null
psql_source -c "DROP DATABASE igni_t50" >/dev/null

# Run directories are named to the second; each run below gets its own.
sleep 1
before_marker=$(cat "${BACKUPS}/.last-success")
before_runs=$(runs | wc -l | tr -d ' ')

# A canary table this schema does not have. The dump itself is fine, so
# what is being tested is the verification step rather than pg_dump.
if log=$(backup_run -e IGNI_BACKUP_CANARY_TABLE=a_table_this_schema_does_not_have 2>&1); then
  fail "the script accepted a dump that does not contain its canary table"
fi
grep -q 'does not contain a_table_this_schema_does_not_have' <<<"$log" ||
  fail "the run failed, but not at the canary check: ${log}"
ok "a dump without the canary table is refused"

# An organisation whose database cannot be dumped. Every other archive
# in the run is fine; the run must still not count.
psql_source -c "INSERT INTO tenants (org_id, number, database, state, created_at)
  VALUES ('drill-missing-org', 99, 'igni_t99', 'active', now())" >/dev/null
sleep 1
if log=$(backup_run 2>&1); then
  fail "a run that could not back up one organisation reported success"
fi
psql_source -c "DELETE FROM tenants WHERE org_id = 'drill-missing-org'" >/dev/null
# For that reason and no other: the organisations around it were dumped.
grep -q 'organisation drill-missing-org (igni_t99, active) was not backed up' <<<"$log" ||
  fail "the run failed, but not because of the missing organisation: ${log}"
grep -q "wrote .*/org-${ACTIVE_ORG}-${ACTIVE_DB}.dump" <<<"$log" ||
  fail "the other organisations were not attempted after one failed: ${log}"
ok "one organisation that cannot be dumped fails the whole run"

[[ "$(runs | wc -l | tr -d ' ')" == "$before_runs" ]] ||
  fail "a failed run was promoted to a complete run directory"
ok "a failed run stays .part and is not counted as a run"

[[ "$(cat "${BACKUPS}/.last-success")" == "$before_marker" ]] ||
  fail ".last-success moved on a failed run, so the healthcheck would stay green through an outage"
ok ".last-success is untouched by a failure"
