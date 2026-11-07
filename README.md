# igni server

[![OpenSSF Scorecard](https://api.scorecard.dev/projects/github.com/ignite-ember/igni-server/badge)](https://scorecard.dev/viewer/?uri=github.com/ignite-ember/igni-server)

**igni server is the free, Apache-2.0 base of igni**, the agentic platform
that regulated organisations run on their own infrastructure: air-gapped
on their own hardware, or in a private cloud on GPUs they rent. It owns
identity and access (accounts, organisations, sign-in, SCIM
provisioning), routes inference to the GPU nodes a customer registers,
keeps the audit trail, and serves the admin portal and its API.

Everything in this repository is free to use, change and redistribute
under Apache-2.0. The paid modules (CodeIndex, Artifacts and Fleet, plus
the model Factory) are separate products in their own repositories. They
plug into igni server through the published service-identity and
extension interfaces described below, and need a licence file from
Ignite Ember.

igni itself is three repositories, also Apache-2.0: the backend and CLI in
[igni-core](https://github.com/ignite-ember/igni-core), the desktop app and
web UI in [igni-app](https://github.com/ignite-ember/igni-app), and the SDKs
in [igni-sdk](https://github.com/ignite-ember/igni-sdk). Repository integration (GitHub, GitLab, webhooks) belongs to
CodeIndex.

**Ignite Ember hosts nothing.** There is no hosted tier and no
multi-tenant service: you run igni on your own hardware or in your own
cloud account — see [self-hosting.md](docs/self-hosting.md), which is an
honest accounting of what still assumes infrastructure we operate.

## Features

- **Sign-in** — email, Google and OIDC, plus SCIM provisioning from the
  customer's identity provider
- **Organisations** — memberships, roles, invitations, claimed email
  domains, and a database of its own for each organisation
- **Inference routing** — the OpenAI-compatible endpoints a customer
  registers, with concurrency leases that hold across replicas
- **Audit and data protection** — an append-only audit trail, retention
  windows, subject access and erasure
- **The admin portal** — its API, and the React app itself (`portal/`)

## Documentation

| Document | Description |
|----------|-------------|
| [Architecture](docs/ARCHITECTURE.md) | System architecture and data flow |
| [Self-hosting](docs/self-hosting.md) | What still assumes our infrastructure, and the plan |
| [Service identity](docs/service-identity.md) | How a service such as CodeIndex or Artifacts registers, is approved, gets tokens, checks igni's, and ships its extension through igni |
| [GPU clusters](docs/gpu-clusters.md) | Runtime inference-node registration |
| [GPU metrics](docs/gpu-metrics.md) | What the nodes report and how it is read |
| [Review findings](docs/REVIEW_FINDINGS.md) | Index of the pre-production review: every finding the code and docs cite as `Fnn`, and the tests that guard it |
| [Compliance](docs/COMPLIANCE.md) | SOC 2 / ISO 27001 / GDPR — what the technical controls evidence, and what they do not |
| [Record of processing](docs/RECORDS_OF_PROCESSING.md) | GDPR Article 30 — every activity, what personal data it holds and for how long. Generated from the schema classification, so it cannot drift |
| [Compliance tracker](docs/COMPLIANCE_TRACKER.md) | The control register: status, evidence, owner, review date |
| [Portal manual QA](docs/PORTAL_MANUAL_QA.md) | Manual test script for the admin portal |
| [Contributing](CONTRIBUTING.md) | How to send a change, and the Developer Certificate of Origin |
| [Security](SECURITY.md) | How to report a vulnerability |

## Quick start

### Prerequisites

- Python 3.13 — `pyproject.toml` pins `==3.13.*`, and the container image
  builds on `python:3.13-slim`
- PostgreSQL, with a role that has `CREATEDB` — each organisation gets a
  database of its own, created the first time it is needed
- Redis

Or only Docker: [Docker Compose](#docker-compose) below runs all of it,
Postgres and Redis included.

### Installation

```bash
git clone https://github.com/ignite-ember/igni-server
cd igni-server

uv venv
source .venv/bin/activate
uv sync

# A development .env: dist.env with APP_ENV=dev, Postgres and Redis on
# localhost, a fresh SECRET_ENCRYPTION_KEY, the API and portal URLs, and
# one-time sign-in codes written to the server log. PG_PORT=, REDIS_PORT=,
# PORT= (the API) and PORTAL_PORT= when yours differ. For a real deployment,
# copy dist.env and go through it instead — see "Environment" below.
make dev-env

# Postgres and Redis, if you do not have them (any way you like; this is one):
docker run -d --name igni-pg -e POSTGRES_USER=myuser -e POSTGRES_PASSWORD=mysecretpassword \
  -p 127.0.0.1:5432:5432 postgres:17
docker run -d --name igni-redis -p 127.0.0.1:6379:6379 redis:7

# Create the database. The migration refuses to create one for you
# outside dev, deliberately: see "Data you own" below.
PGPASSWORD=mysecretpassword createdb -h localhost -p 5432 -U myuser igni

make migrate
make run              # the API, on 127.0.0.1:7777 (PORT=…)
make portal-dev       # in a second terminal: npm ci in portal/ first; the portal, on :3000
```

`make run` serves the API with reload; `make portal-dev` serves the portal
and proxies its API calls to the API on the same `PORT`. There is nothing
else to start: the periodic work this product does — retention sweeps — runs on
its own asyncio task inside the API process, which is what DEC-13 settled.

### The first sign-in

A new deployment has no accounts, no organisation and no model.

1. **The first account is a super admin.** Open the portal
   (`http://localhost:3000`), sign up, and read the code from the API's log —
   with no mail relay, `LOG_SIGN_IN_CODES=true` (which `make dev-env` sets)
   writes it there: `sign-up code for you@example.com is 123456`. An address
   on a special-use domain (`.test`, `.localhost`, `.local`, `.invalid`) is refused;
   `you@acme.example.com` is fine.
2. **Create an organisation** — the portal offers it, or
   `POST /v1/portal/organizations` with `{"display_name": …, "domains": [...]}`.
   Organisations are created, not given: nobody is in one until then.
3. **Add a model.** On the portal's inference page, add the
   OpenAI-compatible endpoint you serve (vLLM, or anything that speaks
   `/v1/chat/completions`), or set `INFERENCE_BACKENDS` in `.env`, or have the
   node register itself against `/v1/nodes` ([gpu-clusters.md](docs/gpu-clusters.md)).
4. **Point igni at it.** In `~/.igni/config.yaml` on the developer's machine:
   `api_url: http://localhost:7777` and `portal_url: http://localhost:3000`,
   then `igni login` (see igni's QUICKSTART); `igni -m "…"` then uses the
   models this server routes to.

Turn `LOG_SIGN_IN_CODES` off once a mail relay or SSO works: anyone who can
read the log can sign in as anybody.

### Tests

```bash
make test            # or: .venv/bin/python -m pytest tests/ -q
make lint            # black --check, ruff, mypy
```

Tests need Postgres (a role `myuser` / `mysecretpassword` that may create
databases; they create and migrate `ember_test` themselves) and Redis, and run
with `APP_ENV=dev` and a `SECRET_ENCRYPTION_KEY` of their own, as CI does.
On other ports than 5432 and 6379:

```bash
IGNI_TEST_PG_PORT=56701 REDIS_HOST=127.0.0.1 REDIS_PORT=57701 APP_ENV=dev \
SECRET_ENCRYPTION_KEY="$(python3 -c 'import base64,os;print(base64.urlsafe_b64encode(os.urandom(32)).decode())')" \
  .venv/bin/python -m pytest tests/ -q
```

## Docker Compose

Local:

```bash
make up              # make down to stop it
```

A second stack beside one already running — another checkout, a demo — needs
its own name and ports, or it takes over the first one's containers:
`make up IGNI_STACK=igni2 IGNI_API_PORT=7787 IGNI_PORTAL_PORT=3010` (and the
same variables for `make down`).

Starts Postgres and Redis, the migration runner, the API (`:7777`) and
the portal (`:3000`) — both ports bound to `127.0.0.1`; Postgres and
Redis are not published, the stack reaches them by name. Docker is all
it needs. It reads the same `.env` as the Quick start, at the repository
root, when there is one — without it, the compose file's development
defaults (`make up` is
`cd containers && docker compose -f docker-compose.local.yml --env-file ../.env up --build -d`).
The database and Redis are always the bundled ones: that `.env`
describes a server run on the host, where they are `localhost`. Source
is volume-mounted for reload and `PORTAL_AUTH_BYPASS=1` is set. That is
the whole open server; nothing in it needs a paid module.

If you also have the Artifacts module's source (a separate, licensed
repository) checked out beside this one, as `../igni__artifacts`, add
`containers/docker-compose.artifacts.yml` (or run `make up ARTIFACTS=1`).
It builds and runs Artifacts on **its own name**,
`http://artifacts.localhost:8100`, not as a path on `:7777`.

Every service has its own hostname, here and in production
(`artifacts.example.com`, never `igni.example.com/v1/artifacts`). A
service's URL is its identity: the audience of the tokens people get for
it, and where igni calls it to erase or export a person's data — see
[service-identity.md](docs/service-identity.md#a-service-has-its-own-hostname).
`*.localhost` is loopback on the laptop with no hosts entry (browsers,
curl, macOS; on Linux add `127.0.0.1 artifacts.localhost` to your hosts file
if yours does not resolve it), and a network alias gives the same name to
the container inside the stack, so igni reaches the service at exactly the
URL it registered. Its settings — including a **dev-only**
`GRAPH_CREDENTIALS_KEY` — are in that compose file, each overridable from
`.env` as `ARTIFACTS_<NAME>`. In the chart, the service's host is
`artifacts.host`; see [charts/igni/README.md](charts/igni/README.md).

Production:

```bash
IGNI_IMAGE=<registry>/igni-api:<explicit-tag> \
IGNI_PORTAL_IMAGE=<registry>/portal:<explicit-tag> \
IGNI_ENV_FILE=.env.prod \
docker compose -f docker-compose.prod.yml up -d
```

**Upgrading: pass `--remove-orphans`.**

```bash
docker compose -f docker-compose.prod.yml up -d --remove-orphans
```

Compose only manages services the current file declares. A release that
*removes* one leaves the running container alone — `up` will not
recreate it and `down` will not stop it, so it keeps running against
your database and your Redis until somebody notices it by hand.

This is not hypothetical: DEC-13 removed the Celery worker and Flower,
and on the machine this was written, both were still up and serving days
later, Flower still serving the broker-URL page on loopback. Nothing warned,
because every mechanism that would have — the compose service, the
startup check for its password, the settings key — went with the
release.

That expects Postgres and Redis to exist already. For one instance
serving one customer — the pre-marketplace shape — run them alongside,
and terminate TLS in front:

```bash
IGNI_DOMAIN=igni.example.com IGNI_ACME_EMAIL=ops@example.com \
docker compose -f docker-compose.prod.yml -f docker-compose.tls.yml \
  --profile bundled up -d
```

Set `POSTGRES_HOST=postgres` and `REDIS_HOST=redis` in the env file for
that shape. TLS is a separate file rather than a profile because compose
interpolates every service before choosing profiles, so a required
variable in an inactive profile refuses the whole project.

Add `--profile backup` for a nightly `pg_dump`, verified before it counts
and pruned only after a success:

```bash
IGNI_BACKUP_DIR=/mnt/backups docker compose -f docker-compose.prod.yml \
  --profile bundled --profile backup up -d
```

Each run is one directory, `igni-<UTC stamp>/`, with an archive for the
shared plane, one per organisation's own database (detached
organisations included) and a `MANIFEST` naming which is whose, so one
organisation can be restored without rolling back the others — see
[charts/igni/README.md](charts/igni/README.md#restoring-one-organisation)
for how; the compose service runs the same script. A run counts only if
every archive in it verified.

It works with a managed database too — it reads `POSTGRES_HOST` from the
same env file. Point `IGNI_BACKUP_DIR` at storage that outlives the
instance: a dump on the same disk as the database survives a dropped
table and not a lost volume. The dumps are mode 600 and as sensitive as
the database. The Helm chart does the same thing as a CronJob, off until
you name the volume.

Both variables are required rather than defaulted, and the env file is
named explicitly so a leftover development `.env` cannot be deployed. The
API binds to loopback unless you set `IGNI_API_BIND=0.0.0.0`; put a
reverse proxy in front of it. Postgres and Redis are external, and this
file has no portal service — the compose stack is the API and the
migration.

Kubernetes:

```bash
helm install igni ./charts/igni -f my-values.yaml
```

Google Cloud Marketplace listing artefacts — the deployer parameters,
the deployer image and the Application object — are in
[marketplace/](marketplace/), including what a person still has to do
that a repository cannot.

The chart is the customer-facing artefact: API, portal, and the
migration as a hook that must succeed before anything serves. It refuses
to render without the images, the Postgres and Redis hosts, an
encryption key and the server's public URL, each refusal naming what is
missing. See
[charts/igni/README.md](charts/igni/README.md) — including what it has
*not* been verified against, which is a live cluster.

## Data you own

Everything durable lives outside the containers, which is deliberate but
means backups are yours to arrange:

| State | Where | If you lose it |
|---|---|---|
| Users, orgs, memberships, groups, usage, notifications | **PostgreSQL** — the shared database, and one per organisation (`<db>_t<n>`) on the same server | Unrecoverable. Back up every one of them, not only the shared one. |
| Concurrency leases, one-time sign-in codes | **Redis**. No code, token or API key is stored there as given: high-entropy codes as their SHA-256, values the server must hand back sealed under `SECRET_ENCRYPTION_KEY` | Survivable — leases expire and rebuild, codes are reissued. This row used to carry a caveat about the moment a task queue mattered; DEC-13 removed the queue, so the caveat is gone rather than merely unmet. |
| Audit trail | **PostgreSQL** (`audit_events`) | Unrecoverable, and the record of who did what. Append-only; bounded by `AUDIT_RETENTION_DAYS`. Back this up with the rest of the database. |
| Token-signing keys | PostgreSQL (`signing_keys`), the private half Fernet-encrypted under `SECRET_ENCRYPTION_KEY`; the public half is served at `/.well-known/jwks.json` | Survivable: delete the rows and restart, and the server generates a new key. Every session ends and everybody signs in again. Rotate on purpose with `python tools/rotate_signing_key.py --stage`, which publishes the new key ten minutes before it signs so nothing is interrupted. |
| Registered services and the extensions they ship | PostgreSQL, the shared database (`registered_clients`, `service_extensions`). Each bundle is stored whole — up to 400 MiB — so the shared database's backup grows by every approved service's bundle | Survivable, with a super admin's help: each service registers and uploads its extension again on its next start, and waits as pending until it is approved again. Until then it can get no token and its extension is not offered to anybody. |
| Secrets the deployment stores | PostgreSQL, Fernet-encrypted under `SECRET_ENCRYPTION_KEY` | The rows are useless without the key. Rotation is supported: put the old key in `SECRET_ENCRYPTION_KEYS_PREVIOUS`, run `python tools/rotate_secret_key.py --apply`, then clear it. **Losing** the key without a rotation leaves those secrets unreadable, and each has to be set again. Back up the key separately from the database — a backup containing both defeats the encryption. |

Nothing else in the stack keeps state. The prod compose file declares
`pgdata` and `redisdata`, and both are inert unless you run the `bundled`
profile — under which they hold the two rows above and are the thing to
back up.

This sentence read "exactly one volume and it is Flower's" until DEC-13,
and it had already been wrong for longer than that: the bundled profile
added the two real ones and nobody came back to the paragraph. Which is
why `tests/test_the_front_door_is_true.py` now derives the list from the
compose file instead of trusting it.

**The migration will not invent a database for you.** Outside
`APP_ENV=dev`, `alembic upgrade head` refuses when the target database
does not exist, rather than creating it. It used to create it, which
turned a misspelled or unset `POSTGRES_DB` (it defaults to `igni`), a
replacement host, or a lost volume into a stack that started cleanly,
passed every health check and contained nothing.

## Environment

`dist.env` is the template; copy it to `.env` and edit. The variables that
matter most:

| Variable | Description |
|----------|-------------|
| `APP_ENV` | `dev` relaxes the startup safety checks. Anything else enforces them. |
| `SECRET_ENCRYPTION_KEY` | **Required everywhere, dev included.** A Fernet key; the server refuses to start without a valid one. The server generates its own token-signing key on first start and keeps it in Postgres encrypted under this — there is no signing secret to set. |
| `PUBLIC_BASE_URL` | This server's external address: the `iss` of every token, and the URL services such as CodeIndex and Artifacts are deployed with, beside their own public URL. Must be `https://` outside dev. See [service-identity.md](docs/service-identity.md). |
| `POSTGRES_HOST` / `POSTGRES_PORT` / `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` | Database. `POSTGRES_DB` defaults to `igni`. |
| `REDIS_HOST` / `REDIS_PORT` | Concurrency leases and one-time sign-in codes |
| `LOG_LEVEL` | Default `INFO` |
| `LOG_FORMAT` | `text` (default) or `json`. JSON is one object per line for a log shipper — see [observability.md](docs/observability.md) |
| `METRICS_TOKEN` | Bearer token for `GET /metrics` (Prometheus text format). Empty means the endpoint answers 404. Alert rules: `charts/igni/files/alerts.yaml` |
| `PORTAL_BASE_URL` | Where invite and OAuth links point. Defaults to `http://localhost:3000`. |
| `EMAIL_ENABLED` | Off by default. Every flow has a defined no-email path: invite links come back to the admin, and one-time codes go nowhere unless `LOG_SIGN_IN_CODES` is on. |
| `LOG_SIGN_IN_CODES` | Off by default. With no relay, writes one-time codes to the server log for an operator to hand on — the way in for first setup with no email. Anyone who can read the logs can then sign in as anybody, so the server warns at startup, on every code and to super admins in the portal; turn it off once a relay or SSO works. |
| `EMAIL_BACKEND` | `smtp` or `ses`. See self-hosting.md §6 for relay-specific detail. |
| `SMTP_HOST` / `SMTP_PORT` / `SMTP_USERNAME` / `SMTP_PASSWORD` / `SMTP_TLS_MODE` | Your relay |
| `EMAIL_FROM_ADDRESS` / `EMAIL_FROM_NAME` | Sender identity |
| `AUDIT_RETENTION_DAYS` | Delete audit events older than this. `0` (default) keeps everything. |
| `MCP_GATEWAY_URL` | MCP gateway |
| `PORTAL_AUTH_BYPASS` | Development only; refused outside `APP_ENV=dev` |

`dist.env` is the authoritative list — this table is the subset worth
calling out, not a substitute for reading it.

## API

`/docs` and `/openapi.json` are the authoritative list (168 paths at the
time of writing; this section used to say 85, and its per-prefix counts had
drifted the same way). The shape of it:

| Prefix | What lives there |
|---|---|
| `/v1/portal/*` | Everything the admin portal calls |
| `/v1/auth/*`, `/v1/sign-in{,-with-code}`, `/v1/sign-up`, `/v1/send-*-code`, `/v1/reset-password`, `/v1/cli/*` | Authentication |
| `/.well-known/*`, `/v1/clients`, `/v1/oauth/*`, `/v1/directory/*`, `/v1/revocations`, `/v1/service-*`, `/v1/extensions` | Services such as Artifacts and CodeIndex: registration, tokens, the directory, notifications, and the extensions they ship — see [service-identity.md](docs/service-identity.md) |
| `/v1/platform/*`, `/v1/apps/*`, `/v1/vm/*` | What a service's producers and their VMs pull and report |
| `/v1/nodes/*` | Inference-node registration and heartbeat |
| `/v1/chat/*` | The live inference path |
| `/v1/me/*`, `/v1/notifications/*`, `/v1/organisations/*` | A person's own view: notifications, the organisations served |
| `/scim/v2/*` | Provisioning from the customer's identity provider |
| `/health`, `/ready`, `/version` | Operations |

`/health` is deliberately unconditional — it decides whether to restart
the container, and a restart does not fix an unreachable database.
`/ready` round-trips Postgres and Redis and answers 503 when either is
down, so it is the one to point a load balancer at.

## Releases

`.github/workflows/ci.yml` runs on every pull request, with read-only
permissions and no secrets, so it is safe for pull requests from forks:
lint (black, ruff, mypy), a security audit (pip-audit, bandit), the Python
suite, and the portal (tsc, npm audit, vitest, Playwright).

`.github/workflows/cd.yml` runs the same suite on every push to `main`,
and builds nothing.

`.github/workflows/release.yml` makes a release. It runs only when the
private repository `ignite-ember/igni__release` calls it with a tag or a
commit on `main`: the offline bundle carries the paid module images, and a
public repository's CI artefacts can be downloaded by anyone. It builds
two images, `ghcr.io/ignite-ember/igni-api` and
`ghcr.io/ignite-ember/igni-portal`, pushes them to GitHub Container
Registry, signs them with cosign, attaches a CycloneDX SBOM, and seals the
minisign-signed offline bundle. It deploys nothing.
[RELEASING.md](docs/RELEASING.md) is how a release is cut, and
[OFFLINE_UPDATES.md](docs/OFFLINE_UPDATES.md) explains how to verify an
image or a bundle.

## Development

Before opening a PR: `make lint` and `make test`. `pre-commit install`
sets up the hooks.

```bash
make migrate                                   # shared plane, then every organisation's database
make downgrade-one                             # back one revision
uv run alembic revision -m "description"       # new migration
```

Migrations are round-trip tested against a populated database
(`tests/test_migration_round_trip.py`) — a downgrade that only works on an
empty schema is not a downgrade.

## Tech stack

| Component | Technology |
|-----------|-----------|
| **API** | Python 3.13, FastAPI, SQLAlchemy (async), asyncpg |
| **Portal** | React 19, TypeScript, Vite, Primer React |
| **Monitoring** | Prometheus `/metrics`, scraped by the customer |
| **Database** | PostgreSQL |
| **Inference** | OpenAI-compatible endpoints; self-hosted vLLM is the target |

There is no worker row any more. It read "Celery + gevent (100
concurrency)", then "a custom AsyncIO pool at 200" once the pool was
written — two corrections to a description of a process serving a queue
nothing ever wrote to. DEC-13 removed it rather than correcting it a
third time.

## Project structure

```
igni-server/
├── app/                      # Backend application
│   ├── api/                  # FastAPI routes
│   ├── config.py             # Settings
│   ├── db/sql/               # Models, queries, and each organisation's database
│   ├── middlewares/          # Audit, metrics, body-size limit, timing
│   ├── monitoring/           # /metrics and its registry
│   ├── observability/        # JSON log formatting
│   ├── security/             # Startup safety checks, permissions
│   └── services/             # Business logic
├── alembic/                  # Migrations: the shared plane
├── alembic_tenant/           # Migrations: each organisation's own database
├── charts/igni/              # The customer-facing Helm chart
├── containers/
│   ├── be/Dockerfile         # Backend image
│   └── docker-compose.*.yml
├── portal/                   # React admin frontend
├── docs/
├── tests/                    # pytest suite
└── .github/workflows/        # ci.yml, cd.yml, release.yml
```

## Licence

Apache-2.0 — see [LICENSE](LICENSE) and [NOTICE](NOTICE). That covers the
whole of this repository: the server, the admin portal, SCIM provisioning,
SSO, the audit trail and its export, the Helm chart, the compose files and
the offline update bundle tooling. None of it needs a licence file.

The paid modules — CodeIndex, Artifacts, Fleet and the model Factory —
are not in this repository. They are separate products under a commercial
licence, run as services beside igni server, and check a licence file of
their own (see [licence-file.md](docs/licence-file.md)). The code in this
repository that talks to them — service registration, extension grants,
the licence verifier — is Apache-2.0 like the rest, so anybody can build a
service of their own against it.

Contributions are accepted under Apache-2.0 with a Developer Certificate
of Origin sign-off; see [CONTRIBUTING.md](CONTRIBUTING.md).
