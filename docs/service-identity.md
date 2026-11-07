# Service identity: how a service trusts igni server, and igni trusts it

CodeIndex, Artifacts and every later service — and apps such as a review
bot — are deployed with **igni server's URL** and their own public URL.
Everything else they learn from igni. Nothing secret is shared between igni and a
service, ever: igni signs with a key only it holds, a service signs with a
key only it holds, and each checks the other against published public keys.

## Status: built in igni server

igni server implements every endpoint below. There is no other way in:
the shared `ARTIFACTS_SERVICE_TOKEN` and the `/v1/internal/*` routes are
gone. Artifacts and CodeIndex each follow this document.

## Who reads this

**A service or extension author does not.** They use the SDKs in the igni
repository — `igni-service` for a service, `igni-extension` for an
extension — and call `host.notify(...)`, `host.directory`, `ext.host.notifications.notify(...)`
and the rest; no author writes a URL, a token request or a JWS. This
document is the wire those SDKs speak, for whoever maintains them or
implements one in another language.

## Adding a service, in full

1. Deploy it with igni's URL and its own public URL, on a hostname of its
   own (below).
2. On first start it generates a key pair, registers (§2), uploads its
   extension (§8) and logs its key's thumbprint.
3. A super admin opens *Services* in the portal, compares the thumbprint,
   reads what the extension would put on people's machines, and approves.

Nothing else is configured anywhere — no secret, no client id, no key.

## A service has its own hostname

Every service is reached on **its own hostname** —
`https://artifacts.example.com`, `https://codeindex.example.com` — and
never as a path on igni's (`https://igni.example.com/v1/artifacts`).

Its URL is its identity. It is the `client_uri` it registers (§2), the
**audience** of every token a person gets for it (§3, §4), and the base
igni calls for erasure and subject access (§6). On igni's host:

* the audience would no longer say which of the two a token is for — a
  token for `https://igni.example.com/v1/artifacts` shares its origin
  with igni, and a browser treats the two as one site;
* every path the service serves is a path igni can never use, and the
  two would have to agree forever on who owns which prefix.

How to set it up:

| Where | What |
|---|---|
| Helm (`charts/igni`) | `artifacts.enabled=true`, `artifacts.host=artifacts.example.com`, `artifacts.serviceName=<its Service>`. The chart renders a second Ingress for that host — with `ingress.tls.enabled`, a certificate of its own in `artifacts.tls.secretName` — and a ConfigMap `<release>-artifacts-env` carrying `PUBLIC_BASE_URL=https://artifacts.example.com` and `IGNI_SERVER_BASE_URL` (the API Service, in-cluster) for the service's Deployment to `envFrom`. It refuses to render without the host, or with igni's. |
| Your own proxy | A site of its own for the service's hostname, proxying everything to it. Nothing under igni's site routes to it. |
| Local compose | `http://artifacts.localhost:8100`: `*.localhost` is loopback on the laptop, and a network alias makes the same name reach the container from inside the stack, so igni can call the service at the URL it registered. |

The service's `PUBLIC_BASE_URL` is that URL exactly, and it must be one
igni's API can reach as well as people's machines: igni calls it for
erasure and export.

## 1. Discovery

| What | Where |
|---|---|
| Metadata (RFC 8414 / OIDC Discovery) | `GET {igni}/.well-known/openid-configuration` — `token_endpoint`, `revocation_endpoint`, `authorization_endpoint`, `jwks_uri`, … |
| Public signing keys | `GET {igni}/.well-known/jwks.json` |

`issuer` is `PUBLIC_BASE_URL`. Every igni token carries it as `iss`. Keys
are RS256, identified by `kid` (the key's RFC 7638 thumbprint). A service
caches the key set for `Cache-Control: max-age` (five minutes) and refetches
on an unknown `kid`.

igni generates its signing key on first start and keeps it in Postgres,
encrypted under `SECRET_ENCRYPTION_KEY`. A rotation
(`python tools/rotate_signing_key.py`) stages a new key that is published at
once and signs only ten minutes later, so every replica and every service
cache has it first. A superseded key stays published for 31 days — the
longest any token lives — and is then pruned.

## 2. Registering

A service generates its own key pair (RS256 or ES256) on first start,
keeps the private half to itself, and prints the public key's RFC 7638
thumbprint in its log. Then it registers with RFC 7591 client metadata:

```
POST {igni}/v1/clients
{
  "client_name": "CodeIndex",
  "client_uri": "https://codeindex.example.com",   // its own public URL
  "kind": "service",                               // or "platform"; igni's addition
  "redirect_uris": ["https://codeindex.example.com/auth/callback"],
  "token_endpoint_auth_method": "private_key_jwt",
  "jwks": {"keys": [ { "kty": "RSA", "n": "...", "e": "AQAB", "kid": "..." } ]}
}
→ 201 {"client_id": "...", "client_id_issued_at": ..., "client_uri": "...",
       "state": "pending", "thumbprint": "...", ...}
```

`client_uri` is normalised — lower-case scheme and host, no trailing
slash — and the normalised form is returned: it is the **audience of
every token a person gets for this client** (§4), so the service compares
against exactly this string. It is also where igni reaches a service for
erasure and subject access (§6). https only, except for this machine or
`APP_ENV=dev`.

`kind` is `service`, or `platform` for the cloud's gateway and node agent
(§11). An app does not register here: an org admin registers it on the
portal, in their organisation (§11).

Unauthenticated, and it grants nothing. Registering the same key again
returns the same registration with its current state, so a service simply
registers on every start. At most 20 registrations can be pending. A
private JWK (one with `d`) is refused. Errors are RFC 7591's:
`400 {"error": "invalid_client_metadata", "error_description": ...}`.
Revoking stops token requests and the directory at once.

## 3. Getting a token: `POST {igni}/v1/oauth/token`

Form-encoded (RFC 6749). A client authenticates with `private_key_jwt`:

```
client_assertion_type=urn:ietf:params:oauth:client-assertion-type:jwt-bearer
client_assertion=<JWT signed with the registered key:
                  iss = sub = client_id, aud = issuer or token endpoint,
                  jti unique, exp at most 300 s ahead>
```

Each `jti` is accepted once. **Every token names one organisation.**

| Grant | Who | Gets |
|---|---|---|
| `client_credentials` + `organisation=<org_id>` | a **service**, as itself | a service token for igni: `sub` = client id, `aud` = issuer, `token_use` = `service`, `org` |
| `client_credentials` + `scope=revocations`, no `organisation` | a **service**, at start-up | a service token naming no organisation: `token_use` = `service`, `scope` = `revocations`, no `org`. Good only for `GET /v1/organisations/served` and `GET /v1/revocations?scope=served` (§21); every other route refuses it |
| `client_credentials` (`organisation` optional, and only its own) | an **app**, as itself (§11) | an app token for igni: `sub` = app id, `aud` = issuer, `token_use` = `app`, `org` = its organisation, `kind` = `app`, `name`; never a `group` |
| `client_credentials` | a **platform client**, as itself (§11) | a platform token for igni: `sub` = client id, `aud` = issuer, `token_use` = `platform`; no `org` |
| `authorization_code` + `code_verifier` + `redirect_uri` | a client, for a person who signed in through igni | a user token for that client, and a refresh token |
| `refresh_token` | the same client | a new user token and refresh token — refused once the person is deleted, deactivated, removed from the organisation, or has had their sessions withdrawn, and once the client has revoked the sign-in (§3.1) |
| `urn:igni:params:oauth:grant-type:vm-sign-in` | the person's **cloud igni**, with the session of their own sign-in through the gateway (§16) | a 15-minute session token and their VM's credential |
| `urn:igni:params:oauth:grant-type:vm-credential` | a producer's **VM**, with its own VM credential (§16) | a 15-minute token for the producer and the rotated credential |
| `urn:igni:params:oauth:grant-type:api-key` | a **program** a person gave their API key to (§22) | a 15-minute session as that person, pinned to the key's organisation |
| `urn:ietf:params:oauth:grant-type:token-exchange` + `space=<space_id>` | an **app**, no client authentication | `subject_token` = the app's own token, `subject_token_type` = `urn:ietf:params:oauth:token-type:access_token` → a `space` token for one of its spaces (§17.2) |
| `urn:ietf:params:oauth:grant-type:token-exchange` | the **igni host on a person's machine**, no client authentication | `subject_token` = the igni session, `subject_token_type` = `urn:ietf:params:oauth:token-type:access_token`, `audience` = **the service's URL**, optional `organisation` (required if the session is not pinned to one) → a user token for that URL |
| `urn:ietf:params:oauth:grant-type:token-exchange` | an **app** or a **space** — igni in its VM, or the app itself — no client authentication | `subject_token` = the app's or the space's own igni token, `subject_token_type` as above, `audience` = **the service's URL**, optional `organisation` (only its own) → an `app` or `space` token for that URL (§9.2) |

The token exchange needs no registration of the audience: the session is
the credential, and whoever holds it can already act as that person
everywhere. Naming a URL only says which service will accept the result.

Access tokens live **ten minutes**. That is how revocation reaches a
service: it never asks igni whether somebody is still allowed, it holds a
token that says so for ten minutes and renews it at igni, which refuses.
Refresh tokens live 30 days.

### 3.1 Signing somebody out: `POST {igni}/v1/oauth/revoke`

RFC 7009, form-encoded, with the same client assertion as the token
endpoint (its `aud` may also be this endpoint's URL):

```
token=<the refresh token>
token_type_hint=refresh_token            // optional; the token says what it is
client_assertion_type=…&client_assertion=…
→ 200 (empty)
```

When a person signs out of a service's own pages, the service revokes
their refresh token, so the sign-in ends at igni too and not only in the
service's session store. What stops is the **sign-in**, not one token:
the authorization-code grant gives every sign-in an id, each refresh
token issued from it carries that id (`grant`), and after a revocation
no refresh token of that sign-in renews — renewing never retires the
token it renews, so earlier ones would otherwise still work. Another
sign-in by the same person (another browser) is untouched.

| Presented | Answer |
|---|---|
| a refresh token issued to this client | 200, and the sign-in is revoked; again is 200 too |
| a refresh token issued to another client | 400 `invalid_grant` |
| an access token | 400 `unsupported_token_type` — it cannot be revoked, it expires within ten minutes |
| anything that is not a valid token (expired, garbage) | 200: there is nothing left to revoke (RFC 7009 §2.2) |
| no client assertion, or a bad one | 401 `invalid_client` |

The access token already issued keeps working until it expires — at
most ten minutes — which is the same bargain as every other revocation.
Refresh tokens are not stored: igni keeps the revoked sign-in ids
(in its own Postgres, `revoked_grants`) until the last refresh token
they could stop has expired, 30 days after the revocation at most.

An app or a platform client never gets a person's token: neither uses the
authorization-code grant.

## 4. The tokens

All RS256, all with `iss`, `sub`, `aud`, `iat`, `exp` and `token_use`.
**Check all of them**, including `token_use` — a refresh token is not an
access token, and a service token is not a person.

| `token_use` | `aud` | `sub` | Other claims |
|---|---|---|---|
| `user` | **your URL** (`client_uri`) | the person's user id | `org`, `org_name`, `org_role` (`admin`/`member`), `email`, `name`, `super_admin`; `azp` = your client id when you obtained it yourself (§5) |
| `service` | igni's issuer | your client id | `org` |
| `app` | igni's issuer, or **your URL** when exchanged for you (§9.2) | the app's id | `org`, `kind` = `app`, `name` — an app acting as itself (§11); no `group` |
| `platform` | igni's issuer | the platform client's id | none — the cloud's gateway or node agent (§11) |
| `space` | igni's issuer, or **your URL** when exchanged for you (§9.2) | the space's id | `org`, `kind` = `space`, `app` (its app's id), `external_id` — one end user of an app, a producer of its own (§17); no `group` |
| `igni` | **your URL** | igni's issuer | `org` — igni calling you (§6) |
| `session` | igni's issuer | user id | igni's own portal/CLI session; `kind` = `person`; `org`, `org_name`, `group` (§8.3) on a CLI session; never accept it |
| `refresh` | igni's issuer | user id | `org`, `azp`, `grant` (the sign-in, §3.1), `jti`; only igni reads it |

## 5. Signing a person in to a service's own portal

Authorization code with PKCE (S256 only). The `authorization_endpoint` in
igni's metadata is the **portal's** `/authorize` page, because that is
where the person's session is:

1. Redirect the browser to `{authorization_endpoint}?response_type=code&client_id=…&redirect_uri=…&state=…&code_challenge=…&code_challenge_method=S256`.
2. The portal signs the person in if needed, asks which organisation (if
   they are in more than one), and redirects to `redirect_uri?code=…&state=…`.
   The redirect URI must be one the client registered, exactly.
3. The service's backend posts the code, the verifier and its client
   assertion to the token endpoint. Codes are single-use and live 60 s.

No consent screen: a super admin approved the client for the install.

## 6. igni calling a service: erasure and subject access

An organisation admin's GDPR request fans out to every approved service
with a `base_url`, with a token igni signs for that service
(`token_use` = `igni`, `aud` = its URL, `org` = the organisation):

| Call | Must |
|---|---|
| `DELETE {base_url}/v1/igni/subjects/{user_id}` | erase or redact the person's data in that organisation; **idempotent**; 2xx when done |
| `GET {base_url}/v1/igni/subjects/{user_id}/export` | answer `{"sections": [{"name": "...", "rows": [{...}]}]}` |

An erasure that any service does not confirm answers 502 naming it, and is
safe to run again. An export that a service does not answer is marked in
the report's `truncated`.

### 6.1 A deleted organisation

When an organisation is deleted — by its admin in the portal, or with the
account of its last member — igni tells every approved service with a
`base_url`, with the same kind of token (`token_use` = `igni`, `aud` = the
service's URL, `org` = the organisation):

| Call | Must |
|---|---|
| `DELETE {base_url}/v1/igni/organisations/{org_id}` | stop routing anything to the organisation's data; **idempotent**; 2xx when done, whether or not the service had heard of it |

The service-side SDK mounts it with `igni.router` and calls
`@igni.on_organisation_deleted` with the organisation's id. Stopping the
routing is the floor; what happens to the data is the service's to decide
and to document, and the two practices in use differ:

- **igni server keeps it and detaches it.** Nothing routes to the
  organisation again; its database stays, is still backed up, and an
  operator can read or drop it (`docs/DATA_RETENTION.md`).
- **The paid modules erase it.** CodeIndex and Artifacts detach the
  organisation at once and then erase what it held: its own Postgres
  database, dropped; its Redis database; its routes and licence seats;
  and the module's store — CodeIndex's changeset objects under the
  organisation's prefix, Artifacts' documents, passages and vectors in
  its graph, then the graph's address and credentials. Only the detached
  registry row stays, with `erased_at`, so the organisation's number is
  never reused. Each step is idempotent and a failed step answers igni
  with an error, so the outbox below retries until the erasure is
  finished. Not reached: the local Neo4j index CodeIndex's extension
  keeps on each developer's machine.

Deletion must not depend on every service being up at that moment, so it
goes through an outbox in the shared plane, `organisation_deletion_notices`:

- **Written with the deletion.** The transaction that removes the
  organisation writes one row per approved service. A deletion that
  commits is one every service will hear about; one that rolls back tells
  nobody anything.
- **Delivered by a round** that runs in every replica every 30 s, and at
  once in the replica that deleted. A 2xx deletes the row. Anything else
  — refused, 501 for a service with no handler, a timeout, unreachable —
  keeps it, with `attempts`, `last_error` and a `next_attempt_at` 30 s
  later, doubling to at most six hours. It is retried for as long as the
  service stays registered; nothing gives up on a deletion.
- **Replicas divide the work by row.** A round takes due rows with
  `FOR UPDATE SKIP LOCKED` and moves each one's `next_attempt_at` five
  minutes ahead before calling — a lease. Two replicas never call about
  the same row together, and a replica that dies mid-call lets the row go
  when the lease runs out. No advisory lock is needed.
- **A revoked service waits; a removed one is forgotten.** Rows for a
  service that is not approved are skipped, and delivered if it is
  approved again. Removing the registration removes its rows.

An operator sees what is still owed, and why, in that table.

## 7. The directory

With a service token, for the token's organisation only — the organisation
is never a parameter:

| Call | Answers |
|---|---|
| `GET /v1/directory/organisation` | `org_id`, `name`, `slug`, `tenant_number`, `domains` — derive your per-organisation database and Redis index from `tenant_number` |
| `GET /v1/directory/members?query=&limit=&include_former=` | people: `user_id`, `email`, `name`, `role`, `status`, `super_admin`; prefix search on address or name; at most 500 |
| `GET /v1/directory/people/{user_id}` | one person, present or former; 404 if never a member |
| `POST /v1/directory/people/resolve` `{"identities": [...]}` | user ids or addresses → present and former members only |


A revoked client's service token is refused at once, not after it expires.

## 8. The extension a service ships

A service's extension — the part that runs inside igni on people's
machines — comes **from the service and nowhere else**. There is no
extension marketplace; a developer can still install one from a directory
or repository behind igni's dev flag, and that is the only other way.

**Uploading.** On every start, after registering:

```
PUT {igni}/v1/clients/{client_id}/extension
Content-Type: application/gzip
X-Igni-Bundle-Signature: <compact JWS>
<the bundle: a gzipped tar with manifest.json at its root — the directory `igni ext install` takes>
```

The JWS is signed with the key the service registered (`kid` set) over
`{"extension": <manifest id>, "version": <manifest version>, "sha256": <hex SHA-256 of the archive bytes>}`.
igni refuses a signature that does not verify against that key, a digest
that does not match the bytes, or a manifest whose id or version differs.
Allowed while the registration is still pending — the super admin sees
what they are approving — and refused once revoked. Uploading again
replaces it, except as below. At most 400 MB. `GET {igni}/v1/clients/{client_id}/extension`
answers `{extension_id, version, sha256, held}` for the latest upload (or
404), so a service uploads only when its bundle has changed.

**An update that asks for more is held.** Approval covers the version the
super admin read. Once the service is approved, igni compares each new
upload with the version it would replace
(`compare_grants` in `app.services.identity.extension_grants`). What
counts: the manifest's `workspace` (and `workspacePaths`, empty being the
whole project), `egress`, `secrets`, `tools` (and a tool's hints made
riskier), `toolAudience`, `host`, `rpc`, `hooks` and `service`; and the
impact — a prompt overridden or made `exclusive`, a section replaced,
inserted or appended, a condition (`when`) dropped, an anchor, a command,
a page, a plugin agent, a skill. Anything not literally approved before
is more; name, version, menus, indicators, settings, assets and the text
of a section are not grants. The same or less goes live at once. More is
stored **held**: the upload still answers 200, with `"held": true`, a
`reason` and the `added` grants, and machines keep receiving the approved
version. A newer upload replaces a held one — held again if it asks for
more, live (and the held one discarded) if not. A pending service's
uploads are never held: its approval is the review.

```
GET  {igni}/v1/portal/held-updates                               → [{client_id, service, extension_id, name, approved_version, version, sha256, uploaded_at, comparison: {wider, added: [{kind, label, item, was}]}}]
POST {igni}/v1/portal/clients/{client_id}/held-update/approve    → the client; the held version is now the one distributed
POST {igni}/v1/portal/clients/{client_id}/held-update/reject     → the client; the held version is discarded
```

Super admins only (`require_deployment_admin`, as approving a service),
404 when nothing is held, and each decision is in the audit trail with
the version, the approved version and what it added.
`GET /v1/portal/clients` gives each client's `held_version`, and
`GET /v1/portal/clients/{client_id}` the `held_update` in full. An id another approved service already ships
is refused with 409, and so is approving a second service that ships it.
A bundle that declares an asset igni does not hold is refused (§8.2): a
service uploads its assets first.

**How it runs on a machine: the `python` packaging.** The extension is a
Python package that speaks MCP over stdio, and its bundle carries
everything needed to install it with no network — the extension's own
wheel and every dependency's, for each platform igni runs on. igni runs
on CPython 3.12, so the wheelhouse holds `cp312` (or pure-Python) wheels
for macOS 26 and later on arm64, Linux x86_64 and Windows x86_64. What it
runs that is not Python ships with it as assets (§8.2). The manifest says:

```json
"python": {
  "package": "igni_artifacts_extension",    // the distribution to install
  "entry": "igni-artifacts-extension",       // its console script
  "wheels": "wheels",                        // the wheelhouse, a directory in the bundle
  "mcp": {"transport": "stdio"}
}
```

`python` is a packaging like `sidecar`, `endpoint` and `dev`, and exactly
one is declared. The host installs it into a private environment for that
extension and version, from the wheelhouse only (never an index), and
starts `entry` with the extension's environment (§9). A developer runs
the same manifest from a source checkout: a dev install builds the
wheelhouse from the directory instead of reading one.

**What the super admin sees** (`GET /v1/portal/clients/{client_id}`): the
manifest — pages, menus, commands, indicators, settings, what it does to
igni's prompts, permissions, the assets it ships — the plugin's own
agents and skills, the agents it overrides with files of its own
(`agent_overrides`), and its impact, collisions and dropped edits
(`review`, §8.1).

**Distribution**, to a signed-in machine, once the service is approved
**and** an org admin has switched the extension on for the person's
group (§8.3) — or, with an app's token, for that app (§11):

| Call | Answers |
|---|---|
| `GET /v1/extensions` | `{"producer": {"kind": "person"\|"app", "id": …}, "group": group_id \| null, "group_name": name \| null, "extensions": [{extension_id, version, name, description, service_url, sha256, signature, publisher_jwks, published_at, groups, apps}], "priority": [extension_id, …]}` |
| `GET /v1/extensions/{extension_id}/bundle` | the archive, `application/gzip`, with `X-Igni-Bundle-Signature` and `X-Igni-Bundle-Sha256` |
| `GET /v1/extensions/{extension_id}/assets/{sha256}` | one asset the distributed manifest declares, `application/octet-stream`, with `X-Igni-Asset-Sha256` and `ETag`; one `Range` answered `206` (§8.2) |

A person is in exactly one group of their organisation. Top-level `group`
is that group, read at the time of the call (null only for somebody in no
organisation, who is listed nothing); each extension's `groups` is every
group in that organisation it is on for — an extension can be on for many.
Only extensions whose `groups` contain the person's `group` are listed,
`priority` names only those, and the bundle and assets of any other are a
404 to that person.

The host on each machine verifies the signature against `publisher_jwks`
and the archive's SHA-256 before installing, keeps the extension in step
with the deployed service, and removes one that is no longer listed —
its service gone, switched off for the person's group, or the person
moved to a group that does not have it. It installs and offers only an
extension whose `groups` contain the top-level `group`. It keeps `priority` too, and settles collisions by it (§8.1).

### 8.1 What an extension changes, and where two collide

An extension may take over a great deal of igni: any of its prompts — the
main agent's (`main`), plan mode's (`plan_mode`), any agent's
(`agent:<name>`) — a slash command, the text at a `{{ANCHOR}}`. Per
prompt, its manifest either **overrides** it (`contributes.prompt.overrides`:
a whole prompt of its own, made of the named sections it lists, optionally
`exclusive`) or **edits** it (`contributes.prompt.edits`: exactly one of
`replace` a named section, `insert` a new section `before`/`after` one,
`append` one at the end). An agent's YAML header is its `frontmatter`
section. igni's prompts and their sections are listed in igni's
`docs/prompt-targets.json` (copied here as
`app/services/identity/prompt_targets.json`); an upload naming a section
of igni's own prompts that this igni does not have is shown on the
Services page (`unknown_targets`). A plugin's agent is not igni's to know,
and is not checked.

igni reads the **impact** off the manifest at upload — those, plus its
tools, pages, menu, indicators, settings, host and RPC grants, egress,
secrets and workspace access — and shows it on the Services page.

**Every tool states its hints.** Each entry of `permissions.tools` is
`{"name", "annotations": {"readOnlyHint", "destructiveHint",
"idempotentHint", "openWorldHint"}}` — MCP's tool annotations, all four
required, a read-only tool never destructive — because igni's
permission modes decide on them (igni's `docs/EXTENSIBILITY.md`, *What a
tool says it is*). An upload that leaves one out, or names a tool twice,
is refused with 400. The impact's `tools` carries each tool's name with
its four hints, and the Services page shows them.

**A claim** is something only one extension can have, spelled
`<kind>:<name>`: `prompt:main` (overriding the main prompt),
`prompt:main#workflow` (replacing one section), `command:/review`,
`anchor:GRAPH_SCHEMA`. Agents are prompts like any other:
`prompt:agent:explorer`, `prompt:agent:architect#frontmatter`. Two
extensions **collide** when they claim the same thing; and an `exclusive`
override collides with every other extension that edits that prompt at
all. An insert or an append (`prompt:main+<name>` where it has to be
named) is never a claim: several stack. Pages, settings, tools and a
plugin's own agents are in the extension's namespace and cannot collide;
grants and egress only add. Hooks (`permissions.hooks`, igni's hook
events — igni's `docs/HOOKS.md`, *Extensions*) never collide: igni asks
the extensions hooking an event in the priority order (below), each
handed what the one before left, whether it rejects or changes the
event; approving the service covers what its extension hooks. An event
igni does not have is refused with 400. An extension that runs no
process of its own (`endpoint`) cannot answer a hook — it is never
asked, and its silence would block every fail-closed event — so an
upload declaring both is refused with 400. The impact's `hooks` lists
each event with `decides` (`PreToolUse`, `PostToolUse`,
`UserPromptSubmit`, `Stop`, `PlanSubmit`: it may stop or change the
event; any other it is only told), and the Services page shows them. A
`when` condition does not avoid a collision.

**An override is the new starting point**, not a rival to other
extensions' edits: their appends land on it, and their inserts and
replacements land where it has the section they name. Where it does not,
the edit is **dropped** — not a collision, but listed: the Services page
shows it on the extension whose edit is dropped (`review.dropped`, each
with the overriding extension as `by`) and on the one whose override
drops it (`review.drops_others`), and igni's extensions panel says so on
every machine.

**igni's own commands** (`app/services/identity/igni_commands.json`, a
copy of igni's `docs/igni-commands.json`) are not anybody's: igni answers
them before any extension, so an upload declaring one is refused with a
400 naming it. A version stored before igni gained the command shows it
on the Services page, and igni switches the claim off on every machine.

**Who wins: the higher priority, every time.** A super admin keeps one
priority order of extensions for the deployment, highest first:

```
GET /v1/portal/extension-priority   → {"entries": [{extension_id, name, service}], "set_by", "set_at"}
PUT /v1/portal/extension-priority   {"order": ["acme.lint", "acme.review"]}
```

The order names every extension of every non-revoked service, each once
(400 otherwise), and is audited. Unset, it is the order the services were
approved in; an extension that arrives after it was set comes last. Every
machine receives it as `priority`.

Every collision goes to the extension higher in it, automatically: nobody
decides collision by collision, and **nothing is held back for a
collision** — an upload is distributed as soon as its service is
approved, whatever it collides with (an approved service's upload that
asks for more is held for that, §8). Each loser loses that claim only; the rest of it works. Whole
prompts are settled before sections:

* of two overrides of one prompt, the higher has it;
* an `exclusive` override holds only above every extension that edits
  its prompt — then their edits of it are off, section replacements
  included; if any of them is higher, the exclusive override is off and
  the edits stand, and the next override of that prompt down the order,
  if there is one, has it;
* of two replacements of one section, two slash commands or two anchors,
  the higher has it.

Every loss says why, beginning `lower priority than <extension>`. The
same order is the one several extensions' edits stack in and their
hooks are asked in.

**The Services page shows the outcome**: `review.collisions` lists each
collision the extension is in — `{claim, extensions, winner, losers}`,
`winner` `null` only for an exclusive override switched off with no
other override left — computed over every non-revoked service's upload,
pending ones included, so a clash is visible before either side reaches
anybody; `review.switched_off` lists what it loses and why.

**Every machine applies the same order**: what lost is switched off
there, with the reason shown in igni's extensions panel. A development
install the order does not name ranks below every extension it does.

The rule is implemented twice, here (`app/services/identity/impact.py`)
and in igni (`src/ember_code/extensions/impact.py`), and both run the
cases in `tests/fixtures/extension_impact_cases.json`, which each
repository carries.

### 8.2 What it runs that is not Python: assets

igni provides no database, JVM or model to an extension. One that runs
them ships them — so it installs on a machine with no network, and
nothing is downloaded on first use. Each is declared in the manifest:

```json
"assets": [
  {"name": "neo4j", "platform": "any",
   "file": "neo4j-community-5.26.0-unix.tar.gz", "size": 158822080,
   "sha256": "ad8ac3398606145502b8f489530bbd39333707ae4668156af16b6086b5b037d7",
   "source": "https://dist.neo4j.org/neo4j-community-5.26.0-unix.tar.gz", "unpack": true},
  {"name": "jre", "platform": "macos-arm64", …},
  {"name": "jre", "platform": "linux-x86_64", …},
  {"name": "jre", "platform": "windows-x86_64", …}
]
```

| Field | |
|---|---|
| `name` | `^[a-z0-9][a-z0-9-]{0,63}$`; where it is laid out on a machine, `<bundle>/assets/<name>/` |
| `platform` | `any`, `macos-arm64`, `linux-x86_64` or `windows-x86_64`. A name is one entry for `any`, or one for each of the three — never some of them |
| `file` | a plain file name, no directory |
| `size`, `sha256` | the bytes, pinned: at most 1 GiB; lower-case hex |
| `source` | `https://` — where the bundle builder fetched it, at build time; shown to the super admin |
| `unpack` | `.tar.gz`, `.tgz` or `.zip` unpacked on the machine, nothing reaching outside itself |

The manifest is inside the signed bundle, so **the bundle's signature
covers every asset through its digest**. The bytes travel beside the
bundle, one upload per digest, so a new version that keeps its Neo4j
uploads only its archive and a machine downloads only its own platform's:

```
HEAD {igni}/v1/clients/{client_id}/extension/assets/{sha256}
→ 200 held, 404 not

PUT {igni}/v1/clients/{client_id}/extension/assets/{sha256}
Content-Type: application/octet-stream
X-Igni-Asset-Signature: <compact JWS by the registered key over {"asset": <sha256>, "size": <bytes>}>
<the bytes>
→ 200 {"sha256", "size"}
```

igni refuses an asset whose signature does not verify against the
client's registered key, whose claim names another digest or size, or
whose bytes are not the digest named; and a bundle that declares an
asset this client has not uploaded, or at another size. Allowed while
pending, refused once revoked; at most 1 GiB each. An asset belongs to the
client that uploaded it, and goes when no version that client holds
declares it.

Both ways the bytes stream. igni reads an upload as it arrives, hashing
and counting it, and refuses it the moment it passes the size its
signature claims; nothing of a refused upload is kept. It stores each
asset as a Postgres large object, so neither direction holds the file in
memory, and the object is unlinked whenever its asset goes.

A signed-in machine downloads `GET /v1/extensions/{extension_id}/assets/{sha256}`
— only an asset the distributed manifest declares — for its own platform.
The answer carries `Content-Length`, `ETag: "<sha256>"` and
`Accept-Ranges: bytes`, and a single `Range: bytes=<first>-[<last>]` (or
`bytes=-<n>`) is answered `206` with `Content-Range`, so a download cut
off part-way resumes where it stopped; a range past the end is `416`. A
multi-range request gets the whole asset. The machine checks what it
downloaded against the manifest's size and digest, and lays it out
read-only for the extension's process (`IGNI_EXTENSION_ASSETS`). The
`igni-service` SDK does all of this: `igni-service-bundle` fetches the
assets from their `source` into `extension-bundle.assets/<sha256>`, and
the service uploads the ones igni does not hold before the bundle.

### 8.3 Who gets an extension: per group, off by default

A super admin approving a service is **trust**. Whether anybody *uses* its
extension is the org admin's, per group, and it is **off** until they
switch it on — a newly approved service reaches nobody.

| Call (org admin) | Does |
|---|---|
| `GET /v1/portal/groups/{group_id}/extensions` | `{"extensions": [{extension_id, name, description, service_url, enabled}]}` — every approved extension, with this group's switch |
| `PUT /v1/portal/groups/{group_id}/extensions/{extension_id}` | `{"enabled": true \| false}` → 204. Only an approved service's extension can be switched on (404 otherwise); switching on what is on, or off what is off, changes nothing |

The Default group can have extensions too: it is read-only as the copy of
what igni ships, but whether its people get an extension is not part of
that copy. Everybody is in exactly one group: somebody nobody has placed
yet is put in the Default group when they first ask, as for the group
pack; putting a person in another group moves them out of theirs; taking
them out of a group puts them back in Default. Enablement is not a group *entry*: an
entry is a file igni writes from the group pack, and an extension never
travels in the pack.

**The person's group in their session.** The CLI session token
(`POST /v1/cli/token`, `token_use` = `session`) carries `group` — the id
of the person's one group in `org` when it was issued. It is a snapshot
for a backend that must know a producer's group without asking igni
server; it is never the only thing a decision rests on, because a session
lasts 30 days and a person moves groups. `GET /v1/extensions` answers
with the group read at the time, and that is what a machine installs by.
One group, not a list: `org_group_members` holds one row per person
(`uq_org_group_members_user_one_group`).

## 9. An extension's token for its service

The extension never holds the person's igni session. The host does the
token exchange (§3) for it, for **its own service only**, and hands over
the result. The host starts the extension with three variables — these
are the extension's environment on a person's machine, not settings of
any server:

```
IGNI_SERVICE_URL=<the service's URL: from the distribution record, or the
                  extension's own serviceUrl setting for a development install>
IGNI_SERVICE_TOKEN_URL=<a loopback URL on the host>
IGNI_SERVICE_TOKEN_SECRET=<a secret for this process only>
```

```
GET {IGNI_SERVICE_TOKEN_URL}
Authorization: Bearer {IGNI_SERVICE_TOKEN_SECRET}
→ 200 {"access_token": "...", "expires_in": 600, "service_url": "..."}
→ 503 when nobody is signed in to igni
```

The host caches the token and fetches a new one shortly before it expires.
The extension sends it to its service as `Authorization: Bearer`, and the
service checks it as §4 says, with `aud` = its own URL.

### 9.2 An app's or a space's token for a service

In an app's or a space's VM — or the app itself, for one of its
spaces — there is no person's session: the same token exchange (§3)
takes the producer's own igni token instead, under the same rules — any
service URL as `audience`, `organisation` optional and only its own
(`invalid_target` otherwise), ten minutes:

```
POST {igni}/v1/oauth/token
grant_type=urn:ietf:params:oauth:grant-type:token-exchange
subject_token=<the app's token (§11.1) or the space's (§17.2)>
subject_token_type=urn:ietf:params:oauth:token-type:access_token
audience=<the service's URL>
→ 200 {"access_token", "expires_in": 600, "issued_token_type": "urn:ietf:params:oauth:token-type:access_token"}
→ 400 invalid_grant: not a session, app or space token, or the app or space is gone
```

The result is the producer's own kind of token, not a person's — a
service that accepts only `user` tokens refuses it, rather than taking an
app for a person:

| Producer | `token_use` | `sub` | Claims |
|---|---|---|---|
| an app | `app` | the app's id | `org`, `kind` = `app`, `name` |
| a space | `space` | the space's id | `org`, `kind` = `space`, `app` (its app's id), `external_id` (the app's name for its user) |

`aud` is the service's URL in both, and neither has a `group`. A service
that serves apps or spaces accepts these `token_use` values with `aud` =
its own URL; the host hands them to the extension exactly as a person's
(the loopback above).

### 9.1 The extension's page: a cross-origin call with the token

The extension's page gets the same token from the host (`bridge.service`)
and calls the service directly, from igni's window — another origin. The
service answers that call under CORS, and only that call:

```
OPTIONS {service}/v1/notes
Origin: <igni's window>
Access-Control-Request-Method: POST
Access-Control-Request-Headers: authorization, content-type
→ 204 Access-Control-Allow-Origin: *
      Access-Control-Allow-Methods: DELETE, GET, HEAD, PATCH, POST, PUT
      Access-Control-Allow-Headers: Authorization, Content-Type
      Access-Control-Max-Age: 600

POST {service}/v1/notes
Origin: <igni's window>
Authorization: Bearer <token>
→ ... Access-Control-Allow-Origin: *
```

- **Any origin, because the token is the authority.** A page without a
  token gets nothing it could not get with `curl`. The yes is on routes
  that take a person's token; a refused token's 401 carries it too, so the
  page can fetch a new token and try once more.
- **Never credentials.** No `Access-Control-Allow-Credentials`, and the page
  sends `credentials: "omit"`. A browser sends no cookies on the call and
  shows the page nothing from one that sent them, so a person's session on
  the service's own website (§5) cannot be used cross-origin.
- **Not for the website or igni.** Routes signed in by the website's cookie
  (§5), the sign-in routes themselves, and igni's own calls (§6) give no
  cross-origin yes, and a preflight for the sign-in routes or §6's paths is
  not answered with one.
- A preflight that does not ask for `Authorization`, or asks for any header
  but `Authorization` and `Content-Type`, is refused (403).
- WebSockets are not governed by CORS: a page's socket sends the token in
  its first frame, `{"type": "auth", "token": …}`.

The SDK does all of this: `igni.install(app)`.


## 10. Notifications

Telling people things is the floor's job, and there are two paths:

- **To other people** — a comment on their document, an access request —
  the extension's *service* tells igni server (below). It needs the
  directory, delivery to other machines and a record of who read what.
- **To yourself** — your export is ready, your index finished — the
  extension's *process* tells the igni host on the same machine through
  the app API (§10.2). It never leaves the machine.

Both appear in the same place in igni and count toward the same badge on
the extension.

### 10.1 To other people: the service, through igni server

A service says *what happened and who it concerns*; igni server works out who that really is, stores one
delivery per person, wakes their machines, and records whether each was
delivered and read. The igni host on a person's machine shows them in
igni's own UI. The extension's process and its UI take no part, and
nothing polls.

**From the service** (service token, for the token's organisation):

```
POST {igni}/v1/service-notifications
{"notifications": [{
  "kind": "comment",                         // the extension's own word for what happened
  "title": "Runbook",                        // what it is about
  "body": "Ada: can we drop step 3?",        // one line
  "actor": "ada@acme.com",                   // user id or address; optional
  "target": {"page": "igni.artifacts",       // one of the extension's own pages
             "params": {"slug": "runbook", "comment": "c1"}},
  "event_key": "comment:c1",                 // the same event is delivered once per person
  "recipients": ["max@acme.com", "u-123"]    // user ids or addresses
}]}
→ 200 {"delivered": 3}
```

At most 100 notifications and 1000 recipients per notification in one
call. The service SDK offers this as `host.notify(notification)`.

**What the floor decides**, so no service has to:

- *Who.* Each recipient is resolved — a user id or an address — to an
  active member of the token's organisation. Anybody else is dropped,
  never told elsewhere. The actor is never told about their own act.
- *Once.* A person is told about an `event_key` once, however often the
  service retries. A batch is processed in order, and the check runs per
  person after recipients are resolved to accounts, so **the first
  notification in the batch that reaches a person with a given
  `event_key` wins** and later ones with that key skip them — whether
  they were named by id or by address. Give the verbs of one event the
  same key, most specific first: `mention`, then `comment`, then `reply`
  for `comment:<id>`, and an owner who is also mentioned gets only the
  mention. The check is per person, not global: `shared:<doc>` sent to
  five grantees reaches each of them once. Keys are scoped to the
  extension, so another extension's `comment:c1` is a different event.
  A notification with no `event_key` is never deduplicated.
- *From whom.* The notification's source is the extension the calling
  service ships (§8), recorded by igni rather than taken from the request,
  so one service cannot post as another. A `target.page` must be one of
  that extension's pages.

**On a person's machine** the igni host, which holds the session, uses
these. A notification is stored in the database of the organisation it
happened in, and a person can be an active member of several: every call
covers **all of them**, so the person has one list, one count and one
"mark read" whichever organisation a notification came from.

| Call | Does |
|---|---|
| `GET /v1/notifications?unread_only=&source=&limit=` | the person's notifications from every organisation they are in, merged newest first: `id`, `org_id` (where it happened), `source` (extension id), `kind`, `title`, `body`, `actor`, `target`, `created_at`, `delivered_at`, `read_at`. `limit` is over the merged list. Listing marks what it answers delivered — only that. |
| `GET /v1/notifications/counts` | `{"unread": n, "by_source": {"igni.artifacts": 2, ...}}`, summed over every organisation — the badge on each extension |
| `POST /v1/notifications/read` `{"ids": [...]}` or `{"source": "..."}` or `{}` (all) | marks read, in every organisation; ids need not say which |
| `GET /v1/notifications/stream` | server-sent events carrying only a cursor: "something changed for you", whichever organisation it changed in. The host lists again when woken; correctness never depends on the stream. |

A membership that ends takes its organisation's notifications with it:
from then on that organisation is not read, so its notifications leave
the list and the counts and cannot be marked. They are kept, not
deleted, and come back if the person rejoins.

### 10.2 To yourself: the extension, through the app API

The extension's process is an MCP server talking to the igni host over
stdio, so it tells the host directly, with an MCP notification:

```
{"jsonrpc": "2.0", "method": "notifications/igni/notify",
 "params": {"kind": "export_ready", "title": "Runbook.pdf",
            "body": "Your export is ready",
            "target": {"page": "igni.artifacts", "params": {"export": "e1"}},
            "event_key": "export:e1"}}
```

The same shape as §10.1 without `recipients` or `actor`: the person is
whoever is using this igni. The host checks that `target.page` is one of
this extension's pages, keeps the notification and its read state on the
machine (never sending it to igni server), drops a repeated `event_key`,
and shows it beside the notifications that came from igni server. The
extension SDK offers this as `ext.host.notifications.notify(notification)`.


## 11. Apps and platform clients: the cloud's producers and plumbing

In the cloud (igni's `docs/CLOUD.md`) there are **two kinds of producer**,
not connected to each other: **people**, each in exactly one group, and
**apps**, which act only as themselves — in no group, never for a person.
Each producer gets a microVM of its own. Two more parts of the
deployment act for no organisation at all: the **gateway** and the
**node agent**, which are **platform clients**.

### 11.1 Apps

An org admin registers an app on the portal (*Apps*), in their
organisation. The app generated its own key pair, as a service does; the
admin pastes the **public** key, and nothing secret is typed or shown
anywhere.

| Call (org admin, in the portal's organisation) | Does |
|---|---|
| `GET /v1/portal/apps` | `{"apps": [{app_id, client_id, name, owner_user_id, owner_email, thumbprint, vm_profile_id, created_at}]}` |
| `POST /v1/portal/apps` `{"name", "owner_user_id", "jwks": {"keys": [public JWK]}}` | 201, the app; approved by that act. The owner must be an active member; a key another client holds is a 409; a private JWK is a 400 |
| `GET` / `PATCH /v1/portal/apps/{app_id}` `{"name"?, "owner_user_id"?}` | read, rename, change owner |
| `PUT /v1/portal/apps/{app_id}/keys` `{"jwks"}` | a key rotation: the old keys stop at once |
| `DELETE /v1/portal/apps/{app_id}` | 204; token requests stop at once, tokens already issued expire within ten minutes |
| `GET /v1/portal/apps/{app_id}/extensions` | every approved extension with this app's switch, the shape of §8.3 |
| `PUT /v1/portal/apps/{app_id}/extensions/{extension_id}` `{"enabled"}` | 204; off until switched on, exactly as a group's — and an app's list has nothing to do with any group's |

The app's `app_id` is its `client_id` at the token endpoint. It obtains
tokens as itself with `client_credentials` and `private_key_jwt` (§3) —
`organisation` may be left out, and naming any but its own is
`invalid_target`. The token: `token_use` = `app`, `sub` = the app id,
`org`, `kind` = `app`, and **no `group`**. With it the app reads
`GET /v1/extensions` — its own enabled list, `group: null`,
`producer: {"kind": "app", …}` — runs commands in its own VM (§13), and
is what the gateway authenticates.

A registered client of kind `app` belongs to one organisation
(`registered_clients.org_id`, a constraint makes the two go together);
the Services page lists only services and platform clients. A deleted
organisation takes its apps with it.

### 11.2 Platform clients

The gateway and the node agent register like a service — `POST /v1/clients`
with `"kind": "platform"` — and a super admin approves them on the
Services page after comparing the thumbprint. A platform token
(`client_credentials`, no `organisation`) has `token_use` = `platform`
and no `org`; it is what every `/v1/platform/...` route below requires,
and nothing else accepts it. Neither an app nor a platform client may
upload an extension.

### 11.3 The gateway's feed of producers

The gateway provisions a producer's VM before its first request, and
again when its profile changes, from a feed (platform token). The feed
also carries each producer's **VM profile**, which the gateway keeps
with the producer: a wake reads it there and never asks igni server.

```
GET /v1/platform/producers?since=<cursor>&limit=500&wait=25   // limit 1–1000, default 500; wait 0–30 s
→ 200 {"producers": [{"org_id", "producer": {"kind": "person"|"app"|"space", "id"}, "incarnation",
                      "owner": {"kind": "app", "id"} | null,
                      "active", "removed", "erase_at", "profile_version", "packages_version",
                      "profile": {"profile_id", "profile_name", "memory_mib", "normal_memory_mib",
                                  "expected_memory_mib", "vcpus", "idle_before_pause_s",
                                  "packages": {"apt", "pip", "npm"}, "setup_script",
                                  "volume_max_gib", "erase_after_idle_days"} | null,
                      "volume_gib", "legal_hold", "changed_at"}],
       "cursor": "<opaque: pass it back as since>"}
→ 400 for a cursor the feed did not give
```

- **What is listed:** every producer whose state differs from what the
  feed last reported — oldest change first, so one may appear again.
  Without `since`, every producer there is (and was). A person appears
  when they join, are deactivated (by an admin or by SCIM), leave, or
  get another effective profile (a group move, a profile edited, a cap
  lowered); an app when it is registered, the cloud is enabled or
  disabled for it, it is deleted, or its profile changes. A deleted
  organisation's producers are listed inactive.
- **`producer.kind`:** `person`, `app` or `space` (§17).
- **`owner`:** a space's app, `{"kind": "app", "id": <app_id>}`, so the
  gateway provisions a space with its owner before any of its tokens has
  connected; kept on the row once the space is gone. `null` for a person
  or an app.
- **`active`:** a person is an active member and not deactivated; an app
  is registered **and has the cloud enabled** (a working VM credential,
  §16); a space is declared and its app registered. Inactive and not
  removed only **suspends** the VM — its volume and keys are kept: a
  person deactivated (by an admin or by SCIM), an app whose cloud was
  disabled or whose credential was revoked. Such a producer may come
  back `active`.
- **`removed`:** the owner removed the producer for good: it is never
  usable again, and its VM is suspended at once; the gateway **erases
  its state, volume and keys at `erase_at`** (§18). Only: a space deleted by
  its app; an app deleted (and its spaces with it); a person **removed
  from the organisation** (the organisation owns that VM's data), their
  account deleted, or the person erased (`POST /v1/portal/erasure`); an
  organisation deleted (every producer of it). A removed row is always
  `active: false` and is never changed again but for `erase_at`.
  Once gone, a row keeps the versions it last had.
- **`erase_at`:** on a removed row, when the gateway erases the VM's
  state, volume and keys (and has igni erase the producer's data in the
  organisation's igni database: `igni-cloud db erase-producer --org
  <org_id> --producer <kind>:<id> --incarnation <incarnation>`, §18.3): at once for a space, an app, an
  account or an organisation deleted and for a person erased on request;
  the organisation's retention after a member's removal. `null` while a
  legal hold stands (or on a row that is not removed). A hold placed or
  released lists the row again with the new `erase_at`.
- **`incarnation`:** which life of the producer the row is, and so which
  VM: **key a VM by producer and incarnation.** A person's is their
  membership in the organisation, new each time they are added; an
  app's and a space's is their own id (a re-declared space is a new
  id). A person removed and added again is listed twice: the old
  incarnation `removed: true`, then a new one, active — a fresh VM, with
  nothing of the old. A removed incarnation never comes back.
- **`profile_version`, `packages_version`, `profile`, `volume_gib`:**
  the VM profile the producer's VM gets (§12): `profile` holds its
  values (effective, clamped to the caps), the two versions are its
  hashes. The feed is the only place the gateway learns a producer's
  profile; it hands it to the node agent with every start. `profile` changes exactly when
  `profile_version` does, and every change to what decides it — a group
  move, a group's, an app's or the default profile, a profile edited
  (its packages and setup script too), a cap lowered — lists the
  producer again, with the new one. The deployment's own caps, in force
  until a super admin sets some, change only with a redeploy, which no
  trigger sees: igni server works every producer out afresh when it
  starts while they are in force. `profile` is `null` once the row is
  removed. A producer whose volume an admin grew (§12.1) is listed
  again with the larger `volume_gib`; it only ever grows.
- **`legal_hold`:** a legal hold stands on the producer (§18.2): on the
  person in this organisation, or on the app — which covers its spaces.
  The gateway erases nothing of the VM after inactivity while it does
  (§12.2); a hold placed or released lists the row again at once.
- **`changed_at`:** when igni server saw the change, not necessarily when
  it was made.
- **`cursor`:** unchanged when nothing is new.
- **`wait`** makes it a long poll: answered as soon as anything is newer
  than `since`, or empty with the same cursor when `wait` runs out.
  Every mark sends `NOTIFY` on the shared plane, which wakes the waiting
  request at once (changes made in an organisation's own database — a
  space deleted, a credential revoked — notify from igni server's code,
  and a waiting request also looks again every second). A removal or
  deactivation reaches a waiting gateway in well under a second.
- **How changes reach it, and why none is missed.** Every table that
  decides what a producer is — memberships, accounts, apps and their
  registrations, groups and their members, profiles, the caps, VM
  credentials, spaces (§17), volumes grown (§12.1), organisations — carries a trigger that
  **marks** the producers a change touches, in the transaction that
  made the change; no code path can forget to. When the feed is read,
  one transaction at a time (a lock it lets go of only by committing)
  works out what the marked producers are now and numbers, from one
  sequence, each that differs from what the feed last said. Only that
  transaction draws numbers, so none is committed after a larger one,
  and a cursor never skips a change, however the changes' own
  transactions interleaved. A read costs what changed, not how many
  producers there are. The retention round **reconciles** once a day —
  works every producer out afresh — as a safety net; the tests hold
  that it finds nothing a mark missed.
- **Retention of the feed's rows.** A removed row is deleted by the
  daily retention round `PRODUCER_FEED_RETENTION_DAYS` (default 30, `0`
  keeps them) **after its `erase_at`**, once the gateway has been handed
  its latest version; a person erased on request, as soon as `erase_at`
  has passed and it was handed over. An inactive row that was not
  removed is **never purged**: its VM still exists, suspended, and the
  row is how the gateway knows it.

## 12. VM profiles

An org admin defines named **profiles** and assigns one to each group
and each app; a person gets their one group's, and a group or app with
none gets the organisation's **default** profile ("Standard": 1024 MiB
normally and up to 4096 MiB, 1 vCPU, paused after 10 s idle — made the first time anything asks, and
never deletable). A profile holds:

| Field | Meaning |
|---|---|
| `memory_mib`, `vcpus` | the most memory the VM may have (default 4096) and its vCPUs: its size at boot, which cannot change while it runs |
| `normal_memory_mib` | what the VM normally has (default 1024; at most `memory_mib`, else 422). The node agent keeps the rest in the VM's balloon, gives it while the VM needs it (a native build) and the node has room, and takes it back when the need is over and before the VM pauses; a change applies at once |
| `idle_before_pause_s` | seconds idle before the VM is paused (default 10); `null` is never |
| `expected_memory_mib` | what the node expects the VM to use when it places it; `null` (default): the node's rule, a share of `memory_mib`; at most `memory_mib`, else 422 |
| `packages` | `{"apt": [...], "pip": [...], "npm": [...]}` — one package name per entry, nothing a shell would split |
| `setup_script` | run as root when the profile's read-only layer is built; at most 64 KiB |
| `volume_gib` | the volume a new VM gets, in GiB (default 10) |
| `volume_max_gib` | how far an admin may grow one producer's volume (§12.1), in GiB (default 50); at least `volume_gib`, else 422 |
| `erase_after_idle_days` | **apps and their spaces only:** a VM not woken for this many days has its memory, snapshots, volume and keys erased; the app stays and its next wake starts fresh (§12.2). `null` (default) is never; 1–3650. A person's VM is never erased this way while they are a member |
| `internet` | whether a VM with the profile may reach the internet (default `false`: only what its node allows, `[[network.allow]]`). A security grant, made per profile, never per node; a node grants it only when it may (§12.4) |

The **platform's caps** — the largest ceiling, the most vCPUs, the
longest idle (`null`: any, never included), the largest volume a
profile may let a VM grow to (`max_volume_gib`) and the longest an app's
VM may go unwoken before it is erased (`max_erase_after_idle_days`;
`null`, the default: any, never included — set, every profile must
erase within it, and one saved before is served clamped) — are the super admin's. A
profile that asks for more is refused (400, saying which); one saved
before a cap was lowered is served **clamped** to it and shows
`within_caps: false` (a volume's size and maximum clamped to
`max_volume_gib`). Until a super admin sets them the caps are the
deployment's (`CLOUD_VM__CAP_MAX_MEMORY_MIB`, `__CAP_MAX_VCPUS`,
`__CAP_MAX_IDLE_BEFORE_PAUSE_S`, `__CAP_MAX_VOLUME_GIB`,
`__CAP_MAX_ERASE_AFTER_IDLE_DAYS`: 8192 MiB, 4 vCPUs, 3600 s, 200 GiB and
none by default). The Standard profile, and the value a
profile body leaves out, are the deployment's too (`CLOUD_VM__STANDARD_*`:
name, memory, normal memory, vCPUs, idle, volume and its maximum). An organisation's **quota** of awake VM time
(`awake_vm_seconds_per_month`, null unlimited) is set by a super admin
and **stored, not enforced** — nothing counts awake time yet.

| Call | Who | Does |
|---|---|---|
| `GET /v1/portal/vm-caps` | anybody signed in | the caps |
| `PUT /v1/portal/vm-caps` | super admin | set them |
| `GET /v1/portal/orgs/{org_id}/vm-profiles` | that org's admins, super admin | `{org_id, caps, quota, default_profile_id, profiles: [{id, name, is_default, …fields, version, packages_version, within_caps}], groups: [{id, name, vm_profile_id}], apps: [{id, name, vm_profile_id}]}` |
| `POST` / `PUT` / `DELETE /v1/portal/orgs/{org_id}/vm-profiles[/{profile_id}]` | the same | define (201), replace whole, delete (204; its groups and apps go back to the default) |
| `PUT /v1/portal/orgs/{org_id}/groups/{group_id}/vm-profile`, `.../apps/{app_id}/vm-profile` `{"profile_id": id \| null}` | the same | assign; null is the default |
| `GET` / `PUT /v1/portal/orgs/{org_id}/vm-quota` `{"awake_vm_seconds_per_month"}` | read: the same; write: super admin | the quota, with `enforced: false` |

The organisation is in the path because the super admin defines and
assigns profiles in any organisation without being a member of it.
Copying a group copies its profile.

**What a producer's VM gets** reaches the gateway only on its row in the
producers feed (§11.3), which the gateway keeps and hands the node agent
with every start; there is no per-producer call for it:

```
"profile_version", "packages_version",
"profile": {"profile_id", "profile_name",
            "memory_mib", "normal_memory_mib", "expected_memory_mib", "vcpus", "idle_before_pause_s",
            "packages": {"apt", "pip", "npm"}, "setup_script", "volume_max_gib",
            "erase_after_idle_days", "internet"},
"volume_gib", "legal_hold"
```

Values are effective (clamped to the caps). `profile_version` is a hash
of the profile's id and every effective value, so it changes exactly
when anything the VM gets changes — including the producer moving to a
group with another profile, or a cap being lowered. `packages_version`
covers only the packages and the setup script: a new one means the
profile's layer must be rebuilt. A new timeout applies at once; a new
ceiling or CPU count at the producer's next cold boot (igni's
`CLOUD.md` §2). `volume_gib` is the producer's own (§12.1);
`profile_version` covers the profile's volume settings, not one
producer's growth.

### 12.1 Growing one producer's volume

| Call | Who | Does |
|---|---|---|
| `GET /v1/portal/orgs/{org_id}/producers/{person\|app\|space}/{producer_id}/volume` | that org's admins, super admin | the producer's volume |
| `POST` the same `{"gib": n}` | the same | grow it to `n` GiB: 409 if smaller than now (**never shrunk**), 400 past the profile's `volume_max_gib`, 200 and nothing new for the same size; 404 for no such producer. Audited with the size asked for |

The answer: `{"org_id", "producer": {"kind", "id"}, "incarnation",
"volume_gib", "volume_max_gib", "grown_by": {"kind": "person", "id",
"acting_as": "org_admin"|"super_admin"} | null, "grown_at" | null}`.

A producer's volume is its profile's `volume_gib`, or the size it was
grown to if larger — for its current incarnation only (§11.3): a person
removed and added again starts at the profile's. A lower profile size or
maximum later never shrinks a grown volume. **The gateway learns of a
growth from the feed:** the producer is listed again with its new
`volume_gib` (§11.3), and grows the volume; it never shrinks one.

### 12.2 Erasing an app's VM after inactivity

A profile's `erase_after_idle_days` keeps an app from holding data in a
VM nobody uses. When an app's or a space's VM has not been **woken** — by
a connection, an event, a command or a timed wake; not by the gateway's
own provisioning or moves — for that many days, the gateway erases its
memory state, snapshots, volume and keys, as for a removed producer
(§18.3) but **without** its rows in the organisation's igni database, and
the app stays: active, with the same id. Its next wake starts fresh from
the profile's base, with a new volume, a database login granted again (a
new password) and a new VM credential picked up by the node agent.

**Never a person's VM.** A member's workspace is kept for as long as they
are a member; their VM is erased only after they are removed (§18). A
profile assigned to a group is served with the field, and the gateway and
igni server both leave people out.

**A legal hold on the app stops it** (§18.2), for its spaces too. Erased
once, a VM is not erased again until it has been woken since.

The gateway asks first and reports after (platform token):

```
POST /v1/platform/orgs/{org_id}/producers/{app|space}/{id}/vm-resets/check
     {"incarnation", "reason": "idle", "last_woken_at"?}
→ 204 may be erased now
→ 409 a person's VM; the profile does not erase after inactivity; a legal hold (named); another life

POST /v1/platform/orgs/{org_id}/producers/{app|space}/{id}/vm-resets
     {"incarnation", "reason": "idle", "last_woken_at"?, "summary": {…what the node erased, ≤ 8 KiB}}
→ 200 {"org_id", "producer", "incarnation", "reason", "credential", "reset_at"}
```

The report re-checks, is one row in the organisation's audit trail
(method `SYSTEM`, route `producer-vm/reset`, target `app_vm` /
`space_vm`, detail `{"incarnation", "why": "erased_after_idle",
"erase_after_idle_days", "last_woken_at", "credential", "by_client",
"summary"}`), and makes sure the next fresh VM can authenticate: its
credential lived on the erased volume (§16), so a working, picked-up one
is replaced by a new family waiting for the node agent's pickup
(`credential: "reissued"`; `awaiting_pickup` when one was waiting
already, `none` when the cloud is not enabled for the app).

### 12.3 An app or an extension asks for a profile; an org admin decides

A VM runs nothing as root that an org admin has not approved, and a
profile's `packages` and `setup_script` run as root when its layer is
built. So neither an app nor an extension sets its own profile: each
**asks**, and the request changes no VM until an org admin (or the super
admin, as for every profile) approves it.

- **An app** asks with its own token (§11.1), for itself only: `{app_id}`
  must be the token's `sub` (403 otherwise — another app, a person, an
  org admin's session; 401 for no token). A **service** cannot ask for an
  app: a service is the deployment's and owns no app; its token is
  refused here.
- **An extension** asks through its manifest's `vm` section, part of the
  signed bundle its service ships (§8) — the same fields in the
  manifest's camelCase: `packages`, `setupScript`, `memoryMib`,
  `normalMemoryMib`, `expectedMemoryMib`, `vcpus`, `idleBeforePauseS`,
  `volumeGib`, `volumeMaxGib`, `eraseAfterIdleDays`, `internet`. A malformed `vm` is
  refused at upload (400). An extension is the deployment's and is
  switched on per group and per app in each organisation (§8.3), while a
  profile is given per group or app; so its request is **one per
  organisation**, kept the first time anything in it reads requests, and
  approved by each organisation's admin for that organisation alone. The
  profile an approval makes is not given to anybody: the admin gives it
  to the groups and apps that have the extension on, with the calls of
  §12, because only they know whether those groups' other extensions need
  something else (a group has one profile, so two extensions that both
  ask are combined by the admin, in a profile of their own). A version
  whose `vm` asks for nothing drops the request; the profile stays the
  organisation's. A space asks for nothing: its VM gets its app's profile.

**What is asked** is everything a profile says but its name (§12), with
the same limits; a field left out is the deployment's Standard value, and
a field igni does not know is refused (422) rather than dropped. A
request over the platform's caps is refused at once (400, saying which).

| Call | Who | Does |
|---|---|---|
| `PUT /v1/apps/{app_id}/vm-profile-request` | the app | ask. 201 with the request when it is new or differs from the last — **pending**, its decision cleared; 200 when it is the same as the last, which keeps its state (a rejected request sent again stays rejected) |
| `GET /v1/apps/{app_id}/vm-profile-request` | the app | its request; 404 for none |
| `GET /v1/apps/{app_id}/vm-profile` | the app | what its VM gets **now**, whatever is pending: `{profile_id, profile_name, memory_mib, normal_memory_mib, expected_memory_mib, vcpus, idle_before_pause_s, packages, setup_script, volume_max_gib, erase_after_idle_days, internet, volume_gib}` — effective, clamped to the caps |
| `GET /v1/service-vm-profile` | a service (its token, §3) | `{org_id, extension_id, request, profile, assigned: {groups, apps}}` for its extension in the token's organisation: `request` null while the manifest asks nothing; `profile` what an approval made (effective), null before one; how many groups and apps the admin gave it |
| `GET /v1/portal/orgs/{org_id}/vm-profile-requests` | that org's admins, super admin | `{"requests": [...]}`, pending first, then the newest |
| `POST .../vm-profile-requests/{request_id}/approve` `{"version"}` | the same | approve **the version read**: 409 when the request changed since, or is approved already; 400 when it no longer fits the caps (a cap lowered since). The request becomes a profile named `App: <name>` or `Extension: <name>` (a number added when the name is taken) — or, approved before, the profile that approval made is **replaced in place**, keeping its name and everybody it was given to. An app's is given to the app |
| `POST .../vm-profile-requests/{request_id}/reject` `{"reason"?}` | the same | pending only (409 otherwise); nothing changes, the reason (≤ 500 characters) is kept on the request. A rejected request may still be approved later |

A request: `{"id", "org_id", "subject": {"kind": "app"|"extension", "id",
"name"}, "state": "pending"|"approved"|"rejected", "requested": {…every
value, the Standard ones filled in: exactly what would run}, "version",
"packages_version", "within_caps", "requested_by": {"kind":
"app"|"service", "id"}, "requested_at", "decided_by": {"kind": "person",
"id", "acting_as": "org_admin"|"super_admin"} | null, "decided_at",
"reason", "profile_id"}`. `version` is a digest of `requested`;
`packages_version` is the digest a profile with those packages and that
script gets (§12), so the layer the node builds, `pkg-<packages_version>`,
is the one the admin approved.

**The profile keeps what was last approved.** A changed request is
pending again and the VM keeps its profile until an admin decides. An
admin may still edit the profile an approval made like any other; a
later approval replaces it with what was asked.

**The audit trail.** Approving and rejecting are portal mutations, so
each is a row of the audit trail, like every portal change, with target `vm_profile_request`
and detail `{"subject", "state", "version", "packages_version",
"internet", "profile_id"}` — not the reason, which stays on the request. An app's
`PUT` is a row too, attributed to `app:<app_id>`, with `{"app_id",
"version", "packages_version", "internet", "changed", "state"}`; defining or
replacing a profile records `{"name", "version", "internet"}`; an extension's
request comes from the bundle upload, which is audited as itself (§8).
A refused attempt is recorded like any other. Deleting an app forgets its
request; the profile an approval made stays the organisation's.

**Example: a headless browser.** An app whose agent drives Chromium
through Playwright's MCP server asks for (the VM's base is Ubuntu 24.04):

```json
PUT /v1/apps/{app_id}/vm-profile-request
{
  "memory_mib": 4096, "normal_memory_mib": 2048, "vcpus": 2,
  "packages": {
    "apt": ["ca-certificates", "curl", "unzip", "fonts-liberation", "libnss3", "libatk-bridge2.0-0t64",
            "libgbm1", "libasound2t64", "libxkbcommon0", "libxcomposite1", "libxdamage1", "libxrandr2",
            "libpango-1.0-0", "libcups2t64"],
    "npm": ["@playwright/mcp@0.0.84"]
  },
  "setup_script": "#!/bin/sh\nset -eu\n# Chromium from inside the network, never Playwright's CDN.\nmkdir -p /opt/chromium /opt/google/chrome\ncase \"${APT_MIRROR:-}\" in\n  file://*) cp \"${APT_MIRROR#file://}/playwright/chrome-linux.zip\" /tmp/chromium.zip ;;\n  *) curl -fsSL https://artifacts.bank.internal/playwright/chrome-linux.zip -o /tmp/chromium.zip ;;\nesac\nunzip -q /tmp/chromium.zip -d /opt/chromium && rm /tmp/chromium.zip\n# Where Playwright MCP's default channel (chrome) looks.\nln -sf /opt/chromium/chrome-linux/chrome /opt/google/chrome/chrome\n"
}
```

Installing `@playwright/mcp` downloads no browser; Playwright normally
**downloads** Chromium from the internet when asked to. An air-gapped
deployment has no such route: the browser comes from the organisation's
own package mirror — a real Chromium `.deb` the mirror carries (on
Ubuntu 24.04 the archive's `chromium` is only a stub for the snap, which
does not run in a VM) — or is placed by the setup script from inside the
network, as above: from the offline mirror's directory when the layer is
built with no network at all, or from an internal artifact store. The
agent's MCP server then runs `npx @playwright/mcp --headless` (the VM has
no display), or names the browser with `--executable-path`. The igni
SDKs' docs carry the same example.

### 12.4 Internet access is granted per profile

A VM reaches only what its node allows (`[[network.allow]]`, e.g. the
organisation's package mirror) unless its profile says `"internet": true`.
That is the only switch for one VM: an org admin sets it on a profile, or
approves it when an app or an extension asks for it (`"internet": true` in
the request, `internet` in a manifest's `vm`). The portal shows it on every
profile and every request as a security grant, and the audit trail records
it on each define, replace, ask, approval and rejection (§12, §12.3).

The feed carries it in each producer's `profile` (§12); `profile_version`
changes when it does. A node grants it only when it may: a node whose
`[network] egress = "internet"` has the capability; an air-gapped node, or
one without the capability, starts the VM internal-only and says so in its
log (Fleet's `docs/node-agent.md`). A change applies at the VM's next start
or resume at the latest. Every profile that existed before has
`internet: false` (t0026), and a request's `version` leaves `internet` out
while it is `false`, so nothing already decided goes back to pending.

## 13. Commands in a producer's VM

A command runs as root in one producer's VM with `/bin/sh -c`. Who may
ask, exactly:

| Caller | May run in |
|---|---|
| a producer — a person with their session, an app with its token | its own VM only |
| an org admin | any VM of their organisation (people and apps) |
| the super admin | any VM of the platform (`org_id` names the organisation) |

A command for **one** VM wakes it. A **job** — one command for a
group's, a profile's, an organisation's or (the super admin only) every
organisation's VMs — wakes nobody: each VM in its scope is handed it at
its next wake, as a row of its own, for as long as the job is not
cancelled (a VM that joins the scope later gets it too). A platform job
is written into every organisation that exists at that moment, under one
job id.

| Call (person session or app token) | Does |
|---|---|
| `POST /v1/vm/commands` `{"producer": {"kind", "id"}, "org_id"?, "command", "timeout_s"?}` | 202, the record; `timeout_s` 1–3600, default 600; `command` at most 16 KiB |
| `GET /v1/vm/commands?org_id=&producer_kind=&producer_id=&job_id=&limit=` | the **audit log**, newest first: an organisation's for its admins and the super admin, the caller's own VM's for anybody else |
| `POST /v1/vm/commands/{command_id}/cancel?org_id=` | 204 while nobody has been handed it; 409 after |
| `POST /v1/vm/jobs` `{"scope": {"kind": "group"\|"profile"\|"org"\|"platform", "id"?}, "org_id"?, "command", "timeout_s"?}` | 202 `{"jobs": [...]}`, one per organisation reached; org admins and the super admin |
| `GET /v1/vm/jobs?org_id=` | jobs with how many VMs were handed each and how many finished it |
| `POST /v1/vm/jobs/{job_id}/cancel?org_id=` | stop handing it out; VMs already handed it finish it |

A record: `command_id`, `org_id`, `producer`, `command`, `timeout_s`,
`job_id`, `wake`, `requested_by: {kind, id, acting_as: producer|org_admin|super_admin}`,
`state` (`queued` → `handed_out` → `running` → `finished`, or
`cancelled`), `handed_to` (the node agent's client id), `created_at`,
`handed_out_at`, `started_at`, `finished_at`, `exit_code`, `output`
(the last 64 KiB), `error`. Each request is also in the audit trail.

**The node agent's contract** (platform token, HTTP and JSON; streaming
output through the gateway is not part of it):

1. **Which VMs to wake.** `GET /v1/platform/wakes` →
   `{"producers": [{"org_id", "producer": {"kind", "id"}, "reasons": ["command", "event"]}]}`
   — producers with a command for their VM alone, or an event (§15),
   waiting. The gateway polls it and wakes those VMs. A job never puts a
   producer here.
2. **At every wake**, for that VM:
   `POST /v1/platform/orgs/{org_id}/producers/{kind}/{id}/commands/pull` →
   `{"commands": [{command_id, command, timeout_s, job_id, requested_by, created_at}]}`,
   oldest first: commands for this VM, and every active job in its
   scope it has not had. Each is now `handed_out` to the caller; one
   not acknowledged or finished within ten minutes is handed out again
   at a later pull, so a node agent that dies loses nothing.
3. **Starting one:** `POST /v1/platform/orgs/{org_id}/vm-commands/{command_id}/ack`
   `{"started_at"?}` → 204 (409 unless handed out and waiting).
4. **Finishing one:** `POST /v1/platform/orgs/{org_id}/vm-commands/{command_id}/result`
   `{"exit_code": int|null, "started_at"?, "finished_at"?, "output"?, "error"?}`
   → 204; a second result is a 409. `exit_code` null with `error` when it
   could not run or was killed at its timeout; `output` is stdout and
   stderr together, cut from the front past 64 KiB.

## 14. A person's MCP approvals

In the cloud igni in a person's VM keeps no approvals file; it reads and
writes the person's here, with their session. The key is the desktop's:
a server's name and the configuration it came from, so the same name
from another file needs approving again. Group policy still decides
which servers are offered at all. Only people approve — an app's token
is a 403.

| Call | Does |
|---|---|
| `GET /v1/me/mcp-approvals` | `{"approvals": [{server, source, approved_at}]}` in the session's organisation |
| `POST /v1/me/mcp-approvals` `{"server", "source"}` | approve; again changes nothing; answers the list |
| `DELETE /v1/me/mcp-approvals?server=&source=` | 204, withdrawn |

## 15. Events and messages for a producer

A service reaching a producer — a person or an app — outside any call
(igni's `CLOUD.md` §6), two ways:

- **An event, for its extension.** igni hands it to the extension's
  `@ext.on_service_event(type)` handler in the producer's igni and posts
  nothing; the handler acts through explicit host calls, and the agent
  runs only if it posts. One no handler took is `not_handled`.
- **A message, for the agent.** igni posts it exactly as sent — in the
  chat its `thread` names (opened for it the first time) or the
  extension's own chat — starting a run, or waiting for the run going. No
  extension code is involved, and an approval is never answered.

Send a message when you know what the agent should read; send an event
when the extension should decide what happens.

```
POST {igni}/v1/service-events                     (service token, §3)
{"producer": {"kind": "person"|"app", "id": "..."},
 "type": "index.ready",                            // the extension's own word
 "payload": {...},                                 // at most 64 KiB as JSON
 "event_key": "index:r1",                          // optional; stored once per producer
 "expires_at": "2026-10-27T12:00:00Z"}             // optional; at most 14 days ahead
→ 202 {"event_id", "expires_at", "duplicate"}      // expires_at as kept

POST {igni}/v1/service-items                      (service token) — any kind
{"producer": {...},
 "kind": "message"|"chat"|"action"|"event",
 "thread": "claim:CL-20431",                       // optional; the service's key for a chat
 "text": "Claim CL-20431 arrived; assess it.",     // a message's; a chat's first message
 "title": "Claim CL-20431",                        // a chat's; or the chat a message's thread opens
 "start_run": true,                                // false: wait for the chat's next run
 "action": {"op": "show"|"rename"|"cancel", "view": {...}, "title": "..."},
 "attachments": ["<attachment_id>", ...],          // at most 10
 "event_key": "...", "expires_at": "..."}          // as for an event
→ 202 {"event_id", "expires_at", "duplicate"}

POST {igni}/v1/service-attachments?filename=damage.png   (service token)
Content-Type: image/png
<the bytes, at most 5 MiB>
→ 201 {"attachment_id", "filename", "content_type", "size", "sha256", "expires_at"}
```

An item says what its kind needs and nothing else (422 otherwise): a
`message` its text; a `chat` its thread and title; an `action` its
action — `show` a json-render view (`{root, elements}`, at most 64 KiB,
shown in the chat and never sent to the model), `rename` a title,
`cancel` nothing; an `event` its type, and no text, thread or
attachments, since it posts nothing.

**What a service may send beyond a plain event is what its extension
declares**, in `permissions.service`, shown to the super admin who
approves the service (§8.1, the impact's `service`) and checked here on
every request — a 403 naming what is missing:

| Grant | Lets the service |
|---|---|
| `messages` (`service.messages`) | send a `message`: text it wrote, posted as the extension's |
| `attachments` (`service.attachments`) | upload files, and name them on an item |
| `chats` (`service.chats`) | name a `thread` (each opens a chat), send a `chat` or an `action` |

A grant igni does not have is refused at upload (400). Answering an
approval is not among them, and never will be: it is a person's.

**Files.** Any type igni's composer takes, at most 5 MiB each
(`MAX_ATTACHMENT_BYTES`; 413 past it), named by a plain file name (400
for a path) and a media type (415 for anything else). They are kept in
the organisation's own database (`service_attachments`), a day while no
item names them; an item names each of its own service's unexpired,
unnamed files (400 otherwise), and from then on they expire with the
item. The producer's igni fetches each while it delivers the item, with
its own credential — the person's session on the desktop, the VM's token
(§16) in the cloud:

| Call (the producer's own token) | Does |
|---|---|
| `GET /v1/me/events/{event_id}/attachments/{attachment_id}` | the bytes, with `Content-Type` and `X-Igni-Attachment-Sha256`; only for the producer the item is for, while it is pending or delivered and unexpired; anything else is a 404 |

- **For its own extension only.** The extension is the one the calling
  service ships, recorded by igni, never taken from the request (409 if
  it has uploaded none).
- **Refused unless the producer has it on** — a person through their
  group, an app through its own list (403); a producer not in the
  token's organisation is a 404.
- **Rate-limited per service:** `SERVICE_EVENTS_PER_MINUTE` (default
  600, 0 off), a 429 with `Retry-After` past it.
- **Recorded:** the audit trail has the request, refused or not; the
  stored event names the service, the extension and the producer.
- **Stored until delivered.** The gateway, waking the producer's VM
  (`/v1/platform/wakes` lists it with reason `event`), fetches and then
  acknowledges:

| Call (platform token) | Does |
|---|---|
| `POST /v1/platform/orgs/{org_id}/producers/{kind}/{id}/events/pull` | `{"events": [{event_id, extension_id, client_id, kind, type, payload, thread, text, title, start_run, action, attachments: [{attachment_id, filename, content_type, size, sha256}], event_key, created_at, expires_at}]}`, every kind, oldest first, at most 100; `type` is a non-event's kind. An item not acknowledged within two minutes is handed out again — igni receives one at least once and tells a repeat by `event_id`. One whose extension was switched off for the producer meanwhile is dropped, not handed out |
| `POST /v1/platform/orgs/{org_id}/events/ack` `{"event_ids": [...], "not_handled": [{event_id, reason}]}` | `{"delivered": n}`; of `event_ids`, the events in `not_handled` — handed to the extension, taken by no handler — become `not_handled` with the reason, the rest `delivered`. Only an event can be not handled. Audited |
| `POST /v1/platform/orgs/{org_id}/events/fail` `{"event_ids": [...], "reason"}` | `{"failed": n}` — the gateway gave up after its own retries: each pending one becomes `failed`, with the reason, and is never handed out or woken for again. Audited |

- **On a person's desktop** igni fetches their events itself, with
  their session, while it is online — the same shapes, the same lease,
  the same expiry, and the service reads the same states:

| Call (a person's session) | Does |
|---|---|
| `POST /v1/me/events/pull` | the person's own pending events, exactly as the platform's pull answers them (and with the same two-minute lease) |
| `POST /v1/me/events/ack` `{"event_ids": [...], "not_handled": [...]}` | `{"delivered": n}`, as the platform's, counting only the person's own events; anybody else's ids are left alone. Audited |

  An app's token is a 403 (an app's events reach it through the
  gateway). The lease is one per event: whichever of the gateway and
  the desktop pulls first has the event for two minutes, and an event
  acknowledged by either is delivered — igni on the desktop and igni in
  the VM keep separate records of what they took, so an event normally
  reaches one of them, but one taken and not acknowledged within the
  lease may reach the other too. While it is pending the producer is
  still listed in `/v1/platform/wakes`.

**How long an event waits, and what its service learns.**

- An undelivered event waits at most `SERVICE_EVENT_MAX_WAIT_SECONDS`
  (default 1209600, **14 days**). Without `expires_at` it gets exactly
  that; an earlier `expires_at` is kept; a **later one is cut** to the
  maximum rather than refused — the event is still delivered for as long
  as the platform allows, and the answer's `expires_at` is the time
  kept. One already past is a 400.
- Expired, it is no longer handed out and no longer wakes anybody.
- **The service reads what became of each of its own events** — no
  webhooks; it polls:

| Call (service token) | Answers |
|---|---|
| `GET /v1/service-events/{event_id}` | `{event_id, producer, kind, type, thread, event_key, state, dropped_reason, failed_reason, not_handled_reason, created_at, expires_at, delivered_at, updated_at}`, for an item of any kind; another service's is a 404 |
| `GET /v1/service-events?state=&since=&limit=` | `{"events": [...]}`, its own only, oldest change first; `state` one of `pending`, `delivered`, `not_handled`, `expired`, `dropped`, `failed`; `since` returns those whose `updated_at` is later — poll with the last `updated_at` seen; `limit` at most 500 |

`state` is `pending`, `delivered` (a message posted — started, queued or
held for the run going — or an event a handler took), `not_handled` with
`not_handled_reason` (an event no handler took: none for its type, the
extension not running, the handler failing — nothing was posted),
`expired`, `dropped` with
`dropped_reason` (`extension_switched_off`: the producer no longer had
the extension on when it was fetched; `producer_removed`: a space its app
deleted), or `failed` with `failed_reason` (the gateway's: it could not
deliver it). An event whose `event_key` was
already used is answered with the stored one, whatever its state.

- **Retention:** an event is deleted `SERVICE_EVENT_RETENTION_DAYS`
  (default 30) after it left `pending` — delivered, expired or dropped —
  by the daily retention round (`app/services/retention.py`), which also
  marks as `expired` whatever has passed its `expires_at`. Reads mark it
  too, so a service polling sees an expiry at once. `0` keeps them.

**What happens in the producer's igni is igni's**: an event goes to its
extension's handler, which posts or not; a message starts or resumes the
agent's run, unattended, for people and apps alike (decided 2026-10-30,
replacing "an event always starts a run" of 2026-10-26). igni server
carries the item, its files and its outcome, and nothing more.


## 16. A producer's VM holds its own credential

A producer's VM runs with nobody present — a service's message starts
or resumes its agent run, an event wakes its extension (§15), and either
may need an item's files — so it needs a credential of its own. **The
platform never mints a token for a producer**: no `/v1/platform` route
returns one, and a platform token is no subject for any grant. Instead
each VM holds a **VM credential**, for audience `igni-vm`, bound to its
organisation and its producer:

```
igvm1.<org_id>.<credential_id>.<generation>.<secret>
```

Opaque to the VM; igni server keeps only SHA-256 hashes of it.

**Exchanging it** — every time the VM needs a token:

```
POST {igni}/v1/oauth/token                      (form-encoded, no client authentication)
grant_type=urn:igni:params:oauth:grant-type:vm-credential
vm_credential=<the current credential>
→ 200 {"access_token", "token_type": "Bearer", "expires_in": 900, "vm_credential": "<the next one>"}
→ 400 {"error": "invalid_grant", "error_description": ...}
```

- The token lives **15 minutes** and carries what a session does: a
  person's is `token_use=session`, `kind=person`, `org`, `org_name`,
  `group`; an app's is `token_use=app`, `kind=app`, `org`, `name`, no
  `group`. It is what the VM's igni uses everywhere a person's or an
  app's token is accepted, including the token exchange for a
  service (§3).
- **Every exchange rotates the credential.** The answer's
  `vm_credential` replaces the one presented, which no longer works. The
  VM must store the new one (on its volume) before it relies on it.
- **A lost reply may be retried once.** For **60 seconds** after a
  rotation, the credential it replaced may be presented **once** more,
  and the answer is the same `vm_credential` it was issued the first
  time, with a fresh `access_token` — an idempotent retry. Two exchanges
  of one credential at once are the same case: both get one successor.
- **Reuse revokes the family.** Anything else rotated away and presented
  again — an older one, a second retry, or a retry after 60 s — means two
  holders: the whole family, every rotation of that issue, is revoked,
  and the VM must be signed in again (a person) or re-issued (an app).
  The last 20 rotations are recognised; an older one is simply invalid.
- **How a retry is answered without keeping a credential.** A successor
  is not drawn at random: it is HMAC-SHA256 of the family, the next
  generation and the presented secret, keyed from `SECRET_ENCRYPTION_KEY`
  (the first credential of a family is random). A retry presents the old
  secret, so igni server recomputes the very successor it issued — and
  checks it against the stored hash — instead of keeping it, encrypted or
  not. Nothing usable is at rest; a successor is unpredictable without
  both the old secret and the key; and the keys in
  `SECRET_ENCRYPTION_KEYS_PREVIOUS` are tried too, so a key rotation
  inside the window does not turn a retry into reuse.
- **90 days unused** and it expires, revoked as `idle`.
- At each exchange igni server checks the producer may still act: the
  person is an active member, not deactivated and has not signed out
  since it was issued; the app is still registered. If not, the family
  is revoked.

**A person's**, when they sign in through the gateway. The gateway hands
the person's own session to their cloud igni, which exchanges it:

```
POST {igni}/v1/oauth/token
grant_type=urn:igni:params:oauth:grant-type:vm-sign-in
subject_token=<the person's igni session>
organisation=<org_id>                            // when the session is not pinned to one
→ 200 {"access_token", "expires_in": 900, "vm_credential"}
```

Only a person's session is accepted — a platform, service or app token
is `invalid_grant`. Signing in again issues a new family and revokes the
old (`replaced`). It is revoked when they **sign out** (sign out
everywhere, or an admin signs them out), are **removed** from the
organisation or **deactivated** (by an admin or by SCIM), and by the
person or an org admin on the portal.

**An app's**, when an org admin enables the cloud for it:

| Call | Who | Does |
|---|---|---|
| `POST /v1/portal/apps/{app_id}/vm-credential` | org admin | 201 `{credential_id, producer, created_at, awaiting_pickup: true, …}` — never the credential. Again re-issues: the old family stops at once |
| `POST /v1/platform/orgs/{org_id}/producers/app/{app_id}/vm-credential/pickup` | the node agent (platform token) | 200 `{"credential_id", "vm_credential"}` **once**, when it first creates the app's VM; 404 ever after. Audited. The node agent writes it to the VM's volume and keeps no copy |

Until it is picked up the credential is kept encrypted under
`SECRET_ENCRYPTION_KEY`; after, igni server holds only hashes. Removing
the app revokes it.

**On the portal** (*VM credentials*):

| Call | Who | Does |
|---|---|---|
| `GET /v1/portal/vm-credentials` | anybody | `{"credentials": [{credential_id, org_id, producer, created_at, last_used_at, idle_expires_at, awaiting_pickup, picked_up_at, generation}]}` — working ones only: the organisation's for an org admin, your own otherwise |
| `DELETE /v1/portal/vm-credentials/{credential_id}` | its person, or an org admin | 204; revoked |

`revoked_reason` (in the table, and the subject access export) is one
of `replaced`, `reused`, `idle`, `signed_out_or_removed`,
`app_removed`, `revoked_by_person`, `revoked_by_admin`.

## 17. Spaces: an app's end users, each a producer of their own

An app that serves people of its own — customers of a review bot, users
of a tutoring app — declares a **space** for each of them. A space is a
full producer, the third kind beside people and apps: a VM, a volume,
keys and a database login of its own, in the app's organisation, owned
by the app and in no group. In the VM igni runs as
`IGNI_PRODUCER=space:<space_id>`.

`space_id` is igni server's (a UUID); `external_id` is the app's own name
for its user — 1–200 characters, no `/`, unique within the app. The
external id may name a person of the app's; igni server only stores it.

### 17.1 Declaring spaces (the app's own token)

Every call takes the app's token (§11.1, `token_use=app`), and `{app_id}`
must be that token's `sub`: any other producer's token — another app's,
a person's, a space's — is a 403, and anything that is no producer's (a
platform token, none) a 401.

| Call | Does |
|---|---|
| `PUT /v1/apps/{app_id}/spaces/{external_id}` | 201 with the space when new, 200 with the one there when not: idempotent. A new space gets a VM credential waiting for the node agent (§17.3). 409 past a limit (§17.4), naming which |
| `GET /v1/apps/{app_id}/spaces/{external_id}` | the space; 404 for none |
| `GET /v1/apps/{app_id}/spaces?after=&limit=` | `{"spaces": [...], "next": <external_id> \| null}`, by `external_id`; `limit` 1–1000, default 100; pass `next` back as `after` |
| `DELETE /v1/apps/{app_id}/spaces/{external_id}` | 204; 404 for none. The space is gone: its VM credential is revoked (`space_removed`), its pending events are dropped (`producer_removed`), and the feed (§11.3) lists it `removed: true` — the gateway's cue to crypto-erase its VM state and volume. Its feed row goes `PRODUCER_FEED_RETENTION_DAYS` after the gateway was handed it. The same `external_id` may then be declared again: a new space, a new id |
| `POST /v1/apps/{app_id}/spaces/{external_id}/vm-credential` | 201, a new VM credential waiting for pickup; the old family stops at once (a VM whose credential was revoked for reuse) |

A space: `{"space_id", "app_id", "org_id", "external_id", "producer": {"kind": "space", "id": <space_id>}, "created_at"}`.

Deleting the app deletes its spaces, with the same effects.

### 17.2 Acting as a space

The app trades its own token for a space's (RFC 8693, no client
authentication — the app token is the credential):

```
POST {igni}/v1/oauth/token
grant_type=urn:ietf:params:oauth:grant-type:token-exchange
subject_token=<the app's token>
subject_token_type=urn:ietf:params:oauth:token-type:access_token
space=<space_id>
→ 200 {"access_token", "token_type": "Bearer", "expires_in": 600,
       "issued_token_type": "urn:ietf:params:oauth:token-type:access_token"}
→ 400 invalid_grant: not an app token, or a space of another app (or none)
```

The token: `token_use` = `space`, `sub` = the space id, `aud` = igni's
issuer, `org`, `kind` = `space`, `app` = the app id, `external_id`; never
a `group`. It is accepted everywhere an app's token is for a producer:
`GET /v1/extensions` (the app's list, `producer: {"kind": "space", …}`),
commands in its own VM (§13), and the exchange for a service's token
(§9.2: `token_use` `space`, `aud` the service's URL). Only people
approve MCP servers (§14) and fetch events with `/v1/me/events` (§15): a
space's token is a 403 there.

### 17.3 The space's VM

- **Its credential** works as an app's (§16): issued when the space is
  declared, kept encrypted until the node agent picks it up — once — with
  `POST /v1/platform/orgs/{org_id}/producers/space/{space_id}/vm-credential/pickup`;
  exchanged in the VM with the `vm-credential` grant for a 15-minute
  space token. It is revoked when the space or its app is deleted.
- **What it inherits from its app:** the VM profile (the app's, or the
  default; its row on the producers feed carries it, §11.3), the extensions the app has switched on, and its
  settings. Per-space overrides are not offered: a space is one more VM
  of its app, configured with it.
- **Events** may be addressed to it (`"producer": {"kind": "space", "id"}`
  in `POST /v1/service-events`, §15), refused unless its app has the
  extension on; it is listed in `/v1/platform/wakes` and pulled with
  `.../producers/space/{space_id}/events/pull` like any producer.
- **Commands** (§13): the space's token runs in its own VM; org admins
  and the super admin in any space's. A job for a profile or an
  organisation reaches its spaces.
- **The feed** (§11.3) lists it with kind `space`: `active` while it is
  declared and its app is registered; the versions are its app's
  profile's.

### 17.4 Limits

| Call | Who | Does |
|---|---|---|
| `GET /v1/portal/space-caps` | anybody signed in | `{"max_spaces_per_app", "max_spaces_per_org", "updated_at"}`; `null` is no cap, which is the default |
| `PUT /v1/portal/space-caps` `{"max_spaces_per_app", "max_spaces_per_org"}` | super admin | set them |
| `GET /v1/portal/orgs/{org_id}/space-quota` | its org admins, super admin | `{"org_id", "max_spaces_per_app", "max_spaces", "caps": {…}, "spaces": <how many now>}` |
| `PUT /v1/portal/orgs/{org_id}/space-quota` `{"max_spaces_per_app", "max_spaces"}` | the same | the organisation's own limits, each at most the platform's cap (400 otherwise); `null` leaves only the cap |

Declaring a space past the smaller of the two limits per app, or of the
two per organisation, is a 409. Lowering a limit removes no space.

## 18. Retention: how long a removed member's VM is kept

The organisation is the controller of its members' work in the cloud.
When a person is removed from it, their producer there is **removed at
once** — suspended, never usable again (§11.3) — and its VM's state,
volume and keys are **erased at `erase_at`**: the removal plus the
organisation's retention. A defined rule, enforced by the gateway from
the feed, and every step of it audited.

### 18.1 The rule

| What | Erased |
|---|---|
| a person removed from an organisation | `removed_member_retention_days` after the removal (default **30**; `0` at once), unless a legal hold stands |
| a person erased on request (`POST /v1/portal/erasure`) | at once, overriding the retention — **refused while a legal hold stands** (409, saying where; the refusal is audited) |
| a person's account deleted; a space or an app deleted or **discarded**; an organisation deleted | at once |
| a removed member's VM **discarded** by an org admin (§18.5) | at once, overriding the retention — **refused while a legal hold stands** (409, naming it) |

| Call | Who | Does |
|---|---|---|
| `GET /v1/portal/retention-caps` | anybody signed in | `{"max_removed_member_retention_days", "updated_at"}` — default 365 |
| `PUT /v1/portal/retention-caps` `{"max_removed_member_retention_days"}` | super admin | the platform's maximum |
| `GET /v1/portal/orgs/{org_id}/retention` | its org admins, super admin | `{"org_id", "removed_member_retention_days", "max_removed_member_retention_days", "updated_at"}` |
| `PUT` the same `{"removed_member_retention_days"}` | the same | 0 up to the maximum (400 past it). For removals from now on: an erasure already scheduled keeps its time. A lowered maximum caps the organisation's value from then on |

**Audited:** each erasure the feed schedules, moves or stops is a row in
the organisation's audit trail — method `SYSTEM`, route
`producer-feed/erasure`, target `person_vm` / `app_vm` / `space_vm` and
the producer's id, detail `{"incarnation", "erase_at", "why"}` with
`why` one of `removed`, `held`, `released`, `erased_on_request`,
`discarded`; and when the gateway reports the erasure **done**, one more
with `why: "erased"`, the reporting client and the node's part-by-part
`summary` (§18.3). The calls above and below are portal requests, in the
trail as such.

### 18.2 Legal holds

| Call | Who | Does |
|---|---|---|
| `GET /v1/portal/orgs/{org_id}/legal-holds` | its org admins, super admin | `{"holds": [{org_id, user_id, reason, placed_by_user_id, placed_at, incarnations: [{incarnation, removed_at, erase_at}]}]}` |
| `PUT /v1/portal/orgs/{org_id}/legal-holds/{user_id}` `{"reason"}` | the same | 200, the hold. Every removed incarnation of the person here not yet erased — and any removed while it stands — gets `erase_at: null` |
| `DELETE` the same | the same | 204 (404 for none). Each held incarnation is rescheduled to its removal plus the retention it was removed under, or now if that has passed |

An incarnation whose `erase_at` has passed is erased; a hold cannot
bring it back, and is not shown in `incarnations`.

**A hold on an app** keeps the app's VM and its spaces' from being
erased — after inactivity (§12.2) or once discarded (§18.5). Discarding a
held app still removes it at once and frees its place under the licence;
only the erasure waits (`erase_at: null`) until the hold is released, and
then happens at once. A hold must be placed before the discard: an app's
erasure is due as soon as it is discarded.

| Call | Who | Does |
|---|---|---|
| `PUT /v1/portal/orgs/{org_id}/legal-holds/apps/{app_id}` `{"reason"}` | its org admins, super admin | 200 `{org_id, app_id, reason, placed_by_user_id, placed_at}`; 404 for an app neither here nor discarded and waiting |
| `DELETE` the same | the same | 204 (404 for none) |

`GET .../legal-holds` lists them as `"apps": [...]` beside `"holds"`.

### 18.3 What the gateway does

Suspend at removal; at `erase_at`, erase the VM's state, volume and keys
and run, against the organisation's igni database,
`igni-cloud db erase-producer --org <org_id> --producer <kind>:<id> --incarnation <incarnation>`.
A row listed again with `erase_at: null` (held) or a later one
(released) replaces the schedule it had. When an erasure is **done** the
gateway reports it:

```
POST /v1/platform/orgs/{org_id}/producers/{kind}/{id}/erasures {"incarnation", "summary": {…, ≤ 8 KiB}}
→ 200 the erasure (§18.5), "state": "erased"      → 404 no such removed incarnation
```

Idempotent: the first report is audited (`why: "erased"`), a repeat is
answered the same and not audited again.

### 18.4 Exporting a removed member's data during retention

While a removed incarnation waits for its erasure (or is held), the
organisation may take a copy of what igni holds of it — the
conversations and the rest of the producer's data in the organisation's
igni database:

| Call | Who | Does |
|---|---|---|
| `POST /v1/portal/orgs/{org_id}/producers/person/{user_id}/exports` `{"incarnation"?}` | its org admins, super admin | 202, the export, `requested`; the latest removed incarnation when none is named. 404 when the person has none waiting for erasure; 409 for an incarnation not waiting (erased, or not removed). Audited |
| `GET /v1/portal/orgs/{org_id}/exports`, `.../exports/{export_id}` | the same | `{"exports": [...]}`, one |
| `GET /v1/portal/orgs/{org_id}/exports/{export_id}/download` | the same | the archive, `application/octet-stream` (409 until `ready`). Audited |

An export: `{export_id, org_id, producer, incarnation, state:
requested|handed_out|ready|failed, requested_by_user_id, requested_at,
handed_out_at, finished_at, size, error, expires_at}`. A ready archive
is kept `PRODUCER_EXPORT_RETENTION_DAYS` (default 7) and then deleted by
the retention round, whatever became of the incarnation: it is the
organisation's copy. A failed export goes a day after it failed.

**The node agent runs it** (platform token, audited):

1. `POST /v1/platform/exports/pull` → `{"exports": [{export_id, org_id,
   producer, incarnation, command}]}`, `producer` an object
   `{"kind": "person", "id": "<user_id>"}` as everywhere in this
   document (the `kind:id` string is only inside `command`), with `command` =
   `["igni-cloud", "db", "export-producer", "--org", "<org_id>",
   "--producer", "person:<id>", "--incarnation", "<incarnation>"]`, run
   against the organisation's igni database with `--out <file>` appended
   — the node agent chooses the file, and uploads it. Each is handed to the caller for 30 minutes, then
   handed out again. One whose incarnation was erased meanwhile is
   marked `failed` instead of handed out.
2. `PUT /v1/platform/orgs/{org_id}/exports/{export_id}/archive` with the
   archive as the body (at most `PRODUCER_EXPORT_MAX_BYTES`, default
   2 GiB, else 413) → the export, `ready`; 409 unless handed out and
   waiting.
3. Or `POST /v1/platform/orgs/{org_id}/exports/{export_id}/failed`
   `{"error"}` → 204.

**Not yet: the workspace volume.** Proposed as a second kind of export
on the same calls — `{"incarnation", "parts": ["database", "volume"]}` —
which the node agent fulfils by mounting the suspended volume read-only
and archiving it, uploaded the same way (a size large enough to need
resumable, ranged uploads, which this does not have yet).

### 18.5 Discarding: an app at once, a removed member's VM now

An org admin discards what is no longer wanted; the erasure runs in the
background, by the same path as any removed producer's (§18.3).

**An app**, while it is still in use:

| Call | Who | Does |
|---|---|---|
| `POST /v1/portal/apps/{app_id}/discard` `{"start_fresh"?: bool}` | an org admin | 200 `{"discarded": <erasure> \| null, "fresh_app": <app> \| null}` |
| `DELETE /v1/portal/apps/{app_id}` | the same | 204: the same, without a fresh app |

The app is removed **at once**: its tokens and VM credential stop, its
spaces go with it, and the producers feed lists it `removed` with
`erase_at` now. The gateway closes its connections, never wakes it again
and frees its place under the licence straight away (Fleet's apps,
spaces included, are capped at the licence's band), and a node erases
the VM's memory, snapshots, volume and keys — and its rows in the
organisation's igni database — in the background. A legal hold on the
app (§18.2) keeps the data, not the app. `start_fresh` registers a new
app at once with the old one's name, public key, owner (the admin, if
the owner has left), VM profile, extensions and — if it had the cloud
enabled — a new VM credential for the node agent: a fresh VM for the same
app, which now signs in under its new client id. Audited as the portal
request, with `{"why": "discarded", "erasure", "fresh_app"}`.

**A removed member's VM**, before the retention runs out:

| Call | Who | Does |
|---|---|---|
| `POST /v1/portal/orgs/{org_id}/members/{user_id}/discard` `{"incarnation"?}` | its org admins, super admin | 200 `{"erasures": [<erasure>]}`: every removed incarnation of theirs here not yet erased (or the one named) is due **now**. 409 while a legal hold stands (naming it), and for a current member — **a member's VM is never discarded**, active or deactivated; 404 when nothing is waiting |

Audited as the portal request and, per incarnation, `why: "discarded"`
in the erasure trail. An export of that incarnation (§18.4) still
requested or ready is not ended by the discard, but none can be taken
once the erasure is done; the portal warns.

**Where each erasure stands** — `GET /v1/portal/orgs/{org_id}/erasures`
(its org admins, super admin) → `{"org_id", "erasures": [...]}`, every
removed or discarded producer the feed still keeps (§11.3), newest first:

```
{"producer": {"kind", "id"}, "name", "incarnation", "removed_at", "erase_at",
 "state": "scheduled" | "held" | "erasing" | "erased", "erased_at", "legal_hold",
 "exports", "discarded_by_user_id", "discarded_at"}
```

`name` is the person's email, or the app's name when it was discarded.
`erasing` means due and not yet reported done by the gateway; `erased`
that it was (§18.3).

## 19. Model calls from a producer's VM, and whose they are

`POST /v1/chat/completions` takes a person's session, an app's token
(§11.1) or a space's (§17.2). Each call's usage is recorded in the
organisation's own database (`chat_usage`), as it is for a person:

| Caller | `user_id` recorded | `app_id` | Organisation |
|---|---|---|---|
| a person | their user id | null | the session's (or the `X-Org-Id` one they are an active member of) |
| an app | `app:<app_id>` | the app | the app's |
| a space | `space:<space_id>` | the space's app | the app's |

The organisation's usage on the portal (team scope) counts all of them;
an app's whole use is its own rows plus its spaces', by `app_id`. There
is no token quota enforced on model calls today — for people or
producers — so nothing is refused for usage; the awake-time quota (§12)
is stored, not enforced. An app's or a space's model choice is the
request's, or the node's default: groups (and their pinned model) are
people's.

### 19.1 When every node is full: a fair queue

A request that finds a free slot on a node takes it at once, unless
others are already waiting. When every node is at its in-flight cap the
request **waits** instead of being refused, in a queue that takes
**turns between organisations**, and within one **between producers**
(a person, an app, each space), oldest request first for each: an
organisation with a hundred busy producers gets one turn per round, like
one with a single request, and no producer crowds out its organisation's
others. A request keeps its node affinity (the node holding its KV
cache) when that node has room; the per-node cap is unchanged.

- **Bounds:** a request waits at most `CHAT_QUEUE_MAX_WAIT_SECONDS`
  (default 900 — an agent run waits its turn), and at most
  `CHAT_QUEUE_MAX_LENGTH` (default 2000) wait in one API replica; past
  either, 429 with `Retry-After: 30`.
- **A client that disconnects** leaves the queue; a slot granted as it
  left goes back. A streaming response starts when the request is
  dispatched.
- **Metrics:** `igni_chat_queue_depth{org}` and
  `igni_chat_queue_wait_seconds{org}`.
- **Across replicas:** each API replica queues its own requests; the
  nodes' slots are counted in Redis, so replicas take slots as they come
  free (a release wakes its own replica at once, others within a quarter
  second), each serving its waiters in turn. Fairness is exact within a
  replica and approximate across them.

## 20. Adding somebody back, and sessions of an earlier membership

`POST /v1/portal/team/invite` `{"email", "role"}` for an address that
already has an account adds it at once — a new membership, `status:
"active"` — rather than leaving an invitation, as signing up with that
address would; the address's domain is the organisation's, which is what
decides an account's organisation. 409 when it is already a member here
(any status), when the account is deactivated, or when it is a member of
another organisation. A person removed and added back so is a **new
incarnation** (§11.3): a fresh VM.

A session pinned to an organisation is good only for the membership it
was signed in under: once a person is removed, their sessions for that
organisation stop (403 while they are no member; 401 "an earlier
membership" once they are added back), at the portal, at
`/v1/chat/completions`, and for the token exchanges and VM sign-in. They
sign in again. Their sessions for no organisation, or another, are
untouched.

A person is a member of **one organisation at a time**: the one owning
their address's domain (a domain is owned by one organisation). Signing
up joins that one; the invite above refuses an account that is a member
elsewhere; an account in an organisation cannot move to an address on
another; an organisation's creator joins it only when their own address
is on its domains and they are in no organisation yet. The database
guarantees it: a unique index on `org_memberships(user_id)` over current
memberships (active or deactivated — removal deletes the row), so no
path can put a person in two.

## 21. Revocation: nothing works from the moment of removal

When a person is removed from an organisation (or deactivated, or their
account deleted or erased, or their sessions withdrawn), an app or a
space deleted, or an organisation deleted, nothing they hold works from
that moment:

- **At igni server**, on the very next request: every session, VM
  token, refresh and exchange is checked against the membership (§20),
  the account (deactivation, withdrawn sessions) and the app or space
  (it must still be registered or declared). There is no cache to wait
  out.
- **For the gateway and services**, which check tokens by their
  signature, through **revocations**, long-polled:

```
GET /v1/platform/revocations?since=<cursor>&limit=500&wait=25     (platform token: every one)
GET /v1/revocations?since=<cursor>&limit=500&wait=25              (a service's own token, §3: its organisation's)
GET /v1/revocations?scope=served&since=<cursor>&wait=25           (any of its tokens: every organisation it serves)
→ 200 {"revocations": [{"kind": "person"|"app"|"space", "org_id" | null, "subject_id",
                        "incarnation" | null, "not_before_ms", "reason"}],
       "cursor"}
```

- **Rule:** refuse a token whose subject is `subject_id` (a person's
  `sub`; an app's or a space's `sub`), whose `org` is `org_id` — or any
  organisation, when `org_id` is null — and whose **`iat_ms` is at or
  before `not_before_ms`** (Unix milliseconds). Every igni token carries
  `iat_ms` beside `iat`, so a token issued just after a removal in the
  same second stands. A person removed and added back signs in again:
  their new tokens are after `not_before_ms`.
- **Which organisations a service serves:** `GET /v1/organisations/served`
  (any of its service tokens, or — at start-up, before it knows an
  organisation to name — one with `scope=revocations` and no
  organisation, §3) → `{"organisations": [{"org_id", "name"}]}`
  — every organisation with its extension on for a group or an app — so
  its SDK follows them from the start. `scope=served` gives all of their
  revocations (and those for every organisation) on one cursor and one
  long poll.
- **`reason`:** `removed`, `inactive` (deactivated; an app's cloud
  disabled), `erased`, `sessions_withdrawn` (every organisation). A
  person's `incarnation` is the membership (§11.3) revoked.
- **Cursor and wait:** as the feed's — numbered under the same lock, so
  a cursor misses none; `wait` (0–30 s) answers as soon as there is one.
  The service variant also answers `If-None-Match: "<cursor>"` with 304
  when nothing is new. A revocation is kept 31 days, past the longest a
  token lives (30 days), then deleted by the retention round.
- **Tokens carry** `iat`, `iat_ms` and `sub` (all), `org` and, from a
  VM credential, `inc` — the person's membership — so a gateway can also
  match a VM's token to the feed's incarnation. There is no per-session
  id: a revocation is a watermark per subject (`iat_ms <=
  not_before_ms`), which covers every session a person holds without a
  list of them.

**What the gateway does:** long-poll both the feed (`wait=25`) and the
revocations; close a removed or inactive producer's connections at
once; refuse any token a revocation covers.

**What a service SDK does:** obtain a `scope=revocations` token
(`client_credentials`, no `organisation`), list `/v1/organisations/served`,
long-poll `/v1/revocations?scope=served` (`wait=25`) with it, keep the watermarks by `(org, subject)` — and by subject for
`org_id: null` — and refuse a token whose `iat_ms` is at or before its
watermark, in addition to §4's checks.

## 22. Personal API keys: a person's access, handed to a program

A person who wants a program to act as them — a script, a CI job,
`igni-client` driving their igni in Fleet — makes an **API key** on the
portal and gives it to the program. There is no headless sign-in for a
person otherwise: every other session starts in a browser.

```
igpak1.<org_id>.<key_id>.<secret>
```

Opaque to the program. The secret is 256 bits (`secrets.token_urlsafe(32)`);
igni server keeps only its SHA-256 and compares in constant time. The key
is shown once, in the answer that makes it, and never again.

**Exchanging it**

```
POST {igni}/v1/oauth/token
grant_type=urn:igni:params:oauth:grant-type:api-key
api_key=igpak1.…
→ 200 {"access_token", "token_type": "Bearer", "expires_in": 900}
→ 400 {"error": "invalid_grant", "error_description": "this API key has expired"}
```

- **What it buys:** a **15-minute session** as the person (`token_use`
  `session`, `kind` `person`), pinned to the key's organisation with `org`,
  `org_name`, `group` and `inc` — the same claims as a VM's token (§16), so
  every route that takes a person's session and Fleet's gateway (§11) take
  it — plus `api_key: <key_id>`.
- **Not renewable:** a program exchanges the key again when the session
  runs out. Keys do not rotate.
- **Expiry:** chosen when the key is made — 30, 90 or 365 days. There are no
  keys that never expire.
- **The person may still act:** at every exchange the key's making stands in
  for a sign-in (`PersonInOrganisation.check`, with the key's `created_at`
  as `iat`). Signing out everywhere, removal, deactivation, or leaving and
  rejoining after the key was made refuses it, and revokes it.
- **Revoked eagerly** too, wherever igni server signs a person out, removes
  or deactivates them. Revoking stops new sessions at once; one already
  bought lives out its 15 minutes, at the gateway as anywhere.
- **Recorded:** every exchange, granted or refused, is an audit row, with
  the person as actor and `api_key:<key_id>` as target.

**What its session cannot do.** A key is the credential most likely to
leak, so what it buys turns into nothing longer-lived. A session carrying
`api_key` is refused (403, or `invalid_grant` at the token endpoint) when
it tries to:

| Call | Why |
|---|---|
| `GET` / `POST` / `DELETE /v1/portal/api-keys…` | a key does not make, list or revoke keys |
| `POST /v1/cli/token` | a 30-day session |
| `vm-sign-in` (§16) | a VM credential lives 90 days |
| `POST /v1/portal/oauth/authorize` | a service's refresh token lives 30 days |

Everything else the person may do, the key's session may — including what
an org admin or a super admin may, when its person is one. That includes
registering an app and rotating its keys, so a program can set an
organisation up end to end; both are audited, and an admin can discard an
app.

**On the portal** (*API keys*)

| Call | Who | Does |
|---|---|---|
| `POST /v1/portal/api-keys` `{name, expires_in_days}` | anybody in an organisation | 201 `{key_id, name, user_id, created_at, expires_at, last_used_at, expired, api_key}` — the only time `api_key` is shown |
| `GET /v1/portal/api-keys` | anybody | `{"keys": [...]}`, never the key — keys not revoked, expired ones included: the organisation's for an org admin, your own otherwise |
| `DELETE /v1/portal/api-keys/{key_id}` | its person, or an org admin | 204; revoked. Anybody else: 404 |

`revoked_reason` is `revoked_by_person`, `revoked_by_admin` or
`signed_out_or_removed`.

