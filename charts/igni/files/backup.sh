#!/bin/sh
# Postgres backups for a self-hosted igni deployment.
#
#   backup.sh once   — one run now, exit code says whether it worked
#   backup.sh        — one run now, then one every IGNI_BACKUP_INTERVAL_SECONDS
#
# Used two ways from one script: the compose stack runs it as a long-lived
# service (no host cron to install, works the same on any distribution),
# and the Helm chart runs `once` from a CronJob. The chart carries a byte
# -identical copy in `charts/igni/files/` and a test compares them, because
# two copies of a backup script that have drifted is worse than one.
#
# ## What one run writes
#
# Every organisation has its own logical database on the server
# (`<POSTGRES_DB>_t<number>`, registered in the shared plane's `tenants`
# table), so one run is one archive per organisation plus one for the
# shared plane, in a directory named for the run:
#
#   igni-20261024T030000Z/
#     MANIFEST                       one line per archive: kind, org_id,
#                                    tenant number, state, database, file
#     shared-igni.dump               the shared plane (POSTGRES_DB)
#     org-<org_id>-igni_t1.dump      one organisation's own database
#     org-<org_id>-igni_t2.dump      ...detached organisations included
#
# One archive per organisation, not one for the server, because isolation
# has to hold in the backups too: an organisation's archive never contains
# another's rows, one organisation can be restored without rolling back
# the others, and a deleted (detached) organisation's archive can be
# handed over, kept or destroyed on its own schedule.
#
# Organisations are read from `tenants` *after* the shared plane is
# dumped. Rows are never removed from it (a deleted organisation is
# `detached`, not deleted), so every organisation the shared archive knows
# about has an archive in the same run. Detached organisations are backed
# up like active ones: their data is kept, so it is backed up.
#
# `<POSTGRES_DB>_tenant_template` is never dumped. It holds no data and is
# rebuilt from migrations; a copy of it in a backup is only something to
# restore by mistake.
#
# ## Five things here are deliberate, and each is a way backups fail quietly.
#
# **Dumped with the server's own pg_dump.** This runs in the same image
# as the database, so the versions match by construction. A pg_dump older
# than its server refuses outright; the reverse produces archives the
# customer's restore may not read.
#
# **Written to .part and renamed.** A rename within one filesystem is
# atomic, so a crash — OOM, a full disk, a stopped container — leaves a
# `.part` that is obviously incomplete rather than a truncated `.dump`
# that looks like a backup. The run directory itself is `igni-<stamp>.part`
# until every archive in it is verified, so a run is complete or it is
# visibly not one.
#
# **Verified before it counts.** `pg_restore --list` must parse each
# archive *and* find a table its schema is known to have. A zero-byte
# file, a truncated one, and a dump of the wrong database all pass a
# `pg_dump` exit code of 0 in at least one failure mode each. The restore
# drill (tools/restore-drill.sh) is what a real restore looks like; this
# is the cheap check that the file could take part in one.
#
# **All or nothing.** One organisation whose dump fails fails the run:
# the directory stays `.part`, `.last-success` does not move and the
# healthcheck goes red. A run that backed up nine organisations out of ten
# is a run that lost the tenth, and it must not look like a good night.
# The rest are still attempted, so the log names every organisation that
# failed rather than only the first.
#
# **Pruned only after a success.** Retention that runs unconditionally
# turns one failing night into an empty backup directory a fortnight
# later. Nothing old is deleted until something new is verified.
set -eu

DIR="${IGNI_BACKUP_DIR:-/backups}"
RETAIN_DAYS="${IGNI_BACKUP_RETAIN_DAYS:-14}"
INTERVAL="${IGNI_BACKUP_INTERVAL_SECONDS:-86400}"

# A table every deployment has. Used to prove the archive is a dump of
# this schema rather than a valid archive of something else. One for the
# shared plane, one for an organisation's own database.
CANARY_TABLE="${IGNI_BACKUP_CANARY_TABLE:-audit_events}"
TENANT_CANARY_TABLE="${IGNI_BACKUP_TENANT_CANARY_TABLE:-audit_events}"

: "${POSTGRES_HOST:?POSTGRES_HOST is not set, so there is nothing to back up}"
: "${POSTGRES_USER:?POSTGRES_USER is not set}"
: "${POSTGRES_DB:?POSTGRES_DB is not set}"
POSTGRES_PORT="${POSTGRES_PORT:-5432}"

# Never backed up: see the header. The name app/db/sql/tenancy.py gives it.
TEMPLATE_DB="${POSTGRES_DB}_tenant_template"

export PGPASSWORD="${POSTGRES_PASSWORD:-}"

TAB="$(printf '\t')"

log() {
	echo "$(date -u '+%Y-%m-%d %H:%M:%SZ') backup: $*"
}

# psql against the shared plane: unaligned, tuples only, tab-separated,
# and stopping on the first error so a failed query is a non-zero exit
# rather than empty output that reads as "no organisations".
psql_shared() {
	psql -X -q -t -A -v ON_ERROR_STOP=1 -F "$TAB" \
		--host="$POSTGRES_HOST" \
		--port="$POSTGRES_PORT" \
		--username="$POSTGRES_USER" \
		--dbname="$POSTGRES_DB" \
		"$@"
}

# A file name from a value that came out of a database. Anything but
# letters, digits, `.`, `_` and `-` becomes `_`, so an org_id cannot put
# a `/` in a path.
safe_name() {
	printf '%s' "$1" | tr -c 'A-Za-z0-9._-' '_'
}

# dump_one DATABASE TARGET CANARY — one database into one verified archive.
dump_one() {
	db="$1"
	target="$2"
	canary="$3"
	part="$target.part"

	log "dumping $db from $POSTGRES_HOST:$POSTGRES_PORT"

	# -Fc: the custom format. Compressed, and the only one pg_restore can
	# take a subset out of — which is what a real recovery usually needs.
	if ! pg_dump \
		--format=custom \
		--host="$POSTGRES_HOST" \
		--port="$POSTGRES_PORT" \
		--username="$POSTGRES_USER" \
		--dbname="$db" \
		--file="$part"; then
		log "FAILED: pg_dump of $db did not complete. Leaving $part for inspection."
		return 1
	fi

	if ! pg_restore --list "$part" >/tmp/toc.txt 2>/dev/null; then
		log "FAILED: pg_dump of $db exited 0 but pg_restore cannot read the archive."
		return 1
	fi

	# The table's own entry in the table of contents (`TABLE public
	# audit_events <owner>`), not any line containing the name: an index
	# called `idx_audit_events_org` is not the table.
	if ! grep -q " TABLE public $canary " /tmp/toc.txt; then
		log "FAILED: the archive of $db parses but does not contain $canary."
		log "        This is a dump of the wrong database, or of an unmigrated one."
		return 1
	fi

	mv "$part" "$target"
	chmod 600 "$target"

	size="$(wc -c <"$target" | tr -d ' ')"
	log "wrote $target ($size bytes, $(grep -c . /tmp/toc.txt) archive entries)"
	return 0
}

backup_once() {
	if [ ! -d "$DIR" ]; then
		log "FAILED: $DIR does not exist. Mount it."
		return 1
	fi

	# Incomplete runs and archives from earlier failures. Anything older
	# than an hour goes, so a crash-looping misconfiguration cannot fill
	# the disk with fragments while the operator is asleep; a recent one
	# stays, because that is the one worth looking at.
	find "$DIR" -mindepth 1 -maxdepth 1 -name 'igni-*.part' -mmin +60 -exec rm -rf {} \; 2>/dev/null || true

	stamp="$(date -u '+%Y%m%dT%H%M%SZ')"
	run="$DIR/igni-$stamp"
	run_part="$run.part"

	# Run directories are named to the second. A second run inside the
	# same second would otherwise be moved *into* the first one.
	if [ -e "$run" ] || [ -e "$run_part" ]; then
		log "FAILED: a run stamped $stamp already exists."
		return 1
	fi
	if ! mkdir -m 700 "$run_part"; then
		log "FAILED: cannot create $run_part."
		return 1
	fi

	manifest="$run_part/MANIFEST.part"
	printf '# kind\torg_id\tnumber\tstate\tdatabase\tarchive\n' >"$manifest"

	# The shared plane first: the registry of organisations is in it.
	shared_file="shared-$(safe_name "$POSTGRES_DB").dump"
	if ! dump_one "$POSTGRES_DB" "$run_part/$shared_file" "$CANARY_TABLE"; then
		log "FAILED: the shared plane was not backed up; not attempting the organisations."
		return 1
	fi
	printf 'shared\t-\t-\t-\t%s\t%s\n' "$POSTGRES_DB" "$shared_file" >>"$manifest"

	# Every organisation, active and detached. A query that fails is a
	# failed run, not a run with no organisations in it.
	if ! psql_shared -c 'SELECT org_id, number, state, database FROM tenants ORDER BY number' >/tmp/tenants.tsv; then
		log "FAILED: could not read the tenants table, so no organisation was backed up."
		return 1
	fi

	failed=0
	orgs=0
	while IFS="$TAB" read -r org_id number state database; do
		[ -n "$org_id" ] || continue
		if [ "$database" = "$TEMPLATE_DB" ]; then
			# The application never registers the template. A row that
			# points at it is a fault worth reading about, and a dump of
			# the template is not what that fault needs.
			log "WARNING: the tenants row for $org_id points at the template $TEMPLATE_DB; not dumping it"
			continue
		fi
		org_file="org-$(safe_name "$org_id")-$(safe_name "$database").dump"
		if dump_one "$database" "$run_part/$org_file" "$TENANT_CANARY_TABLE"; then
			printf 'org\t%s\t%s\t%s\t%s\t%s\n' "$org_id" "$number" "$state" "$database" "$org_file" >>"$manifest"
			orgs=$((orgs + 1))
		else
			log "FAILED: organisation $org_id ($database, $state) was not backed up"
			failed=$((failed + 1))
		fi
	done </tmp/tenants.tsv

	if [ "$failed" -gt 0 ]; then
		log "FAILED: $failed organisation(s) not backed up. $run_part is incomplete and does not count."
		return 1
	fi

	# A database named like an organisation's that the registry does not
	# know: normally a crash between creating one and registering it,
	# which the next provisioning adopts. Nothing routes to it, so it is
	# not backed up — but it is a line in the log rather than silence.
	unregistered="$(printf '%s\n' \
		"SELECT datname FROM pg_database
		  WHERE left(datname, length(:'db') + 2) = :'db' || '_t'
		    AND substr(datname, length(:'db') + 3) ~ '^[0-9]+\$'
		    AND datname NOT IN (SELECT database FROM tenants)
		  ORDER BY datname;" | psql_shared -v db="$POSTGRES_DB" 2>/dev/null | tr '\n' ' ' || true)"
	if [ -n "$(printf '%s' "$unregistered" | tr -d ' ')" ]; then
		log "WARNING: databases named like an organisation's but not in tenants, not backed up: $unregistered"
	fi

	mv "$manifest" "$run_part/MANIFEST"
	chmod 600 "$run_part/MANIFEST"
	# The whole run counts, or none of it does.
	mv "$run_part" "$run"
	log "run complete: $run (the shared plane and $orgs organisation(s))"

	# Only now is it safe to delete anything: run directories, and the
	# single-file dumps (`igni-<db>-<stamp>.dump`) earlier versions wrote.
	deleted="$(find "$DIR" -mindepth 1 -maxdepth 1 -name 'igni-*' ! -name '*.part' -mtime "+$RETAIN_DAYS" -print -exec rm -rf {} \; | wc -l | tr -d ' ')"
	if [ "$deleted" -gt 0 ]; then
		log "pruned $deleted backup(s) older than $RETAIN_DAYS days"
	fi

	# The healthcheck reads this file's age. Written last, so it means
	# "a complete, verified run exists" and not "a run was attempted".
	date -u '+%Y-%m-%dT%H:%M:%SZ' >"$DIR/.last-success"
	return 0
}

if [ "${1:-loop}" = "once" ]; then
	backup_once
	exit $?
fi

# The first run is immediate and fatal. A backup service that waits until
# tomorrow to tell you the password is wrong is not a backup service —
# with `restart: always` in front, a bad configuration is a visible
# crash loop at deploy time rather than a discovery during a recovery.
if ! backup_once; then
	log "the first backup failed; exiting so this is visible now rather than at 3am"
	exit 1
fi

while true; do
	log "next backup in ${INTERVAL}s"
	sleep "$INTERVAL"
	# Later failures do not exit: the deployment is working and one bad
	# night should not stop tomorrow's attempt. `.last-success` stops
	# being updated, so the healthcheck goes red and stays red.
	backup_once || log "backup failed; will retry after the interval"
done
