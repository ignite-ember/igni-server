# igni server: changes and upgrades

igni server is the API, the portal and the migrations. It versions on its own cadence, independently of igni-core, the igni app and the SDKs, because different people install them at different times. The server's changelog says when a release changes an interface; while the major version is 0, a minor version may, and 0.7.0's did.

## Version now

| Package | What it is | Repository | Version now |
|---|---|---|---|
| igni server | the API, the portal, the migrations | `ignite-ember/igni-server` | 0.8.0 |

The **Helm chart** version (`0.2.0`) moves independently of the server app version. Keep the two separate.

## What is published

As of 2026-11-22:

| | Published |
|---|---|
| igni server | the git tags **v0.8.0** (and **v0.7.1**, **v0.7.0**) |

**This documentation describes 0.8.0** — the current release, which is the version the rest of the server pages are written against.

## igni server 0.7.0: the pools are gone

0.7.0 is the first release of igni server that has ever shipped, and it changes an interface.

**Inference pools are gone.** igni server routes chat over your nodes and nothing else; CodeIndex runs its own nodes from its own configuration. A node no longer has `pools` — in registration, on the portal's node page, or in storage, where a migration drops the column — and `GET /v1/portal/inference` answers one flat list of nodes with one set of totals, where it used to answer a list of pools. The `codeindex` pool type is gone with it.

**What was left of CodeIndex and Artifacts in igni server is gone too**, because it was not theirs to host: the chart's `artifacts:` block and its second Ingress and ConfigMap, `docker-compose.artifacts.yml`, the `documents_base_url` and `app_base_url` settings, the Redis-database formula for each product, the portal's link to Artifacts' document settings, and the `repo`, `event_id` and `commit` log fields. Those products are registered services like any other, and deploy themselves.

**New in 0.7.0:**

- **Personal API keys.** A person makes a key on the portal and gives it to a program, which exchanges it at the token endpoint for a 15-minute session as them. Keys expire, stop when the person signs out everywhere, leaves or is deactivated, and are recorded at every exchange. A key's session cannot make keys, take a CLI session, sign a VM in or authorise a service.
- **A licence page.** *Deployment → Licence* shows the licensee, the licence ID, its kind and issue date, and each paid module's state, expiry, band and message, and installs a licence file there. Everybody signed in sees *Licensed to &lt;customer&gt; · &lt;licence ID&gt;* at the foot of the sidebar.
- **A refused client is told its state.** A `401 invalid_client` answer now carries `client_state` (`pending` or `revoked`), so an SDK need not read `error_description`. Only an assertion that verifies against the client's key is told; every other refusal is unchanged.

**The `dev-` and `stage-` images are gone.** The release workflow builds unprefixed tags now, and it builds three of them for the API image — the commit, the version, and a moving `:latest` — and two for the portal, the commit and `:latest`. The offline bundle carries both images under the version tag whatever the registry holds.

### The offline bundle changed shape

**The bundle is signed with cosign instead of minisign**, with the same release key as the images. You now hold one public key (`cosign.pub`) and need one tool (`cosign`):

- the signature is **`MANIFEST.sig`**, not `MANIFEST.minisig`;
- the bundle **format is 2**, and an earlier `igni-import` refuses it.

So verify the first format-2 bundle by hand, or with the `igni-import` from this release. See [Offline updates](/docs/server/offline-updates).

## igni server 0.7.1 and 0.8.0

The server has moved on twice since 0.7.0. **0.8.0 is the current release** (the git tag `v0.8.0`); `v0.7.1` sits between it and `v0.7.0`.

**0.7.1** changes no interface — every entry is a fix, an addition that keeps what it was, or a documentation correction. It carries the end-to-end campaign's server-side findings:

- **The chart can mount files into the API pod.** `extraVolumes` / `extraVolumeMounts` let `INFERENCE_BACKENDS_PATH` name a file, not only the inline `INFERENCE_BACKENDS`.
- **A second igni can run on the same host.** The production compose file's container names follow `IGNI_STACK` and its host ports `IGNI_API_PORT` / `IGNI_PORTAL_PORT`; the defaults are unchanged.
- **Organisation databases have a downgrade.** `python -m app.db.sql.tenant_migrations --downgrade <revision>` (and `make downgrade-tenants REV=...`) takes the tenant template and every active organisation back, so a rollback to the previous image does not stop at a missing revision.
- **The portal shows and keeps a VM profile's volume.** The profile form now shows and sends `volume_gib` and `volume_max_gib`, so a portal edit no longer resets a volume set through the API.
- **A group's pinned model is one chat completions actually serves.** Saving refuses a pin that only `/v1/messages` serves and says why, and the portal offers only the chat-completions models.
- **The scheduled vulnerability scan scans the newest release's images** — it checks out the newest `vX.Y.Z` tag and builds that, rather than the moving default branch.

**0.8.0** tightens the trust model of a release:

- **A release build trusts only the release key.** The images and the offline bundle are verified against the one release key and nothing else; a signature from any other key is refused.
- **A tamper-evident central licence clock.** The licence check reads a central, tamper-evident clock rather than the machine's own, so a deployment cannot extend a lapsed licence by moving its system time back.

## Where the release notes live

igni server keeps its release notes in `CHANGELOG.md` in the `ignite-ember/igni-server` repository, in Keep a Changelog format, newest first.

## Where to go next

- **[Self-hosting the igni server](/docs/server/self-hosting-the-server)** — deploying the API, portal and migrations.
- **[Offline updates](/docs/server/offline-updates)** — carrying a release into a network with no route out.
- **[Connecting your GPUs: inference backends](/docs/server/inference-backends)** — routing chat over the nodes and cloud providers you own.
