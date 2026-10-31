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

* **Release keys.** Empty until Ignite Ember runs the key ceremony
  (`issue_licence.py keygen`, below) and pastes the public half into every
  copy. Until then, no deployment outside development accepts a licence,
  and every paid module stays off.
* **The test key** (`igni-test-1`) has its private half in each repository's
  test fixtures, so anybody can sign with it. It is trusted only in
  development: `APP_ENV=dev` for igni server and CodeIndex,
  `ENVIRONMENT=dev` for Artifacts (never its default, `development`), and
  `[licence] trust_test_key = true` for the Fleet gateway.
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
| Portal | A super admin uploads the file (`PUT /v1/portal/licence`). igni server verifies it first and refuses one that is not valid, with the reason. It writes the file only where the path is writable (compose). Where the path is a mounted Secret, it answers `409` and the Secret is updated instead |

Renewing is replacing the file. Each component looks at it again at most
once a minute and re-verifies it when it has changed. Nothing restarts.

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
| Portal | A banner on every page for everybody signed in (`GET /v1/portal/licence/notice`), one sentence per module that is expiring, lapsed, read-only or whose file does not verify. Nothing on a deployment with no paid module. A super admin sees the whole licence at `GET /v1/portal/licence` |
| Module responses | `igni-licence: grace; days-left=60` (or `valid; days-left=12`, `read_only`) on every response while there is something to say, for clients to show |
| Module status | `GET /v1/licence` on each Python module: the module's status and its band |
| Audit trail | Once a UTC day while any module needs attention, a `licence:daily` event in the deployment's own trail, with each module's state |
| Logs and metrics | A warning once a day in each module's log. The gateway exports `igni_gateway_licence_state{state}`, `licence_people`, `licence_band` and `wakes_refused_total{why="licence"}` |

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

`tools/licence/issue_licence.py` is Ignite Ember's tool. It runs on the
issuing machine, which holds the private key in a file or in the
environment (`IGNI_LICENCE_KEY_FILE` or `IGNI_LICENCE_KEY`), never in a
repository. `keygen` refuses to write inside a git working tree.

```bash
python tools/licence/issue_licence.py keygen --out ~/igni-licence/ie-2026.pem --key-id ie-2026
IGNI_LICENCE_KEY_FILE=~/igni-licence/ie-2026.pem python tools/licence/issue_licence.py issue \
  --key-id ie-2026 --licence-id IE-2026-0042 --customer "Example Bank N.V." \
  --expires 2027-10-29 --module codeindex=100 --module artifacts=50@2026-10-29 \
  --module factory --module autopilot=50 --out example-bank.licence.json
python tools/licence/issue_licence.py inspect example-bank.licence.json
```

It refuses a licence that would not work: a number that is not a band, a
banded module with no band, a band on the factory, or Autopilot without the
factory.

## Known limits

* **The clock is the customer's.** A deployment that sets its clock back
  delays the lapse. Nothing records the latest time seen. The contract and
  the audit right are the backstop (license-enforcement.md).
* **A node agent's own timed wakes.** A node resumes its suspended VMs at
  igni's `wake_at` itself, without asking the gateway. These are not yet
  refused in read-only (`igni__cloudvm/crates/node-agent`).
* **Clients.** The CLI and desktop clients do not show the `igni-licence`
  header yet. The portal does.
* **The development flag.** The test key is trusted with a setting the
  operator controls. Turning it on in production to avoid paying is a
  breach of the agreement that the contract and audit right catch, not
  something the code can prevent.
