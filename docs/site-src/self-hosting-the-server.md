The igni server is the backend half of a self-hosted install: a FastAPI service that holds accounts, organisations and the list of inference nodes, plus an admin portal. You run it yourself — there is no hosted deployment to point at. This page covers what needs it, what you have to bring, and the two shapes it ships in: a Helm chart for Kubernetes and a Docker Compose stack for a single host. It is open source under Apache-2.0, at [github.com/ignite-ember/igni-server](https://github.com/ignite-ember/igni-server).

## What needs a server

The CLI finds the server through one setting, `api_url` in `~/.igni/config.yaml`. It has no default on purpose, and operations that need it refuse by name rather than failing somewhere inside an HTTP client:

```yaml
# ~/.igni/config.yaml
api_url: https://igni.your-company.example
```

Signing in and listing models go through that URL. The model catalogue comes from `GET /v1/chat/models` and completions are proxied by the server, so the client never holds an upstream provider URL. Paths that *can* work without a server — local-only models — check whether the setting is configured and skip quietly instead of erroring.

The portal is the other consumer: team, groups, access review, audit and usage all live under `/v1/portal`.

## What you must bring

| Dependency | Required? | Default / note |
|---|---|---|
| PostgreSQL | yes | Not deployed by the chart. `postgres.host` has no default and rendering fails without it |
| Redis | yes | Not deployed by the chart. `redis.host` likewise; `redis.db` defaults to `10` |
| SMTP or SES | no | `email.enabled: false`. Without a relay, sign-in codes reach nobody unless you turn on `email.logSignInCodes` |

The chart bundles no database subchart deliberately: a bundled store is a second thing to patch and back up, and a chart dependency has to be fetched over a network an air-gapped install does not have.

Without a relay, sign-in and password-reset codes are sent nowhere: the log records that a code was issued and not delivered, but not the code. For a first setup before email works, set `LOG_SIGN_IN_CODES=true` (`email.logSignInCodes` in the chart) and the codes go to the application log for an operator to pass on. While it is on, anyone who can read your logs can sign in as anybody, so the server says so at every start, in the same log line as every code, and in a banner super admins see in the portal. Turn it off once email works.

## Getting the images

Two images, `igni-api` and `igni-portal`. Three ways to get them:

- **Pull a release** from `ghcr.io/ignite-ember/igni-api` and `ghcr.io/ignite-ember/igni-portal`. These images are **public**: anyone can pull them with no registry login (running the server still needs a licence). Both are tagged with the release version (`0.8.0`), with the commit they were built from, and with a moving `:latest` that points at `0.8.0`. Release tags are unprefixed — there are no `dev` or `stage` images, because 0.7.0 removed them. Mirror the images into your own registry if your cluster should not pull from the internet.

  ```bash
  docker pull ghcr.io/ignite-ember/igni-api:0.8.0
  docker pull ghcr.io/ignite-ember/igni-portal:0.8.0   # :latest resolves to 0.8.0 too
  ```

- **Import them from a release's signed offline bundle**, which carries them with their signatures and SBOMs — see [Offline updates](/docs/server/offline-updates). The bundle is private (it carries the paid modules' images); on a network with no route out, it is the only way.
- **Build them yourself.**

Either way the chart has no default repository and `fail()`s rather than guessing: an unqualified name would resolve to Docker Hub and pull somebody else's, and an air-gapped cluster has to name its own registry.

```bash
# from the repository root
docker build -f containers/be/Dockerfile  -t registry.example.com/igni/igni-api:0.8.0 .
docker build -f portal/Dockerfile         -t registry.example.com/igni/igni-portal:0.8.0 portal/
```

Note the build contexts differ: the API builds from the repository root, the portal from its own directory.

Push both to a registry your cluster can reach, then give the chart those names. **Tag with a version, never `latest`** — the chart refuses `latest` outright, because a `latest` tag cannot be rolled back to and an unchanged tag gives Kubernetes no reason to roll pods on upgrade.

The embedder that document search uses is not one of them. It belongs to the documents service and is built and deployed from the Artifacts module's own repository — see [Running the documents service](/docs/server/running-the-documents-service). This chart deploys no embedder and no documents service: there is no `embedder`, `embedderImage` or `artifacts` value in it. It used to deploy `<release>-embedder` and set `EMBEDDER_URL`; `helm upgrade` deletes that Deployment and Service, so a documents service that pointed `EMBEDDER_URL` there needs the Artifacts embedder deployed first.

## Deploying with Helm

The chart is version 0.2.0 and deploys application 0.8.0. The two move independently: a customer taking a fixed chart template need not move the image.

These have no default and the chart calls `fail()` naming each: the image repositories, `postgres.host`, `redis.host`, `secrets.secretEncryptionKey` (a Fernet key), and — outside `appEnv: dev` — an `https://` `urls.publicBase`, which the Ingress host supplies when the Ingress is on. Mirror the images into your own registry first — an unqualified repository name resolves to Docker Hub.

There is no signing key to supply. The server generates its own on first start, keeps it in Postgres encrypted under `SECRET_ENCRYPTION_KEY`, and publishes the public half at `/.well-known/jwks.json` — which is why that key is required in every environment, and why it must be backed up apart from the database.

```bash
helm install igni ./charts/igni \
  --namespace igni --create-namespace \
  --set image.repository=registry.example.com/igni/igni-api \
  --set portalImage.repository=registry.example.com/igni/igni-portal \
  --set postgres.host=pg.internal \
  --set redis.host=redis.internal \
  --set urls.publicBase=https://igni.example.com \
  --set urls.portalBase=https://igni.example.com \
  --set secrets.secretEncryptionKey="$(python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')"
```

Tags default to `.Chart.AppVersion`, currently `0.8.0`. A tag of `latest` is refused outright — it cannot be rolled back to, and an unchanged tag gives Kubernetes no reason to roll pods.

**Both images answer to the version the chart defaults to.** The release workflow pushes `igni-api` and `igni-portal` under the commit they were built from, under the release version (`0.8.0`) and under a moving `:latest`, so the default tags resolve in the registry without a `--set`. The offline bundle carries the same version tag, so an imported install needs nothing either.

The values that matter:

| Value | Default | What it does |
|---|---|---|
| `appEnv` | `prod` | `dev` downgrades the startup safety checks to warnings — all but the encryption key, which is required everywhere |
| `urls.publicBase` | `""` | `PUBLIC_BASE_URL`: the issuer of every token and the URL services are deployed with. Empty with the Ingress on means `https://<ingress.host>` |
| `logFormat` | `text` | `json` emits one object per line for a log shipper |
| `api.replicaCount` | `2` | |
| `api.terminationGracePeriodSeconds` | `""` | Derived as `ai.upstreamTimeout + 30` — 330s by default |
| `ai.upstreamTimeout` | `300` | Seconds the API waits for a model to answer |
| `postgres.poolSize` / `maxOverflow` | `5` / `15` | Per-**process** ceiling on concurrent queries |
| `postgres.poolTimeout` | `10` | Seconds a request waits for a free connection |
| `service.apiPort` / `portalPort` | `7777` / `3000` | |
| `ingress.enabled` | `false` | `/v1` and `/.well-known` to the API, everything else to the portal |
| `licence.existingSecret` | `""` | A Secret holding the paid modules' licence under the key `licence.json`, mounted read-only at `/etc/igni/licence/` in the API so the portal can show it. The base needs none. See [Licensing and evaluations](/docs/server/licensing-and-evaluations) |

The chart sets no ingress annotations (`ingress.annotations` defaults to `{}`), which matters for one thing in particular: the API accepts request bodies up to **32 MiB** (`MAX_REQUEST_BODY_BYTES`), and most ingress controllers cap them far lower — nginx at 1 MiB by default. A long chat context or a large changeset then gets a 413 from a component the server never sees, so nothing in its logs explains it. Raise the controller's limit to match:

```yaml
ingress:
  annotations:
    nginx.ingress.kubernetes.io/proxy-body-size: "32m"
```

Two routes are larger, and both are a service publishing what it ships. A service publishes its extension bundle to `PUT /v1/clients/{client_id}/extension` — every dependency as wheels for three platforms — and the API accepts up to **400 MiB** there. It publishes each asset to `PUT /v1/clients/{client_id}/extension/assets/{sha256}` and accepts up to **1 GiB** there. Every other route on the server is held to 32 MiB, and the server refuses it itself, so raising the controller's limit does not raise the server's. If services reach the server through this Ingress, the controller has to admit the largest of them, which is what the chart's own values file recommends — `"1024m"`, with buffering off and long timeouts:

```yaml
ingress:
  annotations:
    nginx.ingress.kubernetes.io/proxy-body-size: "1024m"
    nginx.ingress.kubernetes.io/proxy-request-buffering: "off"
    nginx.ingress.kubernetes.io/proxy-read-timeout: "3600"
    nginx.ingress.kubernetes.io/proxy-send-timeout: "3600"
```

The shipped Caddyfile allows 400 MiB on the extension upload and 32 MB everywhere else. The portal's own nginx caps `/v1` at 32 MB, so an upload that goes through the portal is refused at 32 MB; the chart's Ingress sends `/v1` straight to the API, which is where the larger limits apply.

**TLS on the Ingress.** The chart terminates TLS at the Ingress when you turn it on: set `ingress.tls.enabled: true` and `ingress.tls.secretName` to the Secret that holds the certificate for `ingress.host`. The certificate itself can be one you load into that Secret, or one cert-manager issues — point a `cert-manager.io/cluster-issuer` (or `cert-manager.io/issuer`) annotation at your issuer under `ingress.annotations` and cert-manager fills the Secret in. This is the Kubernetes equivalent of the Caddy automatic-HTTPS and the Compose TLS overlay below.

```yaml
ingress:
  enabled: true
  host: igni.example.com
  annotations:
    cert-manager.io/cluster-issuer: letsencrypt
  tls:
    enabled: true
    secretName: igni-tls
```

| `retention.auditDays` | `0` | 0 keeps forever; the retention runner does not start |
| `monitoring.serviceMonitor.enabled` | `false` | Needs the Prometheus Operator CRDs |
| `backup.enabled` | `false` | Requires `backup.existingClaim` when on |
| `networkPolicy.enabled` | `false` | Deny-by-default both ways; needs `networkPolicy.ingressFrom` when on |

Two defaults that bite. First, the pool: `poolSize + maxOverflow` is 20 per process, and two API replicas is 40 — fine against a default `max_connections` of 100, fatal against Cloud SQL's smallest tier, which allows 25 in total. A startup check logs both numbers side by side and warns when two processes would not fit. Second, the grace period is derived rather than fixed, so raising `ai.upstreamTimeout` raises it with you; set it explicitly only if you mean to override that.

Secrets can come from the chart (`secrets.create: true`) or from one you created (`secrets.existingSecret`). The second is better — a value in a values file is also in the Helm release object and in whatever CI ran the install. The keys the chart writes are `SECRET_ENCRYPTION_KEY`, `SECRET_ENCRYPTION_KEYS_PREVIOUS`, `POSTGRES_PASSWORD`, `SMTP_PASSWORD`, `SUPER_ADMIN_PASSWORD_HASH`, `METRICS_TOKEN`, `OIDC_CLIENT_SECRET`, `OAUTH_GOOGLE_CLIENT_SECRET`, `SCIM_TOKEN`. Empty ones are omitted rather than written as `""`, because an unset `SECRET_ENCRYPTION_KEY` means "refuse to store provider credentials" while a present-but-empty one would be taken as a key.

`SCIM_TOKEN` and `METRICS_TOKEN` both fail closed: without them every `/scim/v2` route answers 503, and `GET /metrics` answers 404. There is no secret for the documents service or any other service: each registers its own public key and is approved on the portal's *Services* page, so the Secret has nothing to hold for them.

### Before you enable the NetworkPolicy

`networkPolicy.enabled` renders a policy with no rules over every pod of the release — API, portal, migrate Job, backup CronJob — so both directions are denied, and then one policy per hole:

- **Ingress to the API** (7777) from the portal pods and your ingress controller; **to the portal** (3000) from the controller only. Name the controller with `networkPolicy.ingressFrom.namespaceSelector` (a label map, e.g. `kubernetes.io/metadata.name: ingress-nginx`) and optionally `podSelector`. The render fails if it is empty, rather than admitting every namespace.
- **Prometheus**, if you scrape `/metrics`: `networkPolicy.metricsFrom`. Required when `monitoring.serviceMonitor.enabled` is on.
- **Services that call the API in-cluster** — a service deployed beside the server, at `http://<release>-api.<ns>.svc:<port>` — go in `networkPolicy.apiIngressFrom`, as NetworkPolicy peers.
- **DNS**: port 53, UDP and TCP, to any destination. It is not pinned to the cluster resolver by default because CoreDNS's labels differ between distributions and NodeLocal DNSCache answers on a link-local address; set `networkPolicy.dns.to` if you know yours.
- **The portal's egress** is the API pods on 7777, and nothing else.
- **Everything else the server reaches** is `networkPolicy.egress`: one entry per destination, `{name, cidrs, ports: [{port, protocol}]}`, applied to the API, migrate and backup pods unless `components` narrows it. Empty by default — so with the policy on and nothing listed, the server can resolve names and reach nothing. The migrate Job and the backup both need Postgres; the backup's volume is mounted by the node, so it needs nothing more. `egressCidrs` still works but opens every port to each CIDR; prefer `egress`.

```yaml
networkPolicy:
  enabled: true
  ingressFrom:
    namespaceSelector: {kubernetes.io/metadata.name: ingress-nginx}
  egress:
    - {name: postgres, cidrs: [10.0.10.5/32], ports: [{port: 5432}]}
    - {name: redis, cidrs: [10.0.10.6/32], ports: [{port: 6379}], components: [api]}
    - {name: idp, cidrs: [10.0.20.0/28], ports: [{port: 443}], components: [api]}
    - {name: smtp, cidrs: [10.0.30.4/32], ports: [{port: 587}], components: [api]}
    - {name: inference, cidrs: [10.0.40.0/24], ports: [{port: 8000}, {port: 443}], components: [api]}
```

None of it does anything on a CNI that does not enforce NetworkPolicy; confirm yours does before you count it as a control. The migrate Job is a Helm hook and runs under the previous release's policies, so add a new Postgres address in one upgrade and move to it in the next.

The destinations people forget:

- your **OIDC issuer**, whose discovery document and JWKS are fetched on every SSO sign-in;
- your **SMTP relay or SES endpoint**, or invitations stop sending silently;
- the **inference nodes** themselves;
- every **service you approve** — Artifacts, CodeIndex — at the URL it registered with. The server calls each one when an organisation admin runs an erasure or a subject-access export; blocked, the erasure answers 502 and the export comes back marked truncated;
- your **OpenTelemetry collector**, if you set `OTEL_EXPORTER_OTLP_ENDPOINT` ([Server configuration](/docs/server/server-configuration)). Blocked, traces are dropped and nothing else breaks.

Both failures land on your users rather than on you: a sign-in page whose SSO button does nothing, and invitations that report success and never arrive.

### Monitoring is three switches, not one

`monitoring.serviceMonitor.enabled` is the one in the table above, and the chart ships two more. `monitoring.prometheusRule.enabled` renders ten alerts across three groups — an audit write failing in `igni.audit`; a dependency down, a rate of server errors, and replicas disagreeing about their version in `igni.availability`; and retention not running, retention never having run, retention failing, email codes throttled, the connection pool exhausted and clock skew in `igni.compliance`. `monitoring.dashboard.enabled` renders a Grafana dashboard as a ConfigMap; it needs no CRD, so it is the one that is harmless to leave on.

Writing your own alerts for failures the chart already covers is the common outcome of not knowing these exist.

Traces are a fourth, off by default and set outside the chart's values: `OTEL_EXPORTER_OTLP_ENDPOINT` (and the other standard `OTEL_*` variables) through `extraEnv`, pointing at your own collector. There is no default endpoint, and request bodies are never exported. See [Server configuration](/docs/server/server-configuration).

### One host, or the browser blocks you

The API's CORS allowlist is empty and there is no setting for it. That works because the portal's nginx proxies `/v1` same-origin and the chart's Ingress puts both on a single `host`. **Serve the API from a different hostname than the portal and every portal request fails in the browser** — with nothing in the server log, because the request never arrives.

### Services find the server by one URL

`PUBLIC_BASE_URL` (`urls.publicBase`) is the server's external address. Every token it signs carries it as `iss`, and a service such as CodeIndex or Artifacts is deployed with two addresses: that URL, and its own public URL. Nothing is configured on the server for the service at all. From the server's URL it reads `/.well-known/openid-configuration` and the public keys at `/.well-known/jwks.json`, so whatever fronts the server must route `/.well-known` to the API as well as `/v1` — the chart's Ingress and the shipped Caddyfile both do; a proxy of your own has to as well.

Nothing secret is shared with a service. On first start it registers its own public key at `POST /v1/clients`, uploads the extension it ships to people's machines, and logs its key's thumbprint. A super admin opens the portal's *Services* page, compares the thumbprint, reads what the extension would install, and approves it. From then on the service gets organisation-scoped tokens from `/v1/oauth/token`, reads people from the directory, and its extension is offered to every signed-in machine. Revoking it on the same page stops all of that at once. The contract is `docs/service-identity.md` in the server repository.

### Every service has its own hostname

A service — Artifacts, CodeIndex, anything later — is served on a hostname of its own, such as `https://artifacts.example.com`, and never as a path on the server's (`https://igni.example.com/v1/artifacts`). Its URL is its identity: the address it registers as, the **audience** of every token a person gets for it, and where the server calls `{service URL}/v1/igni/subjects/...` for an erasure or a subject-access export. Under the server's host it would share the server's origin, so a token's audience would no longer say which of the two it is for, and every path the service served would be one the server could never use.

So each service needs its own DNS name and its own certificate, and its `PUBLIC_BASE_URL` is exactly `https://<that name>` — reachable from the server as well as from people's machines.

**This chart deploys no service.** It used to carry an `artifacts` block that published the documents service on a second Ingress of its own, with a ConfigMap beside it; 0.7.0 removed the block, and the chart now renders one host — the server's. A service is deployed from its own repository, on a host of its own, and registers itself with the server. The single-instance Caddyfile routes only the server; a service run beside it gets a site block of its own for its own domain. How to stand the documents service up is in [Running the documents service](/docs/server/running-the-documents-service).

To rotate the signing key, run `python tools/rotate_signing_key.py --stage` from a checkout of the server repository with the deployment's Postgres settings and `SECRET_ENCRYPTION_KEY` in its environment. The new key is published at once and signs ten minutes later, so every replica and every service has it before it is used; nothing restarts.


## Migrations

Alembic, always. On Kubernetes it is a `pre-install,pre-upgrade` hook at weight `-5` running `alembic upgrade head`; the release does not proceed if it fails, and the delete policy is `before-hook-creation` so a failed Job and its logs stay in the cluster for you to read:

```bash
kubectl -n igni logs job/igni-migrate
```

The hook reads its own small ConfigMap and Secret (`<release>-migrate-env`) carrying only `APP_ENV`, `LOG_LEVEL` and the Postgres coordinates — not the encryption key, not any sign-in secret. That is not tidiness: Helm creates hooks before the release's own objects, so a hook reading the main ConfigMap cannot start on a first install. The hook has its own ServiceAccount for the same reason.

On compose, `migrate` is a service that waits up to 120 seconds for Postgres and then migrates; the API, portal and backup all wait on `condition: service_completed_successfully`.

Alembic will **not** create a missing database outside `dev`. It exits telling you to `createdb` by hand or fix `POSTGRES_DB`/`POSTGRES_HOST` — because `postgres_db` defaults to `igni`, and a typo there would otherwise produce a freshly migrated empty database that passes every health check.

Locally: `make migrate`, and `make downgrade-one` for `alembic downgrade -1`.

## Upgrading, and what a rollback does not undo

An upgrade is the same command with the new release's tag. The chart's own version moves independently of the application's: 0.7.0 shipped chart 0.2.0, which dropped the `artifacts` block. On a network with no route out, import the release's images from its signed bundle first — see [Offline updates](/docs/server/offline-updates):

```bash
helm upgrade igni ./charts/igni --namespace igni --reuse-values \
  --set image.tag=0.8.0 --set portalImage.tag=0.8.0
```

The migration hook runs **before** the new pods, at weight `-5`, and the release stops if it fails — so a bad migration leaves the old version running against an unmigrated database rather than a new version against a broken one.

`helm rollback` moves the pods back. **It does not move the schema.** Nothing runs `alembic downgrade`, so the old image comes up against a database that is one or more revisions ahead of it, and whether that works depends entirely on whether the migration was additive. Two consequences worth planning for:

- **Take a backup before upgrading**, not after. It is the only thing that undoes a destructive migration.
- **Read the migrations in the release before applying it.** `alembic history` between your current revision and `head` tells you whether a rollback is survivable. Any migration that drops or renames a column is not.


## Restoring a backup

Every organisation has its own database, and every backup run keeps them apart: one directory per run, holding an archive for the shared plane, one archive per organisation (detached ones included), and a `MANIFEST` naming which archive is which. The shared plane's archive includes the registered services and the extension bundles they uploaded — each up to 400 MiB — so expect it to grow by roughly the size of every approved service's bundle.

```
igni-<UTC stamp>/
  MANIFEST
  shared-igni.dump
  org-<org_id>-igni_t3.dump
```

Each archive is **`pg_dump --format=custom`**, which is the only format `pg_restore` can read selectively — and which means `psql < file` will not work on it. They are taken by the database's own image, so the `pg_dump` version always matches the server they came from.

**Restoring one organisation** leaves every other organisation, and the shared plane, alone. Look up its database name in the run's `MANIFEST` (say `igni_t3`), restore beside it, then swap:

```bash
psql -c 'CREATE DATABASE "igni_t3_restore"'
pg_restore --exit-on-error --no-owner -d igni_t3_restore org-<org_id>-igni_t3.dump
psql -c 'ALTER DATABASE "igni_t3" WITH ALLOW_CONNECTIONS false'
psql -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = 'igni_t3'"
psql -c 'ALTER DATABASE "igni_t3" RENAME TO "igni_t3_replaced"'
psql -c 'ALTER DATABASE "igni_t3_restore" RENAME TO "igni_t3"'
```

**Rebuilding a whole server**: restore the shared archive first, then each organisation into the database name its `MANIFEST` line gives, then run the migrate job, which also recreates the tenant template.

A run counts only when every archive in it passed: each is listed with `pg_restore --list` and checked for a canary table — `audit_events` by default, overridable with `IGNI_BACKUP_CANARY_TABLE` and `IGNI_BACKUP_TENANT_CANARY_TABLE` — because `pg_dump` exits 0 in more than one failure mode that produces an unusable file. One failed organisation fails the run.

**Prove it works before you need it.** `tools/restore-drill.sh` runs a real backup, restores the shared archive and an organisation's archive into throwaway databases and checks them, which is the only way to learn that a migration, an image change or a new extension broke your restore path on a day when it does not matter.


## Deploying with Docker Compose

`containers/docker-compose.prod.yml` requires three variables and refuses to start without them, naming each:

```bash
cd containers
IGNI_ENV_FILE=.env.prod \
IGNI_IMAGE=registry.example.com/igni/igni-api:0.8.0 \
IGNI_PORTAL_IMAGE=registry.example.com/igni/igni-portal:0.8.0 \
docker compose -f docker-compose.prod.yml --profile bundled up -d
```

`IGNI_ENV_FILE` is required rather than defaulting to `.env` specifically so a leftover development env file cannot be deployed: `APP_ENV` is read from the same file as the check that would catch it, so naming the file is what breaks the loop. Start from `dist.env` in the repository root, which ships `APP_ENV=prod` and empty credentials, and copy it to `containers/.env.prod` to fill in.

The `bundled` profile adds Postgres (`postgres:17.6-alpine`) and Redis (`redis:7.4-alpine`) on the same host; point `POSTGRES_HOST=postgres` and `REDIS_HOST=redis` at them. Omit the profile and both are external. Nothing in the file `depends_on` a profiled service — that would make the other shape refuse to load entirely.

The API and portal publish on `127.0.0.1` by default (`IGNI_API_BIND`, `IGNI_PORTAL_BIND`), so a reverse proxy fronts them rather than the container being the edge.

For TLS, add the overlay — a separate file rather than a profile, because compose interpolates variables in every service before deciding which profiles are active:

```bash
IGNI_DOMAIN=igni.example.com IGNI_ACME_EMAIL=ops@example.com \
docker compose -f docker-compose.prod.yml -f docker-compose.tls.yml --profile bundled up -d
```

Caddy is the only service that binds every interface, routes `/v1/*` and `/.well-known/*` to `igni_api:7777` and the rest to the portal, and deliberately does not get the application env file. It needs DNS pointing at the host and port 80 reachable before first start.

A nightly `pg_dump` is available under the `backup` profile, writing to `./backups` unless you set `IGNI_BACKUP_DIR`. Point that at durable storage somewhere else — a backup on the same disk as the database survives a dropped table and not a lost instance.

Validate any change to these files with `make check-configs`, which parses the Caddyfile and renders all three compose combinations.

## After it starts

The startup checks warn rather than refuse for most things, and the warnings are the useful part:

```bash
kubectl -n igni logs deploy/igni-api | grep 'Deployment check'
```

Fatal everywhere, `dev` included: a missing or invalid `SECRET_ENCRYPTION_KEY`. Fatal outside `dev`: a `PUBLIC_BASE_URL` that is not `https://`, `PORTAL_AUTH_BYPASS` enabled, a non-HTTPS `OIDC_ISSUER`, and a database still carrying the development bypass's admin rows. Warned: no mail relay, localhost still in `PORTAL_BASE_URL` or the OAuth URLs, a half-configured OIDC, an unfinished key rotation, a `NODE_REGISTRATION_TOKEN` shorter than 32 characters, and a break-glass key that can never work.

Liveness probes `/health`, which checks nothing deliberately; readiness probes `/ready`, which does a `SELECT 1` and a `PING` and returns 503 when a dependency is genuinely unavailable — but treats a merely saturated pool as `busy` and stays ready, since pulling a working replica out of a load balancer is the one action guaranteed to make a capacity problem worse. Both are mounted at the root, and so is `/version`, which answers the version that replica is running: `/health`, `/ready` and `/version` are not under `/v1`, so a probe does not move when the API version does.

The portal opens on its own dashboard: `/` on your install goes to `/dashboard`, and somebody signed out is sent on to sign in. Ignite Ember's public site is not part of a self-hosted install.

`/docs`, `/redoc` and `/openapi.json` are served only when `APP_ENV=dev`, and the chart's ingress does not route them in any case.

## The local stack

For development, `make up` builds and runs `containers/docker-compose.local.yml` on loopback with `APP_ENV=dev` and `PORTAL_AUTH_BYPASS=1`. That bypass writes a real administrator row with an empty password, which outlives the flag — never point it at a database you intend to keep.

## Where to go next

The server being up is the halfway point. In order:

1. **[Server configuration reference](/docs/server/server-configuration)** — every setting, and which ones are safe to leave empty.
2. **[Connecting your GPUs](/docs/server/inference-backends)** — until a node is registered there is nothing for the agent to talk to.
3. **[Administering your organisation](/docs/server/administering-your-organisation)** — invite your developers, decide how they sign in, and ship them a standard set of agents and skills. Publishing the discovery record there is what turns onboarding into "install it and type your work email".
4. **[Running the documents service](/docs/server/running-the-documents-service)** — the optional deployment, if you want it.
5. **[Licensing and evaluations](/docs/server/licensing-and-evaluations)** — if you run any paid module, its licence file.
