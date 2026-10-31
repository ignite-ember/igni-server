# Backup, restore and continuity

For the customer's security and operations teams. How a self-hosted igni
deployment is backed up, how a restore is proven, what the backup does not
cover, and what keeps working if Ignite Ember cannot. Part of the security
pack ([SECURITY_PACK.md](SECURITY_PACK.md)).

Ignite Ember is the company. igni is the software. Ignite Ember has no
access to a deployment, so every step here is the customer's to run.

## 1. What the shipped backup does

igni server keeps all of its state in the customer's PostgreSQL: a shared
plane (`POSTGRES_DB`) and one database per organisation
(`<POSTGRES_DB>_t<n>`). The shipped script, `containers/backup.sh`, dumps
them.

| | What happens | Where it is set |
|---|---|---|
| One run | One `pg_dump -Fc` archive for the shared plane and one per organisation, detached (deleted) organisations included, plus a `MANIFEST` naming each | `containers/backup.sh`, header |
| Checked before it counts | Each archive must parse with `pg_restore --list` and contain a known table. Until every archive passes, the run directory stays `igni-<stamp>.part` | The same |
| All or nothing | One organisation that fails fails the run, and the health check goes red | The same |
| Retention | Runs older than 14 days are deleted, but only after a newer run is complete | `IGNI_BACKUP_RETAIN_DAYS` in compose, `backup.retainDays` in the chart |
| Schedule | Compose: one run every 24 hours (`IGNI_BACKUP_INTERVAL_SECONDS`). Helm: a CronJob, `0 3 * * *` UTC | `containers/docker-compose.prod.yml`, `charts/igni/values.yaml` |
| Where it writes | The directory named by `IGNI_BACKUP_DIR` (compose), or a PersistentVolumeClaim the customer names (`backup.existingClaim`, Helm) | The same |
| Off by default with Helm | `backup.enabled: false`. The chart will not choose a storage class for you | `charts/igni/values.yaml` |

Compose runs the script as a service under the `backup` profile; its health
check fails when the last good run is older than expected. Helm runs the
same script, byte for byte (`charts/igni/files/backup.sh`, compared by a
test), as `charts/igni/templates/backup-cronjob.yaml`.

**Nothing leaves the host.** The script uploads nothing, on purpose: a cloud
SDK in the backup path would contradict the air-gap claim. Copying the
backups to a second site is the customer's job (DP-12 in
[COMPLIANCE_TRACKER.md](COMPLIANCE_TRACKER.md), `Operator`).

## 2. How a restore is proven

`tools/restore-drill.sh` runs in CI on every change (`.github/workflows/ci.yml`,
"Backups restore"). It uses the shipped script to dump a seeded deployment
with two organisations, one of them detached, restores every archive into
fresh databases, and checks:

* schema, row counts, sequences and constraints match the source;
* stored credentials match byte for byte, still encrypted;
* the append-only trigger on `audit_events` still refuses an `UPDATE`;
* the API boots against the restored databases and reports ready.

The commands to restore one organisation beside the live one, or a whole
server, are in `charts/igni/README.md`, "Restoring one organisation".
[EXIT_TEST_RUNBOOK.md](EXIT_TEST_RUNBOOK.md), step B4 and step C6, restores a
production backup into a test environment and into plain PostgreSQL with no
igni software. Run one of those at least once a year and keep the output.

## 3. What to keep outside the database

A database backup alone does not bring a deployment back. Keep these too,
each under the customer's own key custody:

| Item | Why | Where it lives |
|---|---|---|
| The encryption key (`SECRET_ENCRYPTION_KEY` setting) | Every stored secret, including the token-signing key, is encrypted with it. Without it those secrets cannot be read and must be set again, and every session ends. Keep it apart from the backups: a backup holding both defeats the encryption | The deployment's secret store (`charts/igni/README.md`) |
| The licence file | The paid modules check it at start and every minute after | `/etc/igni/licence/licence.json`, from a Secret or `IGNI_LICENCE_DIR` ([licence-file.md](licence-file.md)) |
| The deployment's configuration | Settings, inference node list, identity provider details | The customer's env file, chart values or ConfigMaps |
| TLS certificates | The edge needs them to serve | Caddy's `caddy_data` volume, or a Kubernetes Secret |
| The release bundle and the public keys | Reinstalling the same version without Ignite Ember | The customer's registry and media ([OFFLINE_UPDATES.md](OFFLINE_UPDATES.md)) |
| Model weights | igni does not hold them | The customer's GPU hosts ([MODEL_PROVENANCE.md](MODEL_PROVENANCE.md)) |

Redis holds concurrency leases, one-time sign-in codes and the node
registry. None of it needs a backup: leases expire, a code can be asked for
again, and a live node announces itself again on its next heartbeat;
statically configured nodes are not in Redis at all ([gpu-clusters.md](gpu-clusters.md)).

## 4. What the shipped backup does not cover

Stated so nobody assumes it does.

* **CodeIndex and Artifacts.** Each has its own PostgreSQL databases with
  their own organisation registry. `containers/backup.sh` does not dump them,
  and neither module ships a backup job for a customer deployment. Dump each
  with `pg_dump -Fc` yourself ([EXIT_TEST_RUNBOOK.md](EXIT_TEST_RUNBOOK.md),
  G5).
* **Neo4j.** Artifacts keeps a document graph per organisation in Neo4j,
  and CodeIndex builds a Neo4j index on each developer machine. Neither is
  in any igni backup. The developer-machine index can be rebuilt from the
  changeset store; the Artifacts graph needs Neo4j's own backup.
* **The CodeIndex changeset store.** A filesystem path or an S3-compatible
  or GCS bucket the customer names. Back it up with the storage it lives on.
* **Fleet.** The gateway's PostgreSQL, the producers' volumes on network
  storage, and the OpenBao or Vault Transit keys that unwrap them. Fleet is
  not packaged for customers yet and has no backup tooling.
* **Developer machines.** Client configuration and per-project state in
  `~/.igni/`. These are the customer's endpoint backups.

## 5. Running when something fails

| If this fails | What happens | What brings it back |
|---|---|---|
| One API or portal pod | The chart runs two replicas of each by default (`api.replicaCount`, `portal.replicaCount`). The retention round takes a PostgreSQL advisory lock, so it runs once whichever replica holds it | Kubernetes restarts the pod |
| PostgreSQL | `/ready` reports it down and the API stops taking traffic; `/health` stays up so pods do not restart in a loop | The customer's database high availability, or a restore (section 2) |
| Redis | `/ready` reports it down | Restart it; nothing in it needs restoring |
| An inference node | A registered node that stops sending heartbeats leaves rotation on its own, and a failed request is retried on another node | The customer's GPU operations ([gpu-clusters.md](gpu-clusters.md)); [INCIDENT_RESPONSE.md](INCIDENT_RESPONSE.md), C4, takes a node out by hand |
| A paid module | Each module answers for itself. igni server, sign-in and chat keep working without it | Restart, or restore its own databases |
| The licence lapses | The paid modules warn for 90 days, then go read-only. They never switch off and delete nothing | Install a renewed licence file ([licence-file.md](licence-file.md)) |

The alert rules that tell you about these are in
`charts/igni/files/alerts.yaml`, explained in
[observability.md](observability.md).

Recovery time and recovery point objectives are the customer's to set.
igni's part is a nightly dump by default and a tested restore. A shorter
recovery point needs the customer's own PostgreSQL replication or
point-in-time recovery, which igni does not configure.

## 6. If Ignite Ember cannot continue

The deployment does not depend on Ignite Ember to run. Nothing calls
Ignite Ember, the licence check is offline, and the base (server, portal,
CLI, desktop app) is Apache-2.0, so the customer may build and run it from
source ([EXIT_TEST_RUNBOOK.md](EXIT_TEST_RUNBOOK.md), scenario B).

What the customer loses is new releases, support and new licence files. A
paid module whose licence then lapses goes read-only and stays readable and
exportable. The source of the paid modules is meant to reach the customer
through escrow, but **no escrow agreement is signed and no agent is chosen
yet** (runbook, G7).

Ignite Ember's own business continuity plan, covering who can issue a
licence file, sign a release and answer support if its people are
unavailable: `__ORGANISATION_TO_COMPLETE__`.
