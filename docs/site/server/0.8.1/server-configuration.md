Everything the igni server reads at startup comes from one place: the `Settings` class in `app/config.py` — with two exceptions. `LOG_LEVEL` and `LOG_FORMAT` are read straight from the process environment by `app/main.py` at import, so they must arrive as real environment variables; set only in a `.env` file they reach pydantic and change nothing. The OpenTelemetry variables (`OTEL_*`, below) are read from the process environment only, for the same reason and on purpose. This page lists those settings by subject, gives the real default, and says what happens when one is left empty — because for a lot of them, empty is the supported answer. Read it if you are standing up a deployment, or trying to work out why a feature you configured is answering 404.

## How settings reach the process

`Settings` is a pydantic-settings model instantiated once at import (`settings = Settings()`). Each field maps to an environment variable of the same name in upper case — `postgres_host` is `POSTGRES_HOST` — matched case-insensitively. A `.env` file in the working directory is read if present.

Two things to know about the loader:

- Unknown keys are ignored (`extra: 'ignore'`). A typo in an env file is silent, not an error.
- The nested `ai` group uses a double underscore: `AI__UPSTREAM_TIMEOUT=300`.

`oauth_providers` is a list, so it wants JSON: `OAUTH_PROVIDERS=["google"]`. Writing `OAUTH_PROVIDERS=google` is a startup crash, not a one-element list.

On Kubernetes, the chart writes every non-secret setting into one ConfigMap and the secrets into a separate Secret. In compose, `containers/docker-compose.prod.yml` refuses to start unless `IGNI_ENV_FILE` names the file explicitly — precisely so a leftover development `.env` with `APP_ENV=dev` cannot be deployed by accident.

## What stops the server

`app/security/startup_checks.py` runs inside the FastAPI lifespan, before anything else, and deliberately outside a `try`. Findings are graded by `app_env`: anything that is not `dev` is treated as serving real users, and fatal findings raise `UnsafeDeployment`.

Fatal in every environment, `dev` included:

| Condition | Why |
|---|---|
| `SECRET_ENCRYPTION_KEY` unset, or not a valid Fernet key | The server signs its tokens with an RSA key it generates on first start and keeps in Postgres encrypted under this key. Without it there is nowhere safe to keep the signing key, so nobody can sign in. There is no insecure fallback to allow in dev. |

Fatal outside `dev`:

| Condition | Why |
|---|---|
| `PUBLIC_BASE_URL` not `https://` | It is the issuer of every token, and services fetch the public signing keys from it. Over plain HTTP those keys can be replaced in transit. |
| `PORTAL_AUTH_BYPASS=true` | Every portal request becomes an administrator with no credentials. |
| `OIDC_ISSUER` not `https://` | The signing keys for every SSO login arrive over that connection. Exempted for `localhost`, `127.0.0.1`, `::1`. |
| The dev bypass rows in the database | `demo-user-001` / `demo-org-001` is a real admin account with an empty password, and it outlives the flag that created it. |

Everything else is a warning logged at boot: no SMTP relay, a `NODE_REGISTRATION_TOKEN` set but under 32 characters, a `SUPER_ADMIN_PASSWORD_HASH` that is not bcrypt, public URLs still pointing at `localhost`, a half-finished key rotation, and OIDC with some but not all of its three parts set.

Generate the encryption key the way the check tells you to:

```bash
python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'
```

There is no signing key to generate. The server makes its own and publishes the public half at `/.well-known/jwks.json`; see *Secrets* below for rotating it.

## Data stores

| Setting | Default | Notes |
|---|---|---|
| `POSTGRES_HOST` | `postgres` | A compose service name. The chart requires a real one. |
| `POSTGRES_PORT` | `5432` | |
| `POSTGRES_USER` | `myuser` | |
| `POSTGRES_PASSWORD` | `mysecretpassword` | |
| `POSTGRES_DB` | `igni` | |
| `REDIS_HOST` | `redis` | |
| `REDIS_PORT` | `6379` | |
| `REDIS_DB` | `10` | Chart schema restricts this to 0–15. |
| `DB_POOL_SIZE` | `5` | |
| `DB_MAX_OVERFLOW` | `15` | |
| `DB_POOL_TIMEOUT` | `10.0` | Seconds a request waits for a free connection, then fails. |

There is one pool per process, shared by every repository, so `DB_POOL_SIZE + DB_MAX_OVERFLOW` is that process's whole ceiling on concurrent queries. At startup the server reads the server's `max_connections` and warns if two processes at that ceiling would exhaust it — a warning, never a refusal, because one process cannot know how many replicas you run.

The chart deploys neither store and bundles no subchart for either. `postgres.host` and `redis.host` have no default and `helm install` fails naming them.

## Settings whose emptiness disables a feature

This is the group that causes the most confusion, because a disabled feature is indistinguishable from a broken one unless you know the rule.

| Setting | Default | Empty means |
|---|---|---|
| `SCIM_TOKEN` | `''` | Every `/scim/v2` route answers 503. |
| `METRICS_TOKEN` | `''` | `GET /metrics` answers 404. A wrong token answers 401. |
| `NODE_REGISTRATION_TOKEN` | `''` | Node registration answers 404. |
| `OIDC_ISSUER` | `''` | No SSO button; `/v1/auth/oidc` answers 404. |

`/ready` checks Postgres and Redis and nothing else. Only `unavailable` drains a replica from a load balancer; `busy` — a saturated connection pool — does not, because removing a working replica makes a capacity problem worse.

There is no `EMBEDDER_URL` or `NEO4J_*` here. Documents, their graph and their embedder belong to the artifacts service, which has its own; this server never read either for anything but a readiness probe, and the settings and the probe are gone.

There is no setting here for the documents service, or for any other service. Artifacts and CodeIndex are each deployed with this server's `PUBLIC_BASE_URL` and their own public URL, register themselves, and are approved by a super admin on the portal's *Services* page; the server learns where they are from that registration. A deployment with no approved service simply has none — a subject-access export has no sections from it and an erasure has nobody else to tell, and neither is an error.

## Inference backends

Your own vLLM nodes, described as a JSON array, from `INFERENCE_BACKENDS_PATH` (a file — a ConfigMap or bind mount) or `INFERENCE_BACKENDS` (the same JSON inline). Both default to empty; nodes pinned in the portal and nodes that register themselves merge in, with precedence static, then pinned, then registered. The merged list is re-read every 30 seconds, so adding a node needs no restart.

```json
[
  {"id": "gh200-a", "base_url": "http://10.0.3.11:8000", "model": "igni-1.0-4b", "max_concurrent": 64},
  {"id": "gh200-b", "base_url": "http://10.0.3.12:8000", "models": ["igni-1.0-4b", "sql-lora"], "multimodal": true}
]
```

`id`, `base_url` and at least one model are required; a single-model node may write `model` instead of `models`. `max_concurrent` defaults to 64, `multimodal` to false, and `api_key` is optional because a node on a private network usually has no auth. A `base_url` without an `http://` or `https://` scheme fails the whole list at load rather than silently dropping one node.

A node carries no `pools` field. 0.7.0 removed the two-pool model — `chat` and `codeindex` — and a node is now just a node; migration 0029 drops the column. A `pools` key left in a list written for an earlier release is ignored rather than refused, so the file still loads. CodeIndex runs its own nodes from its own configuration, and there is no setting here for them.

## Email

Off by default and pointing nowhere: `EMAIL_ENABLED=false`, `SMTP_HOST=''`. That is a supported configuration, but with no relay sign-in and password-reset codes reach nobody. For a first setup before email works, `LOG_SIGN_IN_CODES=true` writes them to the application log for an operator to pass on; while it is on, anyone who can read those logs can sign in as any user, and the server warns at every start and shows super admins a banner in the portal. It is off by default. Amazon SES (`EMAIL_BACKEND=ses`) counts as a relay.

| Setting | Default |
|---|---|
| `EMAIL_ENABLED` | `false` |
| `EMAIL_BACKEND` | `smtp` (or `ses`) |
| `SMTP_HOST` | `''` |
| `SMTP_PORT` | `587` |
| `SMTP_TLS_MODE` | `starttls` |
| `SMTP_USERNAME` / `SMTP_PASSWORD` | `''` |
| `EMAIL_FROM_ADDRESS` | `noreply@igni.local` |
| `EMAIL_FROM_NAME` | `igni` |
| `SES_REGION` / `SES_CONFIGURATION_SET` | `''` |
| `EMAIL_CODES_HOURLY_LIMIT` | `600` |

`SMTP_TLS_MODE` names three different servers, not three preferences: `starttls` on 587, implicit `tls` on 465, `none` on 25 for an internal relay. `EMAIL_BACKEND=ses` uses the SES API and the ambient AWS credential chain, so the deployment stores no mail secret at all; `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY` exist for the case where no instance role is available and should otherwise stay empty.

`EMAIL_CODES_HOURLY_LIMIT` is the anonymous-code backstop across *all* addresses; `0` disables it. Per-address limits are always on and are not configurable.

## Sign-in

`OIDC_ISSUER`, `OIDC_CLIENT_ID` and `OIDC_CLIENT_SECRET` go together — all three or none. `OIDC_SCOPES` defaults to `openid email profile`, `OIDC_EMAIL_CLAIM` to `email`, `OIDC_NAME_CLAIM` to `name`, and `OIDC_DISPLAY_NAME` to `SSO` (change it; people recognise their provider's name). `OIDC_LINK_BY_EMAIL` defaults to false and should stay there unless your IdP verifies addresses — on, it is account takeover by email match.

There is no SAML setting: SAML sign-in is not available yet. `OAUTH_PROVIDERS` defaults to `["google"]`, and Google is the only social sign-in there is. Email and SSO are what a company authenticates with; this list is the exception, not the strategy.

`BLOCK_PUBLIC_MAIL_DOMAINS` defaults to true. Domain visibility decides who a colleague is by comparing the part of an address after the `@`, which only works while a domain names an employer.

## URLs that end up in links

| Setting | Default |
|---|---|
| `PUBLIC_BASE_URL` | `http://localhost:8000` |
| `PORTAL_BASE_URL` | `http://localhost:3000` |
| `OAUTH_REDIRECT_BASE_URL` | `http://localhost:5173` |
| `OAUTH_FRONTEND_BASE_URL` | `http://localhost:5173` |

`PUBLIC_BASE_URL` is this server's own external address: the `iss` of every token it signs, and the URL a service such as CodeIndex or Artifacts is deployed with, beside its own — it fetches `/.well-known/openid-configuration` and `/.well-known/jwks.json` from there and finds every other endpoint in them. Whatever fronts the server must route `/.well-known` to the API alongside `/v1`; the chart's Ingress and the shipped Caddyfile do. With the chart's Ingress enabled it defaults to `https://<ingress.host>`. Outside `dev` anything but `https://` refuses to start. How a service registers, is approved on the portal's *Services* page, and gets tokens is in `docs/service-identity.md` in the server repository.

The other three are wrong for every real install and only fail when somebody clicks an invite link, so the startup check warns about each one that still says `localhost`.

## Limits, retention and logs

| Setting | Default | Notes |
|---|---|---|
| `APP_ENV` | `prod` | `dev` downgrades the fatal checks to warnings and is the only value that mounts `/docs`. |
| `LOG_LEVEL` | `INFO` | |
| `LOG_FORMAT` | `text` | `json` emits one object per line. Nothing is shipped anywhere either way. |
| `MAX_REQUEST_BODY_BYTES` | `33554432` (32 MiB) | Larger bodies get a 413 before being read. |
| `AUDIT_RETENTION_DAYS` | `0` | 0 keeps everything, and the retention runner starts nothing. |
| `MCP_GATEWAY_URL` | `http://mcp_gateway:7778` | A compose name; the chart replaces it with an in-cluster one. |
| `CHAT_QUEUE_MAX_WAIT_SECONDS` | `900` | When every model node is full, a request waits its turn — fairly between organisations, then between people and apps — for at most this long, then gets a 429. |
| `CHAT_QUEUE_MAX_LENGTH` | `2000` | The most requests that may wait in one API replica; past it, a 429 at once. |
| `IGNI_LICENCE_FILE` | `/etc/igni/licence/licence.json` | The paid modules' licence. The server only shows it on the portal, installs an uploaded one, and audits it daily; the base ignores a missing file. See [Licensing and evaluations](/docs/server/licensing-and-evaluations). |
| `SERVICE_EVENTS_PER_MINUTE` | `600` | How many events one service may ask igni to deliver to producers per minute (`POST /v1/service-events`); past it, a 429 with `Retry-After`. `0` removes the limit. |
| `SERVICE_EVENT_MAX_WAIT_SECONDS` | `1209600` (14 days) | The longest an undelivered event waits. An event without `expires_at` gets this; a later `expires_at` is cut to it. Then it expires: no longer handed out, and its service reads it as `expired`. |
| `SERVICE_EVENT_RETENTION_DAYS` | `30` | Days an event is kept after it was delivered, expired or dropped, so its service can read what became of it; then the daily retention round deletes it. `0` keeps them. |
| `SUPER_ADMIN_PASSWORD_HASH` | `''` | Break-glass: a bcrypt hash, never a password — generate one with `python -c "import bcrypt,secrets; p=secrets.token_urlsafe(32); print(p, bcrypt.hashpw(p.encode(), bcrypt.gensalt()).decode())"`. Presented as `X-Super-Admin-Key`, it reaches the deployment-wide settings under `/v1/portal` when nobody can sign in — the registered clients and the extension priority order. The super-admin list and the organisation list need a signed-in super admin, and the break-glass key does not reach them. Super admins themselves are accounts (the first one created is one); this makes nobody a super admin. Unset, there is no break-glass. |

The `CLOUD_VM__` group sets the default VM profile and the platform's caps for [Fleet](/docs/fleet) — `CLOUD_VM__STANDARD_NORMAL_MEMORY_MIB=1024`, `CLOUD_VM__STANDARD_MEMORY_MIB=4096`, `CLOUD_VM__STANDARD_VCPUS=1`, `CLOUD_VM__STANDARD_IDLE_BEFORE_PAUSE_S=10`, and the `CLOUD_VM__CAP_MAX_*` caps; that page lists them all.

The `AI__` group has one setting: `AI__UPSTREAM_TIMEOUT=300.0`, the seconds the API waits for a model to answer. (The code-analysis settings that used to sit beside it, such as `AI__MAX_DEGRADED_RATIO`, belong to [CodeIndex](/docs/codeindex), which reads its own.)

## Tracing (OpenTelemetry)

Off by default, with **no default endpoint**: unless `OTEL_EXPORTER_OTLP_ENDPOINT` names a collector, no tracer provider is built and nothing is exported. These are the standard OpenTelemetry variables, read from the process environment only — not a `.env` file, not the database, not an admin screen — so where traces go is decided where the server is started.

| Variable | Default | Notes |
|---|---|---|
| `OTEL_EXPORTER_OTLP_ENDPOINT` | empty | Your collector's base URL (`http://otel-collector:4318`); `/v1/traces` is appended for `http/protobuf`. Must be `http://` or `https://`. |
| `OTEL_EXPORTER_OTLP_PROTOCOL` | `http/protobuf` | Or `grpc`, when the endpoint is the collector's gRPC address (`:4317`). |
| `OTEL_EXPORTER_OTLP_HEADERS` | empty | `key=value,key2=value2`, values URL-encoded — for the collector's authentication. |
| `OTEL_SERVICE_NAME` | `igni-server` | |

Traced: each request (FastAPI; an incoming `traceparent` from the igni client is accepted, so the client's trace continues), each outgoing httpx call (to inference nodes, services, your identity provider; it carries `traceparent` on), and each SQL statement, parameterised, without bound values. `/metrics`, `/health`, `/ready` and `/version` are not traced. Never exported: request and response bodies, headers, the query string and full URL, and the caller's address and user agent.

With the NetworkPolicy on, add the collector to `networkPolicy.egress`. The igni client can export to the same collector; its settings are in its own configuration (`telemetry.otlp`, managed settings or group policy only).

## Secrets

Secrets never belong in the ConfigMap. The chart creates a Secret from `secrets.*` by default, but the better arrangement is `secrets.existingSecret: "igni-secrets"`, which stops the chart rendering a Secret at all and keeps nothing sensitive in your values file, your Helm release object, or your CI logs. The keys it expects are `SECRET_ENCRYPTION_KEY` (required), `SECRET_ENCRYPTION_KEYS_PREVIOUS`, `POSTGRES_PASSWORD`, `SMTP_PASSWORD`, `SUPER_ADMIN_PASSWORD_HASH`, `METRICS_TOKEN`, `OIDC_CLIENT_SECRET`, `OAUTH_GOOGLE_CLIENT_SECRET`, `SCIM_TOKEN`.

The chart omits empty optional secret values rather than writing `""`. It refuses to render without `secrets.secretEncryptionKey`, unless you supply the Secret yourself — in which case the server is what refuses.

`SECRET_ENCRYPTION_KEY` is a Fernet key, and it protects the server's token-signing key as well as the secrets it stores:

```bash
python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'
```

To rotate it, put the old key in `SECRET_ENCRYPTION_KEYS_PREVIOUS` (comma-separated, decrypt-only), deploy, run `python tools/rotate_secret_key.py --apply`, then clear it. The server warns on every boot while it is set, because the old key is still a working key until you finish. The rotation re-encrypts every encrypted value, in the shared database and in every organisation's own database, including the stored signing key and inference nodes' API keys; it is safe to run again if it is interrupted, and it exits non-zero if any value cannot be read, in which case keep the old key. Values in Redis (pending sign-in codes, self-registered nodes' keys) are not re-encrypted but expire within minutes, so wait at least 10 minutes after deploying before you clear the old key.

The signing key rotates separately, with no configuration change: `python tools/rotate_signing_key.py --stage`, run from a checkout of the server repository with the deployment's Postgres settings and `SECRET_ENCRYPTION_KEY` in its environment. The new key is published at once and signs ten minutes later, so every replica and every service has it first; the old one keeps verifying what it signed for 31 days. Without `--stage` it lists the keys and which one is signing.

## Two Helm details worth knowing

`api.terminationGracePeriodSeconds` is empty by default and derives as `ai.upstreamTimeout + 30`. Kubernetes' own default is 30 seconds, which would SIGKILL any completion still streaming after half a minute — the ordinary case for a coding agent, not the edge.

Every pod template sets `enableServiceLinks: false`. Kubernetes injects an environment variable per Service in the namespace, so a Service named `redis` sets `REDIS_PORT=tcp://10.96.112.222:6379`, which fails validation against `Settings.redis_port: int` and kills the pod before it starts.

A handful of settings have no named chart value and no entry in `dist.env` — `INFERENCE_BACKENDS`, `INFERENCE_BACKENDS_PATH`, `NODE_REGISTRATION_TOKEN`. Supply those through the chart's `extraEnv` or `extraEnvFrom`.

## Where to go next

- **[Connecting your GPUs](/docs/server/inference-backends)** — the settings here describe the node list; that page describes what fills it.
- **[Administering your organisation](/docs/server/administering-your-organisation)** — the configuration above decides which sign-in methods exist; that page is where you use them.
