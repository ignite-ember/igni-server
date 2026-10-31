# igni server — architecture

## What this service is

A FastAPI application that owns **identity, access and inference
routing** for a self-hosted deployment, and the admin portal's API.

It does not analyse code and it does not connect repositories.
Repository integration (GitHub, GitLab, webhooks, repositories) belongs
to CodeIndex (igni__code-index).

**It owns:**

- users, organisations, memberships, roles, invitations, and the email
  domains an organisation claims
- sign-in — email, Google and OIDC — and SCIM provisioning
- each organisation's own database, and the registry that routes to it
- the audit trail, retention windows and the GDPR subject-access and
  erasure paths
- the inference node registry, and lease-based routing to whichever
  OpenAI-compatible endpoints a customer has registered
- the admin portal's entire API
- the identity every other service trusts: registration, tokens, the
  directory a service reads people from, and the extension each service
  ships to people's machines — `docs/service-identity.md`

**It does not own:** documents, which live in `igni__artifacts`, or code
search, which lives in `igni__code-index`. Each is deployed with igni's
URL and its own public URL, registers itself, and works only once a super
admin has approved it on the portal's *Services* page. Nothing secret is
shared between igni and either of them.

## How this document went wrong, and what is done about it

It described a Celery worker, a `VectorBridge` storage layer with a
Weaviate backend, a tasks package under `app/` holding the worker's
entry points, five-phase code analysis running in this process, and an
environment table in which most rows named a variable the application
does not read.

(That sentence names the deleted package without quoting its path,
because the rule below reads this file too — and a rule that let an
explanation of a dead path quote the dead path would let anything quote
it. The rule stays; the sentence bends.)

Every one of those was true once. Nothing checked any of them, so each
became false silently: the pipeline moved to `igni__code-index`,
VectorBridge was dropped, DEC-13 removed the worker, and the settings
were renamed. A document whose overview is wrong is worse than no
document, because a reader has no way to tell which sentence to trust.

**So the mechanically checkable claims are now checked.**
`tests/test_the_docs_name_real_things.py` asserts that every repository
path and every environment variable this file (and every other document)
quotes actually exists. That does not make the prose true — a paragraph
can be wrong while every path in it resolves — but it removes the class
of rot that produced the version this replaced.

Treat a claim made only here as a claim. Where something is asserted by
a test, the test is named.

---

## Project structure

```
igni-server/
├── app/
│   ├── api/
│   │   ├── base_v1/
│   │   │   ├── endpoints/
│   │   │   │   ├── portal/       # The admin portal's API
│   │   │   │   ├── client/       # Sign-in, chat, notifications, node registration
│   │   │   │   └── identity/     # Services: registration, tokens, directory, extensions
│   │   │   └── schema/           # Pydantic models
│   │   └── scim/                 # SCIM 2.0 provisioning
│   ├── services/                 # Business logic, one package per area
│   ├── db/sql/                   # Models, query functions, tenancy
│   ├── security/                 # Startup refusals, permissions, service tokens
│   ├── middlewares/              # Audit, metrics, request context
│   ├── monitoring/               # /metrics and its registry
│   ├── observability/            # JSON log formatting
│   ├── config.py                 # Settings
│   └── main.py                   # The application
├── alembic/                      # Migrations: the shared plane
├── alembic_tenant/               # Migrations: each organisation's own database
├── charts/igni/                  # The customer-facing Helm chart
├── containers/                   # Dockerfiles and compose files
├── marketplace/                  # GCP listing artefacts
├── portal/                       # React admin portal
├── tools/                        # Document generators, drills, kind
└── tests/
```

`app/services/` is the layer worth knowing: `auth`, `ai`, `portal`,
`scim`, `notifications`, `email`, `secrets`, `identity`,
`subject_access.py`, `retention.py`, `readiness.py`.

---

## Core components

### Access control

`app/security/`. A request resolves to an account, and an
organisation-scoped one to an active membership and its role in that
organisation, on every request — so deactivating a membership takes
effect on the next one. Deployment-wide settings belong to super
admins, who are accounts (`app/security/super_admin.py`).

### Each organisation's own database

`app/db/sql/tenancy.py`. What has to know about every organisation —
accounts, organisations, memberships, the inference fleet and the
registry of tenants — lives in the shared database. Everything that
belongs to one organisation lives in that organisation's own logical
database, `<db>_t<number>`, so a session opened for one organisation
cannot reach another's rows even by a query that forgot to filter. The
two are migrated separately: `alembic upgrade head` for the shared
plane, then `python -m app.db.sql.tenant_migrations` for every
organisation's database.

### Sessions and revocation

`app/services/auth/`. Tokens are RS256 JWTs with a 30-day lifetime
(DEC-15 — kept deliberately, not inherited), signed with a key the
server generates on first start and keeps in Postgres encrypted under
`SECRET_ENCRYPTION_KEY` (`app/services/identity/`). The public half is
published at `/.well-known/jwks.json`, which is how services check igni's
tokens without sharing a secret — see
[service-identity.md](service-identity.md). `python
tools/rotate_signing_key.py --stage` rotates it. A `tokens_valid_after`
watermark on the user row makes them revocable while the account exists.
The watermark comes from the **database** clock, because two replicas
disagreeing by a second was enough to mint a token that survived its own
revocation.

### Inference routing

`app/services/ai/`. GPU nodes register themselves at runtime and carry
their own `base_url` and key — which is why no model endpoint appears in
the settings table below. Concurrency leases live in Redis so the ceiling
holds across replicas; drains let a node finish what it started.

### The audit trail

`app/middlewares/audit.py` and the `audit_events` table, which is
append-only via a database trigger rather than by convention. Bounded by
`AUDIT_RETENTION_DAYS`.

### Retention

`app/services/retention.py`. Runs on an asyncio task inside the API — no
scheduler process — taking a Postgres advisory lock so exactly one
replica prunes per round. This is the only periodic work the product has,
and it is the reason DEC-13 could remove the worker without replacing it.

---

## Database schema

The shared plane: `audit_events`, `inference_nodes`, `org_domains`,
`org_invitations`, `org_memberships`, `organizations`,
`registered_clients`, `service_extensions`, `signing_keys`, `tenants`,
`user_roles`, `users`. `service_extensions` holds each approved service's
extension bundle — the archive itself, up to 400 MiB — so a shared-plane
backup carries the bundles too.

Each organisation's own database: `audit_events`, `chat_usage`,
`notifications`, `org_groups`, `org_group_entries`,
`org_group_members`.

What personal data each holds, why, and for how long is in
`docs/RECORDS_OF_PROCESSING.md` — which is generated from the schema
classification, so unlike this section it cannot drift.

**The migration will not invent a database.** Outside `APP_ENV=dev`,
`alembic upgrade head` refuses when the target database does not exist.
It used to create it, which turned a misspelled `POSTGRES_DB` or a lost
volume into a stack that started cleanly, passed every health check and
contained nothing.

---

## API surface

`/docs` and `/openapi.json` are authoritative and are served to anything
inside the cluster — deliberately not routed by the Ingress or by Caddy,
because publishing your API documentation is a separate decision from
deploying the product.

| Prefix | Who calls it | Auth |
|---|---|---|
| `/v1/portal/*` | The admin portal | Session token |
| `/v1/sign-in`, `/v1/auth/*`, `/v1/cli/*` | Sign-in, for the portal, the CLI and the desktop app | None, or the identity provider's callback |
| `/v1/chat/*`, `/v1/notifications/*` | The CLI and desktop app | Session token |
| `/.well-known/openid-configuration`, `/.well-known/jwks.json` | Services checking igni's tokens | None — the public half of the signing keys |
| `/v1/clients`, `/v1/oauth/token` | A service or app registering, then getting tokens | None to register (it grants nothing until a super admin approves); `private_key_jwt` for a token |
| `/v1/directory/*`, `/v1/service-notifications` | An approved service, for its token's organisation only | Service token. See [service-identity.md](service-identity.md) |
| `/v1/clients/{client_id}/extension` | A service uploading the extension it ships (up to 400 MiB, the one route above the usual body limit), or asking which version igni holds | `PUT`: a signature over the bundle by the service's registered key. `GET`: none — it answers only an id, a version and a digest |
| `/v1/extensions` | igni on a person's machine, fetching the approved services' extensions | Session token |
| `/v1/nodes/*` | Whatever starts and stops the customer's GPU nodes | `NODE_REGISTRATION_TOKEN`; **404** when unset |
| `/scim/v2/*` | The customer's identity provider | `SCIM_TOKEN`; 503 for everything when unset |
| `/health` | Liveness | None |
| `/ready` | Readiness — checks Postgres and Redis | None |
| `/metrics` | Prometheus | `METRICS_TOKEN`; **404** when unset, not 401 |

`/metrics` answering 404 rather than 401 when disabled is deliberate: an
unconfigured deployment should not advertise that the endpoint is there.

---

## Configuration

Every variable below is a real field on `Settings`, asserted by
`tests/test_the_docs_name_real_things.py`. `dist.env` is the full
operator-facing list with the reasoning behind each one; this is the
subset needed to run the thing.

| Variable | Description |
|----------|-------------|
| `APP_ENV` | `dev` relaxes refusals that would otherwise block startup |
| `POSTGRES_HOST` | Database host |
| `POSTGRES_PORT` | Database port |
| `POSTGRES_DB` | Database name |
| `POSTGRES_USER` | Database user |
| `POSTGRES_PASSWORD` | Database password, if yours wants one |
| `REDIS_HOST` | Concurrency leases and one-time sign-in codes |
| `REDIS_PORT` | Redis port |
| `SECRET_ENCRYPTION_KEY` | Fernet key for the secrets the deployment stores in its own database, the token-signing key first among them. Required in every environment; the server refuses to start without a valid one |
| `PUBLIC_BASE_URL` | This server's external address: the `iss` of every token, and the URL a service is deployed with, beside its own. `https://` outside dev |
| `NODE_REGISTRATION_TOKEN` | Shared secret GPU nodes register with |
| `METRICS_TOKEN` | Bearer token for `/metrics` |
| `AUDIT_RETENTION_DAYS` | Retention window for the audit trail |

### Key files

| File | Purpose |
|------|---------|
| `pyproject.toml` | Dependencies and metadata |
| `app/config.py` | `Settings`, and the reasoning for each default |
| `dist.env` | The operator-facing template |
| `app/security/startup_checks.py` | What the server refuses to start without |
| `alembic/versions/` | Migrations: the shared plane |
| `app/db/sql/tenancy.py` | Each organisation's own database |

---

## Testing

```bash
make test            # or: .venv/bin/python -m pytest tests/ -q
make lint            # black --check, ruff, mypy
```

Postgres on 5432 (the suite creates and migrates `ember_test` itself) and
Redis on 6379. Concurrent runs are refused rather than allowed to
interfere, so run one at a time.

The suite is unusual in shape and it is worth knowing why: most files
assert a *rule* rather than a list, and many exist because a claim in a
document turned out to be false. `tests/test_the_front_door_is_true.py`
checks the README's links, make targets and route paths;
`tests/test_the_docs_name_real_things.py` checks every document's paths
and settings names; the nine `tools/generate_*.py` documents each have a
test asserting the committed file matches its generator.

Migrations are round-trip tested against populated data
(`tests/test_migration_round_trip.py`) — a downgrade that only works on
an empty schema is not a downgrade.

---

## Deployment

Three shapes, all self-hosted:

- **compose** — `containers/docker-compose.prod.yml`. Migration, API,
  portal, and optionally bundled Postgres and Redis under the `bundled`
  profile.
- **Helm** — `charts/igni/`. API, portal, and the migration as a
  `pre-upgrade` hook that must succeed before anything serves. It refuses
  to render without the images, the datastore hosts, an encryption key
  and the server's public URL, each refusal naming what is missing.
- **marketplace** — `marketplace/`, GCP listing artefacts.

Nothing calls out to the vendor from any of them, asserted by
`tests/test_nothing_phones_home.py`.

---

## Development

```bash
git clone https://github.com/ignite-ember/igni-server && cd igni-server
uv sync --group dev

createdb -h localhost -p 5432 -U myuser igni
make migrate
make run
```

`make run` serves on `127.0.0.1:7777` with reload. That is the whole
stack — there is no second process to start.

---

## Monitoring

`/metrics` in Prometheus exposition, with a private registry so the
output is what `app/monitoring/metrics.py` declares and nothing a
dependency happened to register on import. Alert rules ship with the
chart at `charts/igni/files/alerts.yaml`.

Logs are structured JSON on stdout; the fixed field list and the reason
for each field are in `docs/observability.md`.

```bash
docker compose -f containers/docker-compose.local.yml logs -f igni_api
```

---

## Where to look next

| Question | Document |
|---|---|
| How Artifacts, CodeIndex and any later service trust igni, and what crosses | `docs/service-identity.md` |
| What personal data is held, and for how long | `docs/RECORDS_OF_PROCESSING.md` |
| What the technical controls do and do not evidence | `docs/COMPLIANCE.md` |
| What is still open, and who decides it | `docs/DECISIONS.md` |
| What the pre-production review found | `docs/REVIEW_FINDINGS.md` |
| How the chart behaves, and what it has not been tested against | `charts/igni/README.md` |
