# igni — Helm chart

igni server, the open-source base of igni: API, admin portal, and the
database migration that runs before either of them serves.

Nothing in this chart calls out to us. There is no telemetry, no licence
check and no phone-home — asserted by a test, not by this paragraph
(`tests/test_nothing_phones_home.py`).

## What you need first

* **Kubernetes 1.25+** and Helm 3.
* **PostgreSQL 15+**, reachable from the cluster. This chart deploys no
  database and bundles no subchart for one, deliberately: a bundled
  Postgres is a second thing to patch, scan and back up, and a chart
  dependency has to be fetched over the network, which is exactly what an
  air-gapped install cannot do.
* **Redis 6+**, likewise.
* **The two images.** Releases are published to
  `ghcr.io/ignite-ember/igni-api` and `ghcr.io/ignite-ember/igni-portal`,
  and carried in each release's signed offline bundle. Point the chart at
  whichever registry your cluster pulls from — your own mirror, and for an
  air-gapped install only that. There is no default repository, because
  an unqualified image name resolves to Docker Hub and an air-gapped
  cluster cannot reach a public one.

## Install

```bash
# The encryption key. Generate it once and keep it, backed up apart from
# the database: the server's own token-signing key is stored under it.
# There is no signing key to generate — the server makes its own.
ENCRYPTION_KEY=$(python3 -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')

helm install igni ./charts/igni \
  --namespace igni --create-namespace \
  --set image.repository=registry.example.com/igni/igni-api \
  --set image.tag=2026.09.01-abc1234 \
  --set portalImage.repository=registry.example.com/igni/igni-portal \
  --set portalImage.tag=2026.09.01-abc1234 \
  --set postgres.host=pg.internal \
  --set postgres.user=igni \
  --set postgres.database=igni \
  --set secrets.postgresPassword=... \
  --set redis.host=redis.internal \
  --set secrets.secretEncryptionKey="$ENCRYPTION_KEY" \
  --set urls.publicBase=https://igni.example.com \
  --set urls.portalBase=https://igni.example.com \
  --set urls.oauthRedirectBase=https://igni.example.com \
  --set urls.oauthFrontendBase=https://igni.example.com \
  --set ingress.enabled=true \
  --set ingress.host=igni.example.com
```

In practice, put that in a values file instead — and if you do, see
**Secrets** below, because a values file with credentials in it is a
credential.

## What it refuses to do

The chart will not render without the image repositories, the
Postgres host, the Redis host and a 44-character Fernet
`secrets.secretEncryptionKey`, nor outside `appEnv: dev` without an
`https://` public URL — `urls.publicBase`, or the ingress host when the
ingress is enabled. Each refusal names the value and says why.

`urls.publicBase` is `PUBLIC_BASE_URL`: the issuer of every token the
server signs, and the URL a registered service is deployed with, beside
its own public URL. The ingress routes `/.well-known` to the API so the
public signing keys are served there. How a service registers and is
approved is in [docs/service-identity.md](../../docs/service-identity.md).

There is no secret to share with a service, and so nothing for this
chart to hold for one. A service is deployed with two addresses — this
server's URL and its own public URL — registers itself on first start,
and serves nobody until a super admin approves it on the portal's
*Services* page.

This is deliberate. `docker-compose.prod.yml` used to default its image
to `ember/ember-api:latest`, which is an unqualified name and therefore
Docker Hub — a namespace that is not ours and that answers 401, so it
could not even be established who owned it. A missing value that stops
the install is a five-minute problem. A plausible wrong default is an
incident.

It also refuses `image.tag=latest`. `latest` cannot be rolled back to,
and a tag that never changes gives Kubernetes no reason to restart pods
on upgrade — so the upgrade succeeds and changes nothing.

## Secrets

Two ways, and the first is better:

```bash
kubectl -n igni create secret generic igni-secrets \
  --from-literal=SECRET_ENCRYPTION_KEY="$ENCRYPTION_KEY" \
  --from-literal=POSTGRES_PASSWORD=...

helm install igni ./charts/igni --set secrets.existingSecret=igni-secrets ...
```

With `secrets.existingSecret` set, the chart creates no Secret of its
own and nothing sensitive is in your values file, in the Helm release
object stored in the cluster, or in the CI job that ran the install.

The keys it looks for, all optional except the first:

| Key | What happens without it |
|---|---|
| `SECRET_ENCRYPTION_KEY` | The server refuses to start, in every environment. It keeps its token-signing key encrypted under this one |
| `POSTGRES_PASSWORD` | Only needed if your Postgres wants one |
| `SMTP_PASSWORD` | SMTP with authentication |
| `SUPER_ADMIN_PASSWORD_HASH` | No break-glass into deployment-wide settings. Super admins are accounts; this is for an install where nobody can sign in. A bcrypt hash, never a password |

**Rotating `SECRET_ENCRYPTION_KEY`.** Both keys work at once, so a
rotation is an upgrade rather than an outage:

```bash
NEW_KEY=$(python3 -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')

# The old key keeps reading, the new one starts writing.
helm upgrade igni ./charts/igni -f my-values.yaml \
  --set secrets.secretEncryptionKey="$NEW_KEY" \
  --set secrets.secretEncryptionKeysPrevious="<the previous key>"

# Re-encrypt what is already stored, in the shared database and every
# organisation's own. Reports before it writes; safe to re-run if interrupted.
kubectl -n igni exec deploy/igni-api -- python tools/rotate_secret_key.py
kubectl -n igni exec deploy/igni-api -- python tools/rotate_secret_key.py --apply

# Then drop the old key — at least ten minutes after the first upgrade, so
# nothing sealed in Redis under it is still live. Until you do, it still
# opens everything.
helm upgrade igni ./charts/igni -f my-values.yaml \
  --set secrets.secretEncryptionKey="$NEW_KEY" \
  --set secrets.secretEncryptionKeysPrevious=""
```

The last step is the one people skip, and the API warns about it in its
startup log on every boot until it is done.

**Losing** the key — as opposed to rotating it — leaves every secret it
encrypted unreadable, and each has to be set again. That includes the
token-signing key: delete the rows in `signing_keys` and restart, and
the server generates a new one, ending every session. Back the key up
separately from the database: a backup containing both defeats the
encryption.

**Rotating the signing key** needs no chart change. From a checkout of
the repository, with this deployment's Postgres settings and
`SECRET_ENCRYPTION_KEY` in the environment, `python
tools/rotate_signing_key.py --stage` adds a key that is published at
once and signs ten minutes later, so every replica and every service
has it first. The old key keeps verifying what it signed for 31 days.

## Upgrades

```bash
helm upgrade igni ./charts/igni -f my-values.yaml --set image.tag=2026.10.01-def5678
```

Alembic runs as a `pre-upgrade` hook and the upgrade does not proceed if
it fails. Without that ordering the API comes up alongside the migration
and serves against whichever schema happens to exist at that moment.

If a migration fails, its Job and logs stay in the cluster on purpose:

```bash
kubectl -n igni logs job/igni-migrate
```

Migrations in this project are tested for a downgrade round trip against
populated data, but a downgrade is still a database operation on your
data. Take the backup first.

### Upgrade notes

Changes to defaults that an upgrade picks up without a values change:

- **`backup.image` is a map, and no longer Docker Hub.** It was the string
  `postgres:17.6-alpine`, which resolves to Docker Hub. It is now
  `repository` (empty: beside `image.repository`) and `tag`
  (`17.6-alpine`). A string you set is still used as a full reference.
  If your cluster pulled Postgres from Docker Hub, mirror it beside the
  API's image or set `backup.image.repository`.
- **Google sign-in is off unless it has a client ID.** `socialSignIn.providers`
  defaulted to `[google]`; it is no longer set, and the chart lists Google
  in `OAUTH_PROVIDERS` only when `socialSignIn.googleClientId` is set. A
  deployment with Google configured is unchanged. One without it rendered
  `["google"]` and offered no button (the server hides a provider with no
  credentials); it now renders `[]`. An explicit `socialSignIn.providers`
  still wins. The sign-up page, like the sign-in page, now shows only the
  buttons the server reports at `/v1/auth/methods`.

## What is exposed

With `ingress.enabled=true`, one host serves both:

* `/v1` → the API. This is what the CLI talks to.
* everything else → the portal.

`/docs`, `/redoc` and `/openapi.json` are served by the API to anything
inside the cluster and are **not** routed by the Ingress. Publishing your
API documentation is a separate decision from deploying the product.

## Services have their own hostname

A service is served on **its own hostname**, by its own deployment, never
as a path on igni's; this chart deploys none. A service's URL is its
identity ([docs/service-identity.md](../../docs/service-identity.md)): it
is the audience of every token a person gets for the service, and the
base igni calls (`{service URL}/v1/igni/subjects/...`) to erase or export
a person's data. A path on igni's host would share igni's origin, so a
token's audience would no longer say which of the two it is for, and
every path the service serves would be one igni can never use.

With `networkPolicy.enabled`, the API's egress must reach each service's
hostname (a `networkPolicy.egress` entry): igni calls it for erasure and
export. A service that calls the API in-cluster needs allowing in
`networkPolicy.apiIngressFrom`.

## Hardening

Every pod runs as a numeric non-root user (10001 for the backend, 101 for
the portal), with a read-only root filesystem, no privilege escalation
and all capabilities dropped. The one writable path is explicit: `/tmp`,
for the temporary files Python and its libraries expect to be able to
create, bounded with a `sizeLimit` so a runaway writer cannot fill a node
and evict somebody else's pods.

`networkPolicy.enabled` is off by default. Read it before switching it
on: in a cluster whose CNI does not enforce NetworkPolicy it does nothing
while looking like a control, and in one that does, an empty
`networkPolicy.egress` gives you a working deployment of nothing. When on,
every pod of the release is denied both ways; the ingress controller you
name in `networkPolicy.ingressFrom` (required — the render fails without
it) reaches the API and portal, the portal reaches the API, DNS is open on
53, and each `networkPolicy.egress` entry opens one destination on its own
ports. `egressCidrs` still works, on every port. See
`templates/networkpolicy.yaml`.

## Retention

`retention.auditDays` defaults to `0`, which means keep forever, and the
retention runner does not start at all while it is 0. Defensible only if
you can defend it — GDPR Art. 5(1)(e) asks for a limit rather than a
preference. See `docs/COMPLIANCE_TRACKER.md` for what it maps to.

## Backups

```bash
kubectl -n igni create -f my-backup-pvc.yaml   # you choose the storage

helm upgrade igni ./charts/igni -f my-values.yaml \
  --set backup.enabled=true --set backup.existingClaim=igni-backups
```

A nightly `pg_dump` (03:00 UTC by default) into the claim you name,
running the same script the compose stack runs. It runs Postgres's own
image from beside `image.repository`: `registry.example.com/igni/igni-api`
gives `registry.example.com/igni/postgres:17.6-alpine`, which every offline
bundle carries. `backup.image.repository` and `backup.image.tag` name
another one; match the tag to your server's major version. Every organisation has
its own database on the server (`<POSTGRES_DB>_t<n>`, registered in the
shared plane's `tenants` table), so a run is **one archive per
organisation plus one for the shared plane**:

```
igni-20261024T030000Z/
  MANIFEST                         kind, org_id, tenant number, state, database, archive
  shared-igni.dump                 the shared plane: accounts, organisations, the tenants registry,
                                   registered services, the extension bundles they uploaded
                                   and the assets beside them (Postgres large objects)
  org-<org_id>-igni_t1.dump        one organisation's own database
  org-<org_id>-igni_t2.dump        detached (deleted) organisations too — their data is kept
```

Extension assets — the JREs, databases and models an extension ships —
are Postgres large objects in the shared plane, and a whole-database
`pg_dump` includes large objects, so the shared archive holds them and
`pg_restore` brings them back. igni unlinks an asset's object when the
asset goes, so `vacuumlo` is never needed. They make the shared archive
the large one: a few hundred megabytes per service that ships assets.

One archive per organisation so isolation holds in the backups as well:
an organisation's archive never contains another's rows, one
organisation can be restored without rolling back the rest, and a
deleted organisation's archive can be handed over, kept or destroyed on
its own schedule. The tenant template (`<POSTGRES_DB>_tenant_template`)
is never backed up; the migrate job rebuilds it.

Every archive is verified before it counts — `pg_restore --list` must
parse it *and* find a table its schema has — and the run counts only if
every archive in it did. Until then the directory is
`igni-<stamp>.part/`; one organisation that cannot be dumped fails the
Job, and nothing older is pruned. Runs older than `backup.retainDays`
are pruned only after a new one is complete. `tools/restore-drill.sh`
(run in CI) restores the shared archive and two organisations' archives
and compares them with the source.

### Restoring one organisation

Find the organisation's line in the run's `MANIFEST`: it names the
archive and the database (`igni_t3` below). Restore beside the live
database, then swap the names — nothing else on the server is touched,
and the shared plane's `tenants` row already points at that name. Run
this anywhere with the Postgres client tools and the deployment's
database credentials (the backup image is one):

```bash
export PGHOST=… PGUSER=… PGPASSWORD=…
psql -d postgres -c 'CREATE DATABASE "igni_t3_restore"'
pg_restore -d igni_t3_restore --exit-on-error igni-<stamp>/org-<org_id>-igni_t3.dump

psql -d postgres \
  -c 'ALTER DATABASE "igni_t3" WITH ALLOW_CONNECTIONS false' \
  -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = 'igni_t3'" \
  -c 'ALTER DATABASE "igni_t3" RENAME TO "igni_t3_replaced"' \
  -c 'ALTER DATABASE "igni_t3_restore" RENAME TO "igni_t3"'
```

The API's pools reconnect on the next request. Drop `igni_t3_replaced`
once you are satisfied. If the archive is from before a release that
added a tenant migration, run the migrate job again
(`python -m app.db.sql.tenant_migrations`) to bring it to head.

To rebuild a whole server, restore `shared-<db>.dump` into `POSTGRES_DB`
first, then each organisation's archive into the database name its
`MANIFEST` line gives, then run the migrate job, which recreates the
template.

`existingClaim` is required rather than provisioned, and that is the
point: a chart that creates a volume in whatever the default
StorageClass happens to be will put your backups on the database's own
disk in some clusters and on network storage in others, and you find out
which when you need one.

Nothing uploads them anywhere. That is deliberate — an `aws s3 cp` in
the backup path of an air-gapped product is a contradiction — so the last
mile is your volume snapshots or your own cron. Leave `backup.enabled`
off entirely if your cluster or managed database already does this; two
backup systems each assuming the other is authoritative is its own
failure mode.

## Uninstall

```bash
helm uninstall igni -n igni
```

Your database is untouched, because it was never ours. Nothing in this
chart deletes data.

## Verified how

`helm lint`, `helm template` with the values a customer would pass, the
rendered manifests validated against the published Kubernetes schemas,
and 44 assertions in `tests/test_the_helm_chart_deploys_igni.py` covering
the things that render perfectly and are still wrong — a liveness probe
on the wrong path, a Service selector matching no pod, a hook that reads
a ConfigMap which does not exist yet.

**Not** verified against a live cluster. There was none available when
this was written, so the chart has never been installed. That is the one
claim this file will not make; see §49 in `docs/REVIEW_FINDINGS.md`.
