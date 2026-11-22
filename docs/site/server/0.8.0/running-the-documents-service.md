igni runs as one service by default: `igni server`, its Postgres, and its Redis. One further deployment is optional and adds one feature: the **documents service**, the paid Artifacts module, owns every document — versions, projects, folders, drafts, comments, lineage — plus the Neo4j graph and the embedder that make them searchable. It is not needed to run the server, and it is not reachable by a client unless you deploy it. This page is for whoever is standing it up.

**Nothing is configured on `igni server` for it.** There is no documents URL, no shared secret and no timeout to set on the server. The documents service is deployed from the Artifacts module's own repository, with its own manifests; igni server knows it only as a registered service. It is deployed with two addresses — `igni server`'s URL and its own public URL — and introduces itself:

1. On first start it generates a key pair, seals it with `IGNI_KEY_ENCRYPTION_KEY`, registers with the server (`POST /v1/clients`), uploads the extension it ships to people's machines, and logs its key's thumbprint.
2. A super admin opens **Services** in the portal, compares the thumbprint with the one in the log, reads what the extension would put on people's machines, and approves it.
3. A super admin opens the service's own site — the root of its public URL, which the Services page links to — signs in through igni, and sets each organisation's document graph under **Settings**. Until an organisation has one, Artifacts is off for it.

Until that approval the service is registered but trusted with nothing: it cannot get a token, read the directory or write a notification, and its extension is not distributed. After it, an org admin still has to switch the extension on for each group that should have it. Revoking it on the same page stops all of that at once. The whole exchange — discovery, registration, tokens, the directory, the extension — is `docs/service-identity.md` in the server repository; this page is the operator's view of it.

## The documents service

One API process, plus the embedder as an optional dependency of its own, and a Neo4j graph per organisation.

It refuses to start without igni server's URL, its own public URL, a service name, `IGNI_KEY_ENCRYPTION_KEY` and `POSTGRES_URL`, rather than booting into a state where every request fails for a reason nothing names. The server refuses to register a `PUBLIC_BASE_URL` that is plain `http` — except on the same machine, or when the server runs with `APP_ENV=dev` — because people's identity travels to that address. The graphs and the embedder get the opposite treatment: an organisation with no graph has no documents search, an empty `EMBEDDER_URL` disables indexing, and the service still starts and serves.

| Setting | Default | Notes |
|---|---|---|
| `IGNI_SERVER_URL` | `''` | **Required.** igni server's URL — its `PUBLIC_BASE_URL`. `IGNI_SERVER_BASE_URL` also works. Discovery, the public signing keys, registration, the token endpoint and the directory are all found from it. |
| `PUBLIC_BASE_URL` | `''` | **Required.** This service's own public URL, on a hostname of its own (see below), as people's machines and igni server reach it. It is what the service registers as, the audience every token presented to it must name, and where the server sends erasure and subject-access requests. |
| `SERVICE_NAME` | `Artifacts` | What the registration is called on the portal's Services page. |
| `IGNI_KEY_ENCRYPTION_KEY` | `''` | **Required.** A Fernet key sealing the key this service signs with towards igni. Changing it is a new identity, which a super admin approves again. |
| `IGNI_KEY_DATABASE_URL` | `''` | Where that sealed key is kept. Empty means the shared Postgres. |
| `POSTGRES_URL` | `''` | **Required.** A SQLAlchemy async URL for the shared database. Each organisation's documents go in a database of their own beside it, `<db>_t<n>`, which the service creates — so its role needs `CREATEDB`. |
| `REDIS_URL` | `''` | The shared Redis database. Which logical database it uses is the module's own convention — the server's chart assigns a number only for itself. Each organisation's drafts and presence are in a database of their own on the same server, so configure it with enough: `databases 64` holds sixteen organisations. Not checked at startup — see below. |
| `GRAPH_CREDENTIALS_KEY` | `''` | A Fernet key encrypting each organisation's graph password. Not the same key as `IGNI_KEY_ENCRYPTION_KEY`, though you may give them the same value. |
| `EMBEDDER_URL` | `''` | Empty disables indexing. |
| `APP_BASE_URL` | `''` | Where the igni app is, so a non-public document's link opens in it. Empty, every link falls back to the public reader. |
| `IGNI_LICENCE_FILE` | `/etc/igni/licence/licence.json` | The licence file. See *Licence* below. |
| `IGNI_EXTENSION_BUNDLE` | `/app/extension-bundle.tar.gz` | The extension bundle the image carries. Uploaded at start when the server holds a different one; missing means nothing is uploaded. |
| `PORT` | `8100` | |
| `ENVIRONMENT` | `development` | Anything but `production` serves `/docs`. Only exactly `dev` trusts the licence test key. |
| `MAX_REQUEST_BODY_BYTES` | `16777216` | 16 MiB. |
| `DB_POOL_SIZE` / `DB_MAX_OVERFLOW` / `DB_POOL_TIMEOUT` | `5` / `15` / `10.0` | |

**Nothing secret crosses between the two services, in either direction.** The server signs its tokens with a key only it holds and publishes the public half at `/.well-known/jwks.json` under its `PUBLIC_BASE_URL`; the documents service checks a person's token against those keys, and that the token names its own `PUBLIC_BASE_URL` as the audience. The documents service signs its own requests to the server with the key it registered. There is no value to copy from one deployment to the other.

**`PUBLIC_BASE_URL` has to be the address the server can reach as well as the one people use.** The server calls `{PUBLIC_BASE_URL}/v1/igni/subjects/...` when an organisation admin runs an erasure or a subject-access export, so an address that resolves only from people's laptops leaves those requests failing.

**`REDIS_URL` is a hard dependency that startup does not check.** `/ready` reports an empty or unreachable Redis as `unavailable` — a draft lives in Redis before it is published, so there is nowhere to put unsaved work. The service will start and then never become ready. Set it.

The service is two images: the API and its embedder. Both are in each release's signed offline bundle, as `igni-artifacts` and `igni-artifacts-embedder` — see [Offline updates](/docs/server/offline-updates) — or you can build them from the Artifacts module's own repository. `make run` uses `PORT ?= 7777`, which contradicts the Dockerfile's 8100.

```bash
docker build -f containers/be/Dockerfile -t igni-artifacts .              # API, :8100, no torch
docker build -f embedder/Dockerfile -t igni-artifacts-embedder embedder/  # embedder, :7001
```

The embedder's Dockerfile copies a bare `app.py`, so its build context is `embedder/` — not the repository root the API image is built from. For development, `make up` in the Artifacts module's repository runs the embedder alone on `127.0.0.1:7001` (`containers/docker-compose.local.yml`), for a `make run` with `EMBEDDER_URL=http://127.0.0.1:7001`.

The embedder is a separate image because `torch` is the largest thing either would carry. It bakes `sentence-transformers/all-MiniLM-L6-v2` and the reranker `cross-encoder/ms-marco-MiniLM-L-6-v2` in at build time with `HF_HUB_OFFLINE=1`, installs torch from the CPU wheel index, and serves `POST /embed` (at most 2048 texts), `POST /rerank` (at most 200) and `GET /health`. **It has no authentication at all, by design** — it holds no data and answers the same way for everyone. What protects it is the network: a ClusterIP Service, or a compose service with no published port, reachable by the documents service only. Do not expose it. Its `/health` answers `loading` until both models are in memory, which is what its own readiness probe should ask. The documents service's `/ready` never asks it: its `embedder` check reports `disabled` when `EMBEDDER_URL` is empty and `ok` whenever it is set, so the embedder cannot drain a documents replica either way. How to deploy it beside the service — a Deployment and Service, the probe, the memory it needs — is the Artifacts module's `docs/running-the-embedder.md`.

**igni server's Helm chart does not deploy the embedder.** There is no embedder Deployment, no Service and no `EMBEDDER_URL` in it: the server's own readiness probe stopped asking an embedder anything when the clients that used it left, and an embedder outage could drain the server's replicas through `/ready` for a feature this process does not serve. Deploy the embedder from the Artifacts module and set `EMBEDDER_URL` on the documents service's Deployment yourself.

The documents service holds no `users` table. Who somebody is arrives in their token; anybody else — a person to share with, a name to show — it asks of the server's directory (`/v1/directory/*`) with a service token for that organisation. Tokens live ten minutes, which is how a deactivation reaches the documents service: it never asks whether somebody is still allowed, and the server refuses to renew a token for somebody who is not. Document traffic itself does not pass through the server: the igni extension on a person's machine gets a token for the documents service from the igni host and calls the service directly.

## Its own hostname

**The documents service is served on a hostname of its own — `https://artifacts.example.com` — never as a path on the server's.** Its URL is its identity: it is what the service registers as, the audience every token people get for it must name, and the base the server calls for erasure and export. Put it under the server's host (`https://igni.example.com/v1/artifacts`) and a token's audience no longer says which of the two it is for — they share an origin — and every path the service serves becomes one the server can never use. So give it its own DNS name and its own certificate, and set its `PUBLIC_BASE_URL` to exactly that URL. The server enforces this when the service registers: a service's URL must be an origin of its own — scheme, host and port, with no path — and must not be igni's own address.

**The service is deployed from the Artifacts module's own repository, with its own manifests.** igni server's Helm chart publishes no second host: there are no `artifacts` values, no second Ingress and no ConfigMap for the service, and that is a decision rather than an omission — nothing on the server's host belongs to another service. The chart renders one Ingress, for the server's own host, and one ConfigMap, holding the server's own settings.

So the service's environment is set on its own Deployment:

| Variable | Value |
|---|---|
| `PUBLIC_BASE_URL` | `https://artifacts.example.com` — the service's own public URL, and the identity it registers under. |
| `IGNI_SERVER_BASE_URL` | igni server's API Service, in-cluster, when the two run in the same cluster. |

The Deployment also needs its own `IGNI_KEY_ENCRYPTION_KEY`, `GRAPH_CREDENTIALS_KEY`, `POSTGRES_URL`, `REDIS_URL` and `EMBEDDER_URL`, and the licence Secret mounted at the path the service reads. Behind a proxy of your own, give the service its own site; nothing under the server's site routes to it.

`IGNI_SERVER_URL` may be an in-cluster address: the service *calls* the server there, but compares tokens against the issuer it reads from discovery, which is the server's public `PUBLIC_BASE_URL`.

For development, the service and its embedder come from the Artifacts module's own repository. The server's repository no longer carries a compose file for the documents service: `containers/docker-compose.artifacts.yml` is gone, as is everything else that hosted the module here. A local address on a `*.localhost` name, such as `http://artifacts.localhost:8100`, is one the server accepts over plain http, because it is this machine: `*.localhost` is loopback on the laptop (browsers, curl and macOS resolve it with no hosts entry; on a Linux machine whose resolver does not, add `127.0.0.1 artifacts.localhost` to `/etc/hosts`), and a network alias makes the same name reach the container from inside the stack, so the server can call the service at the URL it registered. `http://localhost:8100` would not work for that: inside a container, `localhost` is the container itself.

## When it is unreachable

The failure mode matters for your load balancer, and it is now one-sided.

**The documents service being down does not take the server out of rotation.** The server's `/ready` checks its own dependencies only; it does not probe any registered service. What notices instead are the two requests that have to reach every approved service: an Article 17 erasure that the documents service does not confirm answers **502** naming it, and is safe to run again once the service is back; an Article 15 export that it does not answer comes back with the report marked `truncated`.

Finally, the server and the documents service may share one physical Redis and one physical Postgres. They are kept apart logically: igni server's own Redis database is 10 by default (`redis.db` in the chart), and the chart no longer hands out database numbers for the modules — the Artifacts module's own repository says which logical database the documents service uses, and where each organisation's drafts and presence go. Size `databases` for that.

## Licence

The documents service checks the paid modules' licence file itself, offline, at `IGNI_LICENCE_FILE` (default `/etc/igni/licence/licence.json`), and re-reads it at most once a minute, so a renewed file needs no restart. igni's Helm chart mounts the licence only into igni server, so mount the same Secret at the same path in this Deployment yourself. The test key is trusted only with `ENVIRONMENT=dev` — never with the default, `development`.

When the licence is read-only, every `GET`, the archivist's read-only Cypher and finding a folder's project still work; sharing, new versions, drafts, comments and every other change are refused with `403` and an `igni-licence: <state>` header, and the document room's WebSocket is closed with `4403`. `GET /v1/licence` reports the module's state. The full rules are in [Licensing and evaluations](/docs/server/licensing-and-evaluations), and what each state means for documents is on the [Artifacts](/docs/artifacts) page.

## Where to go next

- **[Artifacts](/docs/artifacts)** — what the documents service gives your team once it is running.
- **[Licensing and evaluations](/docs/server/licensing-and-evaluations)** — the licence file it checks.
