# The licence file

How igni's paid modules are licensed, offline. This is the specification:
the format, the keys, the lapse sequence and what each module does in each
state. Every implementation follows it and is pinned to the same golden
file (`tests/fixtures/licence/golden.licence.json`, copied into each
module's tests).

Ignite Ember is the company. igni is the software.

## What it does, and where

A licence is one small JSON file, signed by Ignite Ember with Ed25519. The
public key is built into each release. Nothing ever contacts Ignite Ember:
there is no activation, no call home and no online check, so an air-gapped
deployment works exactly like any other.

The free base (the server, the portal and the clients, Apache-2.0) needs no
licence and ignores a missing one. Models built by the factory carry no
licence and no expiry.

**Disclosure (customer-facing).** The software maintains tamper-evident
licence-integrity data on the server and can produce a sealed integrity
report at the vendor's request. The data is a single timestamp (the latest
time the deployment has seen, so a clock set back does not revive a lapsed
licence) with a short technical history; it holds no personal data, never
leaves the server on its own, and is only ever disclosed as a sealed report
a super admin exports deliberately ("The clock", "The sealed report").

**Each paid module enforces the licence itself.** igni server is
Apache-2.0, and a check in it would be two lines to delete. So every module
reads the file, verifies the signature with the key built into it, and
decides what it may do. A module cannot be unlocked by faking igni server,
because it never asks igni server. igni server reads the same file only to
show it on the portal, to install an uploaded one, and to record the daily
audit event.

| Component | Where the check is |
|---|---|
| igni server (shows, installs, audits) | `app/services/licence/`, `app/services/deployment_licence/` |
| CodeIndex | `igni__code-index/app/licence/`, policy in `igni__code-index/app/licensing.py` |
| Artifacts | `igni__artifacts/app/licence/`, policy in `igni__artifacts/app/licensing.py` |
| Fleet (the gateway) | `igni__cloudvm/crates/licence`, enforced in `igni__cloudvm/crates/gateway/src/licence.rs` |
| Factory, Autopilot | Carried in the licence and reported on the portal. No code exists yet to enforce them |

The Python files in `app/services/licence/` here are the same, byte for
byte, as those in `igni__code-index/app/licence/` and
`igni__artifacts/app/licence/`. Change them together.

## The file

```json
{
  "igni_licence": 1,
  "key_id": "ie-2026",
  "payload": "<base64url of the payload's JSON bytes, unpadded>",
  "signature": "<base64url of the Ed25519 signature over those bytes, unpadded>"
}
```

The signature covers the payload's bytes exactly as they are, so no
implementation has to re-serialise JSON the way another one did. The
payload:

```json
{
  "format": 1,
  "licence_id": "IE-2026-0042",
  "kind": "commercial",
  "customer": "Example Bank N.V.",
  "issued": "2026-10-29T00:00:00Z",
  "modules": {
    "codeindex": {"users": 100, "expires": "2027-10-29T00:00:00Z"},
    "artifacts": {"users": 50, "expires": "2027-10-29T00:00:00Z"},
    "factory":   {"users": null, "expires": "2027-10-29T00:00:00Z"},
    "autopilot": {"users": 50, "expires": "2027-10-29T00:00:00Z"}
  }
}
```

| Field | Rule |
|---|---|
| `kind` | `commercial` (paid, pilot and partner licences) or `evaluation` |
| `modules` | `codeindex`, `artifacts`, `fleet`, `factory`, `autopilot`. Each module has its own term, because each is sold separately |
| `users` | The band: 50, 100, 200, 500, 1,000, then any multiple of 1,000. An evaluation is for at most 25. The factory has none: it is flat, for any number of models |
| `expires` | UTC. The module lapses at that moment. The issuing tool sets 00:00 UTC on the expiry date |
| Autopilot | Needs the factory in the same licence. The issuing tool refuses one without it, and a module reading one treats Autopilot as unlicensed |

A file that fails any rule is not a licence (`malformed`), even when it is
signed.

## Keys

Public keys are in `app/services/licence/keys.py` (and its copies) and
`igni__cloudvm/crates/licence/src/keys.rs`, under a key id. Only the public
half is ever in a repository.

* **Release keys.** `ie-2026`, created 2026-11-07. Its private half is in
  Cloud KMS and never leaves it; `issue_licence.py issue --kms-key` signs
  through KMS as Ignite Ember's issuing account, and checks the licence
  against `RELEASE_KEYS` before writing it. A release from before the key
  was added accepts no licence outside development.
* **The test key** (`igni-test-1`) has its private half in each repository's
  test fixtures, so anybody can sign with it. **No release trusts it, under
  any setting.** It is not in any shipped package, image or binary:
  * The Python services (igni server, CodeIndex, Artifacts) keep its public
    half in `tests/fixtures/licence/dev-keys.json`, which no image copies.
    A source checkout run with `APP_ENV=dev` (`ENVIRONMENT=dev` for
    Artifacts) trusts that file, for local development; an image never
    does. Its build writes a `RELEASE` marker beside `keys.py`, and with
    the marker there no development key is read, whatever the environment
    says or a mount puts where a checkout keeps one.
  * The Fleet gateway has the key, and `[licence] trust_test_key`, only in a
    build with the `test-licence-key` cargo feature, which only its tests
    enable. A release binary has neither, and refuses a config that names
    `trust_test_key` as an unknown key.
  * Every image and binary is checked before it is published:
    `tools/licence/check_release_image.sh` here (and
    `igni__code-index/tools/check_release_image.sh`,
    `igni__artifacts/tools/check_release_image.sh`) runs the image with every
    development setting and the key file mounted, and fails unless a
    test-key licence is refused as `unknown_key`;
    `igni__cloudvm/scripts/release/build.sh` fails a release whose binaries
    name the test key or whose build enabled the feature.
* **Rotating.** Add the new key under a new id and release. Issue new
  licences with it. Remove the old key only when no licence signed with it
  is still in its term or read-only period.

## Installing and renewing

Every component reads the same file at `/etc/igni/licence/licence.json`
(set `IGNI_LICENCE_FILE` to move it; the gateway uses `[licence] file`).

| Deployment | How |
|---|---|
| Kubernetes | One Secret with the key `licence.json`. Set `licence.existingSecret` in this chart, which mounts it read-only in the API, and mount the same Secret at the same path in each module's Deployment and in the gateway |
| Compose | One directory, `IGNI_LICENCE_DIR` (default `./licence`), mounted into the API by `containers/docker-compose.prod.yml` and into each module at the same path |
| Portal | A super admin installs the file on the portal's *Licence* page, or with `PUT /v1/portal/licence`. igni server verifies it first and refuses one that is not valid, with the reason. It writes the file only where the path is writable (compose). Where the path is a mounted Secret, it answers `409` and the Secret is updated instead |

Renewing is replacing the file. Each component looks at it again at most
once a minute and re-verifies it when it has changed. Nothing restarts.

### From the portal

*Deployment → Licence* in the sidebar, for super admins only. It shows
who the licence is for, its ID, its kind and issue date, the file's path,
and one row per paid module: its state, expiry, band (users) and the
sentence the modules show people.

To install, choose the `.json` file Ignite Ember sent, or paste it. The
file's contents are put in the box, so what is about to be sent is on the
screen, and nothing is sent that is not JSON. igni server verifies it
against the keys built into this release before writing anything:

| Answer | What the page says |
|---|---|
| `200` | Installed, naming the licence and the customer. The page, the banner and the licensee line refresh |
| `422` | Not installed, with the reason and what to do: `malformed` (ask for the file again), `unknown_key` (a licence for another release, or a test licence), `invalid_signature` (it was changed after signing; install it exactly as sent) |
| `409` | The licence is valid, but the path is a mounted Secret or a read-only volume: put it in the Secret instead |

### Where the licensee is shown

Ignite Ember protects the paid modules with this file, the contract and
traceability, not DRM, so the licence names its customer wherever it is
in use:

* **The portal**, on every page, for everybody signed in: *Licensed to
  Example Bank N.V. · IE-2026-0042* at the foot of the sidebar, from
  `customer` and `licence_id` in `GET /v1/portal/licence/notice`. Only a
  licence that verifies is quoted, and with none there is nothing there
  at all.
* **igni server's log**, once an hour from every replica:
  `licence: IE-2026-0042, licensed to Example Bank N.V.`;
  `licence: no licence installed` when there is no file; or, when the file
  is there but does not verify,
  `licence: the licence file at /etc/igni/licence/licence.json is not valid (unknown_key); no module is licensed`.
  The customer is an organisation, so the line carries no personal data.
* **Each module's log**, when it loads the licence.

**Renewing without a module.** A module the customer does not renew stays
in the new licence with its old expiry, so it goes on to read-only rather
than off. Leaving it out would switch it off.

## The lapse sequence

```
commercial:  valid ──(expires)──▶ grace, 90 days ──▶ read_only   (final; never "off")
evaluation:  valid ──(expires)──▶ read_only ──(+30 days)──▶ unlicensed
```

| State | When | What the module does |
|---|---|---|
| `valid` | Before expiry | Everything. Warns in the last 30 days (7 for an evaluation) |
| `grace` | 0 to 90 days after expiry | Everything, with a warning everywhere |
| `read_only` | From 90 days after expiry, for good (commercial); for the 30 days after an evaluation ends | Reads, searches and exports. Refuses changes, new indexing and new agent runs |
| `unlicensed` | No licence, a file that does not verify, the module not in it, an evaluation 30 days past its end, or Autopilot without the factory | Only what must always work |

Autopilot is never better off than the factory. If the factory lapses,
Autopilot follows it.

`read_only` is where a paid module ends. Data is never deleted and exports
never stop, so a customer can always leave
([EXIT_TEST_RUNBOOK.md](EXIT_TEST_RUNBOOK.md), scenario A).

An ended evaluation goes one step further, to `unlicensed`, 30 days after it
ends. That is not deletion either. The licence check only decides which
operations are allowed; it deletes nothing, so the module's data stays in its
databases and can be dumped with standard PostgreSQL tools
([EXIT_TEST_RUNBOOK.md](EXIT_TEST_RUNBOOK.md), step A9). The 30 days are for
reading and exporting before that.

### What each state allows

Every request or job is one of three operations:

| Operation | `valid`, `grace` | `read_only` | `unlicensed` |
|---|---|---|---|
| **Always**: health, the licence's own status, GDPR erasure and subject access, signing in and out, disconnecting a module from the customer's own systems | Yes | Yes | Yes |
| **Read**: reading, searching, downloading, exporting | Yes | Yes | No |
| **Write**: changing anything, starting indexing, starting an agent run, starting a VM | Yes | No | No |

A refused HTTP request gets `403` with
`{"detail": "<the sentence>", "module", "state", "reason"}` and an
`igni-licence: <state>` header. A refused WebSocket is closed with code
`4403`. A new route that changes something is a write unless its module's
policy says otherwise, so it is refused in read-only until somebody decides
it should not be.

### CodeIndex

| Always | Read | Write |
|---|---|---|
| `/health`, `/version`, `GET /v1/licence`, igni's erasure and export calls (`/v1/igni/*`), sign-in (`/auth/*`); unlinking an account, removing a GitHub App or GitLab OAuth client, removing an installation, revoking an integration, unlinking a repository; webhooks (accepted, and not queued) | Every page and GET; changeset URLs, snapshots and downloads (the index as JSONL, which is the export); preflight; repository discovery; the admin views' batched lookups | Index runs; linking accounts; installing and connecting integrations; adding projects; repository switches; settings; and in the workers, every indexing run, pull-request review and repository preparation queued before the lapse |

A push or pull request that arrives in read-only is answered
`{"accepted": false, "reason": "not_licensed"}`, so the provider does not
retry it. One already queued is marked ignored with the licence's reason.

### Artifacts

| Always | Read | Write |
|---|---|---|
| `/health`, `/ready`, `/version`, `GET /v1/licence`, `/v1/igni/*`, `/auth/*`; removing an organisation's graph | Every GET: documents, versions, comments, lineage, passages, search, projects, the public share link, the comment stream (everything the extension's export reads); the archivist's read-only Cypher; finding a folder's project; pointing an organisation at its graph | Publishing and deleting documents, new versions, drafts, comments and replies, access, audience and owner changes, filing, projects, lineage decisions; the document room's WebSocket, which carries live edits |

### Fleet

The gateway starts and resumes every VM, so it is where Fleet's licence is
enforced (`App::wake` and the pre-wake path).

| Always | Read | Write |
|---|---|---|
| Health and metrics, node registration and heartbeats, the admin API (drain, remove, audit), halting and suspending, removals and erasures, producer exports (the node agent runs them without starting the VM) | `GET /v1/me` | Starting, resuming or provisioning a VM: connections, sign-in, commands, events, timed wakes, pre-wakes, moves |

In read-only, events and commands wait at igni server, as they do when no
node has room. Nothing is lost.

### Factory and Autopilot

Their entitlements are in the licence and shown on the portal, and the
Autopilot rule is enforced wherever their status is read. Neither has code
in these repositories to gate yet. Factory models are never affected: they
carry no licence.

## Warnings

| Where | What |
|---|---|
| Portal | A banner on every page for everybody signed in (`GET /v1/portal/licence/notice`), one sentence per module that is expiring, lapsed, read-only or whose file does not verify, and one while the system clock is behind a time the deployment has already seen ("The clock"). Nothing on a deployment with no paid module. A super admin sees the whole licence on the *Licence* page (`GET /v1/portal/licence`) |
| Module responses | `igni-licence: grace; days-left=60` (or `valid; days-left=12`, `read_only`) on every response while there is something to say, for clients to show |
| Module status | `GET /v1/licence` on each Python module: the module's status and its band |
| Audit trail | Once a UTC day while any module needs attention, a `licence:daily` event in the deployment's own trail, with each module's state |
| Logs and metrics | A warning once a day in each module's log. The gateway exports `igni_gateway_licence_state{state}`, `licence_people`, `licence_band` and `wakes_refused_total{why="licence"}` |

## The clock

The licence is offline, so the time is the deployment's own. Winding the
clock back past `expires` does not make a licence valid again. The
deployment keeps a single *high-water mark* — the latest time it has
seen — and every component evaluates the licence at

    max(now, mark − 24 hours, the licence's issued)

With a correct clock the mark is never ahead of it, and nothing changes.
An NTP correction of less than a day changes nothing either.

**One central mark.** igni server holds the deployment's one mark. The
paid modules and the Fleet gateway no longer keep their own: they read it
from igni server over their existing link (`GET /v1/platform/licence/clock`,
a platform call) and cache the last value, so they keep working while igni
server is briefly unreachable. One mark means a clock wound back on any
one host cannot revive a lapsed licence anywhere.

**Kept in an encrypted file, not a table.** igni server keeps the mark and
a short tamper-event history in one small binary file (mode 0600, a
non-descriptive name) on a writable, persisted volume — `IGNI_STATE_DIR`,
*not* the read-only licence dir. The file is AES-256-GCM; the key is
derived in code from a per-deployment install secret written into the
file's header plus a constant compiled into the release. This is
**obfuscation plus integrity, not secrecy**: the application must read the
file to enforce the licence, so anyone holding both the file and the
binary can derive the key. What it buys is that the bytes carry no
readable marker, a single edited byte fails the authentication tag, and a
removed, replaced or replayed-older file is detected. The honest backstop
against deliberate, coordinated tampering stays the contract and the audit
right (see "The band"). Implementation:
`app/services/deployment_licence/integrity_state.py`.

| | |
|---|---|
| When it moves on | On igni server, at start and every 10 minutes, to the earlier of its clock and the database's, but only while the two agree within 48 hours. A clock set wrongly into the future that the database does not share moves nothing, and correcting it puts everything back. The modules and the gateway never advance it; they read the central value |
| The first start | Seeded from igni server's newest `licence:daily` audit event, when the database's clock could have reached it |
| Tamper | A file that is missing, unreadable, edited, or older than the audit trail (a replayed copy) on a deployment that clearly has history is treated as tamper, never as a clean start: the mark is taken from the audit trail, a tamper event is recorded in the file, and a super admin sees a neutral `integrity` notice on `GET /v1/portal/licence`. The detail is in the file's event history and the sealed report, never in the notice |
| A renewal | Applies at once: a renewed licence's later `expires` is valid whatever time it is evaluated at |
| When the clock is behind (admin only) | More than 24 hours behind the mark: a warning in igni server's log, and `clock` on `GET /v1/portal/licence` (the super-admin overview — not the everybody banner). The sentence: "The system clock is behind a time this deployment has already seen. The licence is evaluated as of no earlier than *time*; fix the clock." |
| Single-replica by default | The default install is one API replica on a ReadWriteOnce volume, which works on every cluster (the chart defaults `api.replicaCount: 1` and `state.persistence.accessMode: ReadWriteOnce`). The file stays the single source of truth |
| Multi-replica (opt-in) | To run more than one API replica, raise `api.replicaCount`, set `state.persistence.accessMode: ReadWriteMany` on a storage class that supports it, and the file then sits on a shared volume with writes serialised by a Postgres advisory lock (one writer at a time). Where ReadWriteMany is not available, keep `api.replicaCount: 1` |

The shared mark logic is `app/services/licence/clock.py` (copied into each
module like the rest of the licence core) and `crates/licence/src/clock.rs`
in the Fleet gateway; the encrypted file and the central API are igni
server's.

## The sealed report

A super admin can export a **sealed licence-integrity report** for Ignite
Ember (`POST /v1/portal/licence/integrity-report`, audited like every
portal change). It carries the mark, the file's write history and any
tamper events, the licence id and the per-module usage — no personal data.

It is sealed so only Ignite Ember can open it (hybrid encryption): a fresh
random AES-256-GCM key encrypts the report, and that key is wrapped with
RSA-OAEP(SHA-256) to Ignite Ember's integrity-report public key. The
matching private half lives only in Cloud KMS (project
`ignite-ember-signing`, key ring `igni-release`, an `asymmetric-decryption`
key — *not* the Ed25519 licence signing key), so opening the report needs
`cloudkms.cryptoKeyDecrypter` on that key. The customer hands over the blob
without reading it; Ignite Ember opens it with `tools/licence/open_report.py`.
The one-time key ceremony is in igni__business-ops `ops/key-ceremony.md`.

**Records (internal).** The integrity file is an on-disk store, not a
database table, and holds no personal data: a per-deployment install id
(random bytes), the high-water timestamp, and a bounded technical history
of mark advances and tamper events. It is therefore outside the
table-based Article 30 register (`docs/RECORDS_OF_PROCESSING.md`, generated
from the schema) by design, not by omission — the former `licence_clock`
table is removed. A sealed integrity report is produced only when a super
admin exports one, carries no personal data, and is disclosed only to the
vendor; the customer-facing disclosure is in "What it does, and where".

## The band

The band is never enforced. A module over its band keeps working, warns,
and records the overage. The band is adjusted at renewal, forward only
(license-enforcement.md, "Soft cap, not a hard block").

| Module | Who counts, for a year from when last seen |
|---|---|
| CodeIndex | A developer who uses it (any person-authenticated call) |
| Artifacts | A person who changes something. Readers are free |
| Fleet | A person with an active producer. Apps and spaces never count |

CodeIndex and Artifacts keep one row per person in their shared plane
(`licence_seats`), so a person in two organisations counts once.

## Issuing

`tools/licence/issue_licence.py` is Ignite Ember's tool. It signs with
the release key in Cloud KMS (`--kms-key`, or `IGNI_LICENCE_KMS_KEY`),
through `gcloud`, as the account `gcloud` is logged in as; only Ignite
Ember's issuing account may sign with that key, and every signature is in
the key project's audit log. The private key never reaches the machine.

```bash
KEY=projects/ignite-ember-signing/locations/europe-west4/keyRings/igni-release
CLOUDSDK_ACTIVE_CONFIG_NAME=ignite-ember \
IGNI_LICENCE_KMS_KEY=$KEY/cryptoKeys/licence-ie-2026/cryptoKeyVersions/1 \
python tools/licence/issue_licence.py issue \
  --key-id ie-2026 --licence-id IE-2026-0042 --customer "Example Bank N.V." \
  --expires 2027-10-29 --module codeindex=100 --module artifacts=50@2026-10-29 \
  --module factory --module autopilot=50 --out example-bank.licence.json
python tools/licence/issue_licence.py inspect example-bank.licence.json
```

`--key-file` (or `IGNI_LICENCE_KEY_FILE` / `IGNI_LICENCE_KEY`) signs with a
key file instead: the test key, or one made with `keygen`, which refuses to
write inside a git working tree.

It refuses a licence that would not work: a number that is not a band, a
banded module with no band, a band on the factory, Autopilot without the
factory, the KMS key under a key id that is not in `RELEASE_KEYS`, or a
signed licence that does not verify against `RELEASE_KEYS`.

## Known limits

* **The clock is the customer's, within limits** (see "The clock"). A
  deployment that resets every component's database to an old backup and
  winds every clock back with it resets the latest time seen. That takes
  deliberate, coordinated tampering; the contract and the audit right are
  the backstop (license-enforcement.md). A clock set wrongly into the
  future on the application host *and* the database host together moves
  the mark on: after the clocks are corrected, the licence is evaluated
  up to a day before that time until the stored mark is cleared, which is
  a support case.
* **A node agent's own timed wakes.** A node resumes its suspended VMs at
  igni's `wake_at` itself, without asking the gateway. These are not yet
  refused in read-only (`igni__cloudvm/crates/node-agent`).
* **Clients.** The CLI and desktop clients do not show the `igni-licence`
  header yet. The portal does.
* **A modified release.** No setting makes a release trust the test key,
  but whoever runs the image controls the machine: one who edits the code
  inside it (as root, or by mounting over its files) can make it trust any
  key. That is a breach of the agreement that the contract and audit right
  catch, not something the code can prevent.
