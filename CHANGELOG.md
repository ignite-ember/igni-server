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

### Added

- **Personal API keys.** A person makes a key on the portal (*API keys*) and
  gives it to a program, which exchanges it at the token endpoint
  (`urn:igni:params:oauth:grant-type:api-key`) for a 15-minute session as
  them — igni server's API, and their igni in Fleet. Keys expire (30, 90 or
  365 days), stop when the person signs out everywhere, leaves or is
  deactivated, and are recorded at every exchange. A key's session cannot
  make keys, take a CLI session, sign a VM in or authorise a service.
  [docs/service-identity.md](docs/service-identity.md) §22.

### Changed

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

- The `dev` and `stage` images of `igni-api` and `igni-portal` (`:dev-<sha>`,
  `:dev`, `:stage-<sha>`, `:stage`): nothing deployed them.

## [0.6.0] - 2026-11-03

The first tagged release. Versions before it were not tagged, so this entry
covers the project's history up to now (development began in November 2025)
by area rather than by version. The release notes are in
[docs/releases/v0.6.0.md](docs/releases/v0.6.0.md).

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

[Unreleased]: https://github.com/ignite-ember/igni-server/compare/v0.6.0...HEAD
[0.6.0]: https://github.com/ignite-ember/igni-server/releases/tag/v0.6.0
