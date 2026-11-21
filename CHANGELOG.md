# Changelog

Notable changes to igni server, newest first. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html); while the major
version is 0, a minor version may change an interface, and its entry says so.

The version is the one in `pyproject.toml`, which `/version`, the Helm
chart's `appVersion` and the release image tags all read
(`tests/test_one_version_number.py`). [docs/RELEASING.md](docs/RELEASING.md)
is how a release is cut.

Security fixes are listed under **Security** with their advisory or CVE
identifier. [SECURITY.md](SECURITY.md) is how to report one.

## [Unreleased]

The server side of the desktop app's Local / Cloud switch and of a
group's mode (igni-core `docs/design/local-cloud-switch.md`). It changes
an interface: a person's service events now go to one side, and to the
desktop until the person first switches to the cloud (see **Changed**).

### Added

- **A group says where igni runs for its people.** `org_groups.mode` is
  `local` (only on their computers), `cloud` (only in the cloud) or
  `local_and_cloud` (both, switched in the desktop app) — the last for
  every existing group, which is what they did. It is read and set with
  the group (`PATCH /v1/portal/groups/{id}`, admin only, on the Default
  group too, which takes nothing else), audited with
  `{"mode": {"from", "to"}}`, copied with a group, and carried in the
  signed group pack as `mode`, after `theme`. The portal's group
  settings have a card for it, "Where igni runs for this group", which
  warns before a change that disconnects people from the cloud.
- **`GET /v1/me/cloud`** tells the desktop whether a person has a cloud
  igni (`cloud`, and `reason` when not: `fleet_not_configured`,
  `not_licensed`, `inactive`, `group_local_only`), the gateway's address,
  their group's mode and who pulls their events — igni-sdk's
  `cloud-discovery.schema.json`. The person's own session only; `ETag`
  and `Cache-Control: private, max-age=60`.
- **`PUT /v1/me/cloud/events-owner`** `{"owner": "local"|"cloud"}`
  chooses which side pulls the person's events where their group runs
  igni in both; `409 {"error": "mode_forbids"}` otherwise. Audited.
- **The producer feed says why a producer is inactive.** A person whose
  group runs igni only on their computers is listed `active: false` with
  `inactive_reason: "group_local_only"` — not removed, so their VM is
  kept — and the group trigger fires on `mode`, so the gateway hears at
  once. Their VM can neither sign in nor renew its credential
  (`invalid_grant`, `"reason": "group_local_only"`); the family is not
  revoked.
- **`chat_usage.reasoning_tokens`** (tenant migration `t0034`): how
  much of the output was thinking, where the provider reports it, null
  otherwise. The portal's usage response carries it per day
  (`reasoning_tokens`) and in total (`total_reasoning_tokens`), null
  when no request in the window reported it.

### Changed

- **A person's events go to one side.** The desktop's pull and the
  gateway's pull no longer race for one lease: the owner is the
  desktop in a `local` group, the cloud in a `cloud` group, and the
  person's choice in a `local_and_cloud` one — the desktop until they
  first switch. While the cloud owns them, `POST /v1/me/events/pull` is
  `409 {"error": "events_owned_elsewhere", "owner": "cloud"}`; while the
  desktop does, the gateway's pull for the person answers no events and
  `/v1/platform/wakes` does not list them for those events. Acknowledging
  and fetching attachments are unchanged. A person who uses Fleet only
  from a program sets the owner to `cloud` once.

### Security

- **The bundle carries Fleet 0.1.1.** `containers/release/artefacts.tsv`
  pins `igni-fleet` 0.1.1 in place of 0.1.0. Fleet 0.1.0's guest base
  trusts a throwaway profile-layer key, so no real profile layer verifies
  against it, and has dnspython 2.9.1 (CVE-2026-128089); 0.1.1's base
  trusts the platform's key and has dnspython 2.9.2.

### Fixed

- **A usage row's output counts the model's thinking, as every provider
  bills it.** Vertex AI's Gemini, on its OpenAI-compatible endpoint,
  reports thinking outside `completion_tokens` (while `total_tokens`
  counts it), so `chat_usage.output_tokens` and the portal's output
  figure under-counted what Google bills as output — tenfold and more
  on a short answer. The thinking the provider reports and the total
  shows outside the completion is now added to the output; OpenAI,
  Azure OpenAI, vLLM and Anthropic already count it there and are
  unchanged. The response itself still carries the provider's own
  `completion_tokens`.
- **Usage rows Gemini on Vertex served before the fix are corrected**
  (tenant migration `t0035`). A `vertex_openai` row whose total is above
  input plus output gets that difference as `reasoning_tokens` and in
  `output_tokens`; the value it held is kept in the new
  `chat_usage.output_tokens_reported`, and the downgrade puts it back.
  No other row is touched, and running it again changes nothing. The
  portal's output figures for past days rise accordingly.
- **The non-streamed answer keeps `usage.completion_tokens_details`,**
  as the streamed one always did; it was dropped on the way back.
- **The security pack says what has shipped.** `docs/SECURITY_PACK.md`
  still said the release keys did not exist and that no release or bundle
  had been made; 0.7.0 was signed and its bundle kept on 2026-11-12. It
  now says so, lists Fleet 0.1.0's throwaway profile-layer key as a gap,
  and, with `docs/OFFLINE_UPDATES.md`, says that the module repositories'
  `release` environments sign their own images too, not only the private
  release repository's.

## [0.7.1] - 2026-11-16

The first release after 0.7.0, and the one the end-to-end campaign's
findings were fixed in. It changes no interface: every entry below is a
fix, an addition that keeps what it was, or a correction to what the
documentation said.

### Added

- **The chart can mount files into the API pod.** `extraVolumes` and
  `extraVolumeMounts` add volumes and mounts to the API pod, so
  `INFERENCE_BACKENDS_PATH` can name a file — the case
  `app/config.py` calls the normal one — and not only the inline
  `INFERENCE_BACKENDS`. `values.yaml` carries the backends ConfigMap as
  the example. [charts/igni](charts/igni).
- **A second igni can run on the same host.** The production compose
  file's container names follow `IGNI_STACK` and its host ports
  `IGNI_API_PORT` / `IGNI_PORTAL_PORT`, as the local file's do. The
  defaults are unchanged, so an install that set them by hand still
  works.
- **Organisation databases have a downgrade.** `python -m
  app.db.sql.tenant_migrations --downgrade <revision>` (and `make
  downgrade-tenants REV=...`) takes the tenant template and every active
  organisation back, so a rollback to the previous image does not stop at
  "Can't locate revision" in migrate. The README and the chart README
  describe both steps.
- **The portal shows and keeps a VM profile's volume.** The profile form
  had no volume fields, and `PUT` replaces the whole profile, so any
  portal edit reset a volume set through the API to the standard 10/50
  GiB and sent that out on the producer feed. The form now shows and
  sends `volume_gib` and `volume_max_gib`; a new profile left empty
  takes the server's standard.

### Changed

- **A group's pinned model is one chat completions actually serves.** A
  group's model is for a chat request that names none, and only
  `/v1/chat/completions` accepts that. Pinning a model that only
  `/v1/messages` serves was accepted and offered, never applied there,
  and turned every model-less chat completion from the group into a 404.
  Saving refuses such a pin and says why, the portal offers only the
  chat-completions models, and a pin whose model has since left that
  endpoint costs the group its pin, not the request.
- **The scheduled vulnerability scan scans the newest release's
  images.** The scheduled scan is the only rescan a shipped image gets,
  and it rebuilt the default branch: when `main` moved dnspython to
  2.9.2 the scan went green while the released 0.7.0 images still
  carried 2.9.1. The job now checks out the newest `vX.Y.Z` tag and
  builds that.

### Removed

- **The cloud marketplace distribution path.** igni is distributed as
  container images published to a registry and the Helm chart in
  `charts/igni/`; there is no cloud marketplace listing. The listing's
  artefacts go with it: the `marketplace/` directory — the deployer's
  Dockerfile, its README and the listing schema — the
  `tools/marketplace/verify.sh` script that built the deployer image and
  checked the listing against the chart, and the chart's `marketplace`
  values with the `app.k8s.io` Application object the deployer
  installed. The images keep their non-root user, their provenance and
  their licence, and the chart still refuses to render without the
  images, the datastores, an encryption key and the server's public URL.
  igni's own plugin marketplace is a different thing and is untouched.

### Fixed

- **The portal image is tagged with the release version.** The release
  workflow's portal build read `steps.meta.outputs.version`, but that job's
  `meta` step never wrote it — the output was an empty string, and the
  build stopped with `invalid tag "ghcr.io/ignite-ember/igni-portal:"`
  before anything was pushed. The backend's `meta` step has always written
  it; the portal's does not, and the 0.7.0 release run hit exactly that.
  The version is written now, and both builds refuse an empty one rather
  than push a tag that names nothing.
- **A drained fleet answers 503, and a fleet with no nodes answers an
  empty model list.** With every node drained, chat admission let
  `AllBackendsDrainedError` out as a bare 500 on `/v1/chat/completions`
  and `/v1/messages`; it is now a 503 in the pool's own words ("Resume
  one from the portal"). With no node at all — none configured, or the
  last registered one stopped or expired — `GET /v1/chat/models` answered
  500; it is now an empty list, not cached, so client model discovery
  survives the GPUs being off.
- **A timed-out non-streamed completion is not asked again.** A
  non-streamed request that timed out was retried up to three times, on
  the same node when it was the only one: with the timeout at 4 s the
  caller waited 12.8 s, and the busy node received the work three times,
  past its concurrency limit. A read timeout now ends a non-streamed chat
  completion or message; refusals, 5xx, unreadable answers and connect
  timeouts are still tried on another node.
- **A sign-in right after "sign out everywhere" is not refused by its own
  token.** For up to two seconds after a withdrawal the watermark (rounded
  up to the next second) was at or after any new token's whole-second
  `iat`, so a sign-in answered 200 with a token refused on its first
  request, for ever. Password, code, social and CLI sign-in now wait until
  a token minted then postdates the watermark — at most about two
  seconds, only right after a withdrawal, never to wait out clock skew.
- **The break-glass key fails closed with a diagnosis on a hash that is
  not bcrypt.** `require_deployment_admin` passed the hash straight to
  `bcrypt.checkpw`, so a value that is not a bcrypt hash answered every
  break-glass call with a 500 (`ValueError: Invalid salt`). The usual
  cause is a compose env file interpolating the hash's `$`. It now
  answers 503 saying the value is not a bcrypt hash and to write `$` as
  `$$` in a compose env file.
- **The retention loop runs on every install.** The runner did not start
  when all three retention windows were 0, so exports of removed members'
  data and 31-day revocations — which `DATA_RETENTION.md` says are always
  cleared — stayed for ever. The loop now always starts, and every
  operator-facing text (the chart README, the chart's NOTES and values,
  the compliance tracker and the alert) says what 0 keeps.
- **A copy of the Default group carries no read-only note.** The portal's
  copy dialog asks only for a name, so a copy kept the Default group's
  "It cannot be changed here" line and showed it next to the Edit button
  of a group that can be changed. A copy now starts with no description
  unless the request gives one.
- **The Default group's description does not invite edits it refuses.**
  It told admins to "Edit or delete any of it here", and every edit of
  the Default group answers 409. It now says to create a group from it,
  and tenant migration `t0032` replaces igni's old line in existing
  organisations.
- **The Default group's seed reaches the API image.** `.dockerignore`'s
  `**/*.md` dropped the agent and skill seeds under `app/services/portal`,
  so every organisation created on a released image got a Default group of
  4 entries instead of 20, while migrated ones got all of them. Both seed
  directories are negated now.
- **The chart's Ingress is admitted by ingress-nginx.**
  ingress-nginx 1.12 turns strict path-type validation on by default, and
  its pattern for `Prefix` and `Exact` paths has no dot, so it refused
  the chart's Ingress at admission and the README's own install failed.
  `/.well-known` is `ImplementationSpecific`, which it admits.
- **The exit check's archive restores onto a plain PostgreSQL.** The
  runbook's restore targets a PostgreSQL 17 with no igni software, so it
  has no `igni` role, and the command stopped at the first `ALTER ...
  OWNER TO igni`, restoring nothing. It now passes `--no-owner
  --no-privileges` and says why.
- **A removed member's export is served as the zip it is.** The node
  agent uploads what `igni-cloud db export-producer` wrote, unchanged, and
  that is a zip; the download named it `export-<id>.tar.gz` as
  `application/octet-stream`, so `tar -xzf` failed on it. It is now
  `application/zip`, named `export-<id>.zip`, and the docs say so.
- **The directory says a removed member is not a former one.** Removal
  deletes the membership, so the directory answered for a removed person
  as for somebody who never belonged. §7 now says *former* means
  deactivated, and what a service does about a removed author.
- **The Services pages say a platform client ships no extension.** They
  told super admins that Fleet's gateway and node agents had not uploaded
  theirs yet, but a platform client may not upload one.
- **A platform client is badged Platform, not App.** The badge said
  Service for a service and App for anything else, so Fleet's gateway and
  node agents were shown as apps.
- **The producers feed's documented profile carries `internet`.** §11.3's
  feed block left it out, although the feed serves it and §12 says so; a
  gateway written from §11.3 would have dropped the one field that is a
  security grant.
- **uvicorn's lines and Python warnings are JSON under
  `LOG_FORMAT=json`.** Only records reaching the root logger were JSON:
  uvicorn.error and uvicorn.access keep uvicorn's own plain-text
  handlers with propagate off, so the startup lines and every access line
  stayed text in the same stream, and warnings went to stderr raw.
- **An outgoing call's span names the host it went to.**
  `docs/observability.md` promises an outgoing-call span carries method,
  host, status and duration, but the httpx instrumentation named its
  target only in `http.url`, which the scrubber drops. The scrubber adds
  `server.address` and `server.port` now.
- **The published API schema matches the routes.**
  `/portal/inference/models` gained `chat_completions` with the
  group-pin fix; `docs/openapi.json` is regenerated.
- **The welcome email names no public install command.** igni runs on
  isolated networks, where `brew install` and `pip install` fail, and how
  the app reaches desktops is the operator's call. The email now links
  this deployment's own portal, and a new optional `CLIENT_INSTALL_URL`
  (Helm: `urls.clientInstall`) names the operator's own page for getting
  the app.
- **Mail names no sender unless the operator sets one.**
  `EMAIL_FROM_NAME` now defaults to empty, and empty means the mail names
  no sender: the From header is the bare address, subjects carry no brand
  suffix, and the header subtitle and footer line are left out. A set
  name is used exactly as before. The welcome mail's copy — a public
  beta, a free Pro plan, a vendor model, a Homebrew formula that no
  longer exists and "our support team" — is gone with it.

### Security

- **dnspython 2.9.2 (CVE-2026-128089) is in the image.** The 0.7.0
  images carry 2.9.1; this release carries the fixed version, and the
  scheduled scan now rescans the newest release's images rather than the
  default branch's, so a shipped image's CVEs are found rather than
  assumed fixed.

## [0.7.0] - 2026-11-10

The first release of igni server that has ever shipped. It changes an
interface: the inference pools are gone, so the API answers one flat list
of nodes where it used to answer a list of pools. While the major version
is 0, a minor version may carry that — this one does, and says so.

### Added

- **A refused client is told its registration's state.** When the token or
  revocation endpoint refuses a client because it is waiting for approval
  or has been revoked, the `401 invalid_client` answer also carries
  `client_state` (`pending` or `revoked`, the registration's own `state`),
  so an SDK need not read `error_description`. Only an assertion that
  verifies against the client's key is told it (see Security below); every
  other refusal is unchanged.
  [docs/service-identity.md](docs/service-identity.md) §3.
- **Personal API keys.** A person makes a key on the portal (*API keys*) and
  gives it to a program, which exchanges it at the token endpoint
  (`urn:igni:params:oauth:grant-type:api-key`) for a 15-minute session as
  them — igni server's API, and their igni in Fleet. Keys expire (30, 90 or
  365 days), stop when the person signs out everywhere, leaves or is
  deactivated, and are recorded at every exchange. A key's session cannot
  make keys, take a CLI session, sign a VM in or authorise a service.
  [docs/service-identity.md](docs/service-identity.md) §22.
- **The portal shows whose licence it is, and installs one.** A *Licence*
  page for super admins (*Deployment → Licence*) shows the licensee, the
  licence ID, its kind and issue date, and each paid module's state,
  expiry, band and message, and installs a licence file chosen or pasted
  there, with the reason when it is refused. Everybody signed in sees
  *Licensed to <customer> · <licence ID>* at the foot of the sidebar;
  nothing without a licence. `GET /v1/portal/licence/notice` gains
  `customer` and `licence_id`, null without a verified licence, and the
  hourly licence job logs the licence ID and customer (or that none is
  installed). [docs/licence-file.md](docs/licence-file.md).

### Changed

- **A new organisation's Default group holds igni's agents as they are
  now.** The seed is a copy of the agents and skills igni bundles, and it
  had drifted: every agent differed, `plan_researcher` still named tools
  (`Read`, `Grep`, `Glob`, `LS`) igni no longer has, and the prompts still
  told the model to look for `ember.md`. The seed is now copied from
  igni's core, in the repository it now lives in (`igni-core`), and
  tenant migration `t0029` carries the refreshed seed to the
  organisations that already have a Default group.
- **igni's core is read from the repository it lives in.** igni's core
  left the monorepo for a repository of its own; the monorepo's copy is
  frozen and has stopped following igni. The refresh, the rehearsal
  scripts and the decision generator read `../igni-core` now, and CI
  checks that repository out under that name.
- **The offline bundle is signed with cosign instead of minisign**, with the
  same release key as the images. A customer now holds one public key
  (`cosign.pub`) and needs one tool (`cosign`): `igni-import --public-key
  cosign.pub`, or `cosign verify-blob --key cosign.pub
  --insecure-ignore-tlog=true --signature MANIFEST.sig MANIFEST` by hand.
  The signature is `MANIFEST.sig` (was `MANIFEST.minisig`), and the bundle
  format is 2: an earlier `igni-import` refuses it, so verify the first
  format-2 bundle by hand or with the `igni-import` from this release. The
  release key is a Cloud KMS key, reached by CD through GitHub OIDC from
  the `release` environment of `ignite-ember/igni__release`; there is no
  signing secret in GitHub
  ([docs/OFFLINE_UPDATES.md](docs/OFFLINE_UPDATES.md)).
- Releases are built, signed and sealed by `.github/workflows/release.yml`,
  run from the private repository `ignite-ember/igni__release` with a tag
  or a commit on `main`, and only for a commit whose tests passed on `main`
  ([docs/RELEASING.md](docs/RELEASING.md)). `cd.yml` runs the tests on each
  push to `main` and builds nothing.

### Removed

- **Inference pools.** igni server routes chat over the customer's nodes and
  nothing else; CodeIndex runs its own nodes from its own configuration.
  A node no longer has `pools`, in registration, on the portal's node page
  or in storage (migration 0029 drops the column), and `GET
  /v1/portal/inference` answers one flat list of nodes with one set of
  totals. An interface change.
- What was left of CodeIndex and Artifacts in igni server, which is not
  theirs to host:
  - the Helm chart's `artifacts:` block, its second Ingress and ConfigMap;
  - `docker-compose.artifacts.yml`;
  - the `documents_base_url` and `app_base_url` settings;
  - the Redis-database formula for each product;
  - the portal's link to Artifacts' document settings;
  - the log fields `repo`, `event_id` and `commit`, written for indexing
    and never set since.

  They are registered services like any other and deploy themselves.
- The `dev` and `stage` images of `igni-api` and `igni-portal` (`:dev-<sha>`,
  `:dev`, `:stage-<sha>`, `:stage`): nothing deployed them.

### Security

- **A client's state is told only to whoever holds its key.** The token and
  revocation endpoints checked a client's registration state before its
  assertion's signature, so an unsigned assertion naming any client id
  learned whether that client was waiting for approval or revoked. The
  signature is now verified first: an assertion that does not verify is
  refused the same way whatever the client's state.
- **The API and worker image ships the fixed zlib1g.** Two HIGH advisories in
  the base OS's zlib — CVE-2026-76844 (disclosure via path traversal) and
  CVE-2026-85091 (a heap buffer overflow in `gz_vacate()`) — were in the
  image the scheduled vulnerability scan builds, which went red on them. The
  image now installs the security update (`zlib1g`
  `1:1.3.dfsg+really1.3.1-1+deb13u1`) on top of its base, and the base's
  Debian release is named in the tag (`python:3.13-slim-trixie`) rather than
  inherited from `python:3.13-slim`, which moved from bookworm to trixie
  between two builds of the image without this file changing.

## [0.6.0] - 2026-11-03

Prepared, and never tagged or built: the release was called off before it
shipped, so no version of igni server had ever been published when this
entry was written, and none had been published when it was called off.
The entry covers the project's history up to then (development began in
November 2025) by area rather than by version, and what the release held
is in [docs/releases/v0.6.0.md](docs/releases/v0.6.0.md). The first
version that has ever shipped is 0.7.0.

### Added

- **Identity and sign-in**: email with one-time codes, Google and OIDC
  sign-in (id_token algorithms pinned), SCIM 2.0 provisioning from the
  customer's identity provider, and a break-glass key for an install whose
  identity provider is unreachable.
- **Organisations**: created by a super admin rather than claimed;
  memberships, roles, invitations and claimed email domains; a person
  belongs to one organisation at a time, and each organisation has a
  database of its own, backed up on its own.
- **Inference routing**: OpenAI-compatible chat completions routed to the
  GPU nodes a customer registers, with concurrency leases that hold across
  replicas, a fair queue when every node is full instead of a 429, and
  GPU node management with live metrics in the portal.
- **Groups**: what each group ships to igni (agents, tools, prompts,
  settings and the model), a Default group seeded with what igni ships,
  and a signed group-policy pack per person.
- **Services and extensions**: the published service-identity contract
  (igni signs with a key it holds alone; services sign in through it),
  extension review with declared reach, tool hints, interceptors and
  collision handling, assets stored as Postgres large objects, and a hold
  on an approved service's update that asks for more than was approved.
- **Cloud pieces for Fleet**: apps, spaces, VM profiles, commands in VMs,
  MCP approvals, the producer feed and events, VM profile requests decided
  by an org admin, and erasure of a removed member's or a discarded app's
  VM after the organisation's retention.
- **Audit and data protection**: an append-only audit trail (SCIM changes
  included), retention windows, subject access exports and erasure,
  revocations published to every service at once, and secrets encrypted at
  rest with rotation that reaches every encrypted column.
- **The admin portal**: the React app and its API, a Team page with each
  member's requests, tokens and last activity, and functional and visual
  end-to-end tests against a real API.
- **Paid-module licence**: an offline, signed licence file with 90 days'
  grace and then read-only; an ended evaluation is read-only for 30 days,
  then stops.
- **Operations**: a Helm chart whose NetworkPolicy denies by default in
  both directions, Docker Compose files, OpenTelemetry traces to the
  operator's collector (off by default), Prometheus metrics, a restore
  drill, and a test suite that proves the server reaches nothing outside
  its network.
- **Releases**: images on GitHub Container Registry signed with cosign and
  carrying a CycloneDX SBOM attestation; a minisign-signed offline bundle
  per release for air-gapped installs, verified by `igni-import` before
  anything is imported, which can also carry signed releases that are not
  images (Fleet's binaries); each release bundle is kept on GitHub
  Container Registry, never overwritten or deleted, for the ten years the
  Cyber Resilience Act asks.
- **Compliance documents**: the security pack, policies, risk assessment,
  statement of applicability, DPIA, records of processing, supplier
  register, incident response and vulnerability management, most generated
  from the code and checked in CI (`make check-docs`).

### Changed

- igni server is identity, organisations and the inference proxy; the git
  providers moved to CodeIndex and the document graph to Artifacts, both
  paid modules in their own repositories.
- Python 3.13 everywhere, and PyJWT in place of python-jose.
- The API image runs as uid 10001 and the portal image as uid 101.

### Removed

- The public marketing site, which moved to its own repository.
- Flower, and with it the only volume the API image had to chown.

### Fixed

- A client that gives up stops its non-streamed completion upstream, so a
  timed-out request is no longer run, and billed, again on each retry.
- A stream no longer ends when upstream is silent for longer than a
  keepalive interval.
- Every SSO sign-in failed: the callback redirected to a portal route that
  did not exist.
- SCIM deactivation was not shown in the portal and was missing from the
  audit trail.
- A membership that ended before any feed read was never recorded.
- The Team page showed usage as 0 for every member.

### Security

- No code, token or API key is kept in Redis in the clear (F151); GPU node
  keys are encrypted at rest (F150).
- One-time codes leave the log by default (F35).
- The API image no longer ships pip, whose vendored msgpack, urllib3 and
  setuptools (CVE-2025-47273) the image scan reported; the portal image
  takes Alpine's fixes for brotli, curl and expat.
- Every GitHub Action is pinned by commit SHA, and an OpenSSF Scorecard
  runs on the public repository.

[Unreleased]: https://github.com/ignite-ember/igni-server/compare/v0.7.1...HEAD
[0.7.1]: https://github.com/ignite-ember/igni-server/compare/v0.7.0...v0.7.1
[0.7.0]: https://github.com/ignite-ember/igni-server/releases/tag/v0.7.0
# 0.6.0 was prepared and never tagged, so there is no release page to
# point at: its entry names the commit that carried it.
[0.6.0]: https://github.com/ignite-ember/igni-server/tree/9ecb879
