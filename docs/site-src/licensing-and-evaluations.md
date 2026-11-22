igni has a free base and paid modules. The base — igni server, the portal, the desktop app and the CLI — is Apache-2.0 and needs no licence. The paid modules — CodeIndex, Artifacts and Fleet, plus the Factory and Autopilot — each check one licence file, offline. This page covers the file, where it goes, and what each module does as a licence runs out.

## One file, checked offline

A licence is a small JSON file signed by Ignite Ember with Ed25519. The release public key (`ie-2026`) is built into each release — igni server, the paid modules and the Fleet gateway — and every licence is verified against it offline. A separate test key exists for development only, trusted just when a component runs with its development setting (`APP_ENV=dev`, or `[licence] trust_test_key = true` on the Fleet gateway). Nothing contacts Ignite Ember: there is no activation, no call home and no online check, so an air-gapped deployment works exactly like any other.

**Each paid module checks the licence itself.** It reads the file, verifies the signature with the key built into it, and decides what it may do. igni server reads the same file only to show it on the portal, to install an uploaded one, and to write a daily audit event. A module never asks igni server whether it is licensed.

| Component | Checks the licence? |
|---|---|
| igni server, portal, desktop app, CLI | No. A missing file is ignored. |
| CodeIndex | Yes |
| Artifacts | Yes |
| Fleet | Yes, in the gateway |
| Factory, Autopilot | Listed in the licence and shown on the portal. Nothing enforces them yet. |

Models built by the Factory carry no licence and no expiry.

## What the file holds

```json
{
  "igni_licence": 1,
  "key_id": "ie-2026",
  "payload": "<base64url of the payload's JSON bytes, unpadded>",
  "signature": "<base64url of the Ed25519 signature over those bytes, unpadded>"
}
```

The payload, decoded:

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
| `modules` | Any of `codeindex`, `artifacts`, `fleet`, `factory`, `autopilot`. Each has its own expiry, because each is sold separately. |
| `users` | The band: 50, 100, 200, 500, 1,000, then any multiple of 1,000. An evaluation is for at most 25. The Factory has none. |
| `expires` | UTC. The module lapses at that moment. |
| Autopilot | Needs the Factory in the same licence. Without it, Autopilot is unlicensed. |

The file is not tied to a host, a hardware id or an organisation. A file that breaks any rule is not a licence, even if it is signed.

## Installing and renewing

Every component reads the same path: `/etc/igni/licence/licence.json`.

| Component | Setting that moves it |
|---|---|
| igni server, CodeIndex, Artifacts | `IGNI_LICENCE_FILE` |
| Fleet gateway | `[licence] file` in `/etc/igni/gateway.toml` |

**Kubernetes.** Put the file in one Secret, under the key `licence.json`:

```bash
kubectl -n igni create secret generic igni-licence --from-file=licence.json=<file>
```

Set `licence.existingSecret: igni-licence` in igni's chart. The chart mounts it read-only in the API, so the portal can show it. igni's chart does not deploy the paid modules, so mount the same Secret at the same path in each module's Deployment and on the Fleet gateway yourself.

**Docker Compose.** Put the file in one directory, `IGNI_LICENCE_DIR` (default `./licence`). `containers/docker-compose.prod.yml` mounts it into the API; mount it into each module at the same path.

**In the portal, or through the API.** A super admin can install a licence from the portal at *Deployment → Licence* (added in igni server 0.7.0), which calls `PUT /v1/portal/licence`; a program can send the file's JSON to that endpoint directly. igni server verifies it first. A file it would not accept is refused with `422` and the reason. Where the path is a mounted Secret, the answer is `409` (`licence_path_read_only`); update the Secret instead.

**Renewing is replacing the file.** Each component looks at it again at most once a minute and re-verifies it when it has changed. Nothing restarts.

A module you do not renew should stay in the new licence with its old expiry. It then goes on to read-only rather than off. Leaving it out of the file switches it off.

## When a licence runs out

```
commercial:  valid ──(expires)──▶ grace, 90 days ──▶ read-only   (for good; never off)
evaluation:  valid ──(expires)──▶ read-only, 30 days ──▶ stops
```

| State | When | What the module does |
|---|---|---|
| `valid` | Before expiry | Everything. Warns in the last 30 days (7 for an evaluation). |
| `grace` | Up to 90 days after a commercial licence expires | Everything, with a warning everywhere |
| `read_only` | From 90 days after a commercial licence expires, for good. For the 30 days after an evaluation ends. | Reads, searches and exports. Refuses changes, new indexing, new agent runs and new VMs. |
| `unlicensed` | No licence, a file that does not verify, the module not listed, or an evaluation more than 30 days past its end | Only what must always work |

**A lapsed commercial licence is never switched off.** Read-only is where it ends. Data is never deleted and exports keep working, so you can always take your data and leave.

Every request a module serves is one of three kinds:

| Kind | `valid`, `grace` | `read_only` | `unlicensed` |
|---|---|---|---|
| **Always** — health, the licence's own status, GDPR erasure and subject access, signing in and out, disconnecting a module from your own systems | Yes | Yes | Yes |
| **Read** — reading, searching, downloading, exporting | Yes | Yes | No |
| **Write** — changing anything, starting indexing, starting an agent run, starting a VM | Yes | No | No |

What that means module by module is on each module's page: [CodeIndex](/docs/codeindex), [Artifacts](/docs/artifacts) and [Fleet](/docs/fleet).

## Evaluations

An evaluation licence is an ordinary licence file with `"kind": "evaluation"`, for at most 25 users. When it ends, the modules go read-only for 30 days, so you can export what you made, and then stop. There is no grace period.

An extension, where one is agreed, is simply a new evaluation file with a later expiry; install it like any other. When the evaluation days start, and when an extension is granted, are set in your agreement with Ignite Ember, not in the software. The file carries only the dates it was issued with, and nothing in igni detects a first activation.

## Seeing where you stand

| Where | What |
|---|---|
| The portal | A banner on every page for everybody signed in, one sentence per module that is expiring, lapsed, read-only, or whose file does not verify. Nothing on a deployment with no paid module. |
| `GET /v1/portal/licence` | The whole licence, for a super admin: path, customer, kind, and each module's state. |
| `GET /v1/portal/licence/notice` | The banner's warnings, for anyone signed in. |
| `GET /v1/licence` on CodeIndex or Artifacts | That module's state and how many people it has counted. |
| The `igni-licence` header | On CodeIndex and Artifacts responses while there is something to say: `grace; days-left=60`, `valid; days-left=12`, `read_only`. |
| The audit trail | Once a UTC day while any module needs attention, a `licence:daily` event with each module's state. |
| Logs and metrics | A warning once a day in each module's log. The Fleet gateway exports `igni_gateway_licence_state{state}`. |

A refused request gets `403` with a JSON body — `detail`, `module`, `state`, `reason` — and the header `igni-licence: <state>`. CodeIndex and Artifacts close a refused WebSocket with code `4403`; the Fleet gateway closes one with `1008`.

## The band is not enforced

The `users` number is never a hard limit. A module over its band keeps working, warns, and records the overage. The band is adjusted at renewal.

| Module | Who counts, for a year from when last seen |
|---|---|
| CodeIndex | A person who uses it |
| Artifacts | A person who changes something. Readers are free. |
| Fleet | A person with an active VM. Apps never count. |

## Known limits

- **The system clock is trusted offline.** A component that checks the licence file on its own — a module, the Fleet gateway — reads the local clock, so setting it back can delay a lapse there. **igni server 0.8.0** closes this for the server: it keeps a tamper-evident central licence clock, so a lapse cannot be delayed by moving the machine's clock back.
- **The desktop app and CLI do not show the `igni-licence` header yet.** The portal banner does.
- **A Fleet node's own timed wakes are not refused in read-only yet.** See [Fleet](/docs/fleet).

## Where to go next

- **[Offline updates](/docs/server/offline-updates)** — the release bundle, which does not carry the licence file.
- **[Self-hosting the igni server](/docs/server/self-hosting-the-server)** — where `licence.existingSecret` sits among the other chart values.
