# Exit-test runbook

How a customer rehearses leaving Ignite Ember, and what to keep as evidence.
This document is part of the igni security pack ([SECURITY_PACK.md](SECURITY_PACK.md)).

Ignite Ember is the company. igni is the software.

## 1. Purpose and scope

DORA requires a financial entity to have a documented exit strategy for ICT
third-party providers that support critical or important functions, and to
test it. This runbook gives the customer a repeatable test of that strategy
for igni. It is not legal advice, and it does not decide whether igni supports
a critical or important function in your organisation. That classification is
yours.

The runbook covers:

* the igni base: the server, the portal, the CLI and desktop clients, under
  Apache-2.0 (`LICENSE`);
* the paid modules CodeIndex and Artifacts;
* the models: igni 1.0 and the domain models the Factory builds for you.

Fleet is on the price list, but it is not packaged for
customers yet (G11), so this runbook checks its licence states in scenario A
and does not rehearse its exit. Autopilot is in the licence and on the price
list but has no code in the repositories reviewed for this runbook, so
there is nothing to test.

An evaluation ends the same way a paid term does, but faster: read-only for
30 days, then the paid modules stop. Scenario A, step A9, rehearses it,
because a customer that evaluates and does not buy leaves through it.

Every step says whether the code supports it today. Where it does not, the
step is marked **Not built** and is listed again in section 6.

### What the commercial terms say

These are the terms the runbook tests against. They are stated in Ignite
Ember's commercial documents, not in this repository's code.

| Term | Source |
|---|---|
| A lapsed paid licence keeps running with warnings for 90 days, then becomes read-only | Product catalogue, "Expiry"; licence enforcement strategy |
| The base and the customer's models keep running after a lapse | Product catalogue; the EULA §4 |
| Models built by the factory carry no licence key and no expiry. The customer owns the weights, the dataset and the evaluation set | Product catalogue, "Domain models" |
| Paid-module source is held in verified escrow and released if Ignite Ember stops supporting igni | Escrow terms (proposed 2026-10-22, not yet signed with any agent) |
| Evaluation keys go read-only for 30 days at expiry, then stop | Product catalogue |

The paid modules enforce the first and last of these in code
([licence-file.md](licence-file.md)). The EULA §4 currently says
something different: on expiry the customer "must stop using the Premium
Features and remove them". Section 6 lists this as a gap.

### What runs where

All of igni's server-side state is in the customer's own PostgreSQL. Ignite
Ember hosts nothing and has no route into the deployment. Two tests defend
this. A source scan fails the build if the server's or the client's source
names an outbound host that is not on the allow-list
(`tests/test_nothing_phones_home.py`). A runtime test boots the server and
fails on any connection outside the network
(`tests/test_igni_reaches_nothing_outside_the_network.py`). Neither sees
native-code networking or a child process's own connections
([SECURITY_PACK.md](SECURITY_PACK.md), Gaps).

| Component | Data it holds | Where |
|---|---|---|
| igni server, shared plane | Accounts, organisations, the `tenants` registry, registered services, extension bundles and their assets (large objects) | Database `POSTGRES_DB` |
| igni server, per organisation | That organisation's conversations, audit trail and the rest of its data | One database per organisation, `<POSTGRES_DB>_t<n>` |
| Redis | Concurrency leases and one-time sign-in codes. Nothing to export | Customer's Redis |
| CodeIndex (paid) | Its index, per organisation | Its own PostgreSQL databases, with its own `tenants` registry |
| Artifacts (paid) | Documents, versions, comments, lineage | Its own PostgreSQL databases, with its own `tenants` registry |
| Models | Weights, tokenizer, configuration | Files on the customer's GPU hosts, served by vLLM |

## 2. Scenarios

| | Scenario | What it proves |
|---|---|---|
| A | The paid licence lapses, or an evaluation ends | Each paid module warns for 90 days, then goes read-only: its data stays readable and exportable. An ended evaluation is read-only for 30 days, then the modules stop, and their data stays in the customer's databases. The base and the models are untouched |
| B | Ignite Ember stops supporting igni | The customer can build, run and restore igni without Ignite Ember, and can obtain the paid modules' source through escrow |
| C | The customer leaves igni entirely | Every piece of customer data and every model can be taken out in an open format, and the deployment can be destroyed |

Run each scenario on a non-production copy restored from a production backup.
The EULA §2 permits copies for testing and disaster recovery and
does not meter them.

### Conventions used below

```bash
export IGNI=https://igni.example.internal      # your deployment
export ADMIN_TOKEN=...                         # an organisation admin's bearer token
export SUPER_TOKEN=...                         # a deployment (super) admin's bearer token
```

Organisation admin endpoints are under `/v1/portal` and need `require_admin`.
The registered-services endpoints need a deployment admin.

## 3. The scenarios, step by step

### Scenario A: the paid licence lapses

**Preconditions**

* A non-production copy of the deployment, restored from a production backup
  (scenario B, step B4, describes the restore).
* At least one paid module deployed and approved on the portal's Services page.
* At least one model node registered and serving.
* The test plan names a person who can sign in as an organisation admin and
  one who is a deployment admin.

**How the lapse is rehearsed**

Each paid module checks the licence file itself, offline, against Ignite
Ember's public key built into the release ([licence-file.md](licence-file.md)).
The test does not wait a year. Ignite Ember issues two **exit-test licences**
for the non-production copy, for the same modules and bands as production:

* **grace**: expired 10 days before the test day;
* **read-only**: expired more than 90 days before the test day.

Installing one is the same act as renewing: the file is replaced, and each
module reads it again within a minute. The production licence is not
touched.

This needs a release built with Ignite Ember's release key. Until Ignite
Ember has generated it (section 6, G1), a development copy (`APP_ENV=dev`)
can rehearse the steps with licences signed by the published test key.

**Steps**

A1. Record the baseline.

```bash
curl -fsS $IGNI/version
curl -fsS $IGNI/ready
curl -fsS -H "Authorization: Bearer $SUPER_TOKEN" $IGNI/v1/portal/licence
curl -fsS -H "Authorization: Bearer $SUPER_TOKEN" $IGNI/v1/portal/clients
curl -fsS -H "Authorization: Bearer $ADMIN_TOKEN" $IGNI/v1/portal/inference/models
```

`/v1/portal/licence` shows the installed licence and each module's state.
`/ready` reports Postgres and Redis separately. `/v1/portal/clients` lists
each registered service, CodeIndex and Artifacts among them, with its state.

A2. Take a backup and keep it (the procedure is in section 4.1).

A3. Install the grace licence. Upload it as a deployment admin, or replace
the licence Secret (Kubernetes) or file (compose) that every component
mounts:

```bash
curl -fsS -X PUT -H "Authorization: Bearer $SUPER_TOKEN" -H "Content-Type: application/json" \
  --data @exit-test-grace.licence.json $IGNI/v1/portal/licence
# or: kubectl create secret generic igni-licence --from-file=licence.json=exit-test-grace.licence.json \
#       --dry-run=client -o yaml | kubectl apply -f -
```

The upload is verified before it is installed, and refused with the reason
if it is not a valid licence. Where the licence is a mounted Secret the
upload answers `409` and the Secret is replaced instead.

A4. Check the grace period: everything works, with warnings.

* The portal shows a banner on every page naming each module, the expiry
  date and the date it becomes read-only
  (`GET /v1/portal/licence/notice`).
* Every answer from a paid module carries `igni-licence: grace; days-left=<n>`,
  and `GET <module>/v1/licence` reports `"state": "grace"`.
* Writing still works: publish a document in Artifacts; ask CodeIndex for an
  index run.
* Within an hour, the deployment's own audit trail holds a `licence:daily`
  event with each module's state, and one more each day. It is in the shared
  plane, not in an organisation's export:

```bash
psql -d <POSTGRES_DB> -c "SELECT occurred_at, target_id, detail FROM audit_events WHERE route = 'licence:daily'"
```

A5. Install the read-only licence, as in A3, and check read-only.

* Reads, searches and exports work: open, search and export documents from
  Artifacts; download a CodeIndex changeset or snapshot.
* Changes are refused with `403`, the reason and `"state": "read_only"`:
  publishing or deleting a document, a comment, an index run. A push to a
  connected repository is answered `"reason": "not_licensed"` and nothing is
  indexed.
* Fleet, where deployed: a person's connection is refused with the same
  sentence and no VM starts. Events and commands wait at igni server.
* GDPR requests still work: `POST /v1/portal/subject-access` and
  `POST /v1/portal/erasure` reach each module.

[licence-file.md](licence-file.md) lists, per module, which operations are
reads and which are writes.

A6. Check that the base and the models are untouched.

* `GET /ready` still reports both dependencies healthy.
* A user can sign in, start a conversation and get an answer from a model.
* `GET /v1/portal/inference/models` still lists the models.
* `GET /v1/portal/audit-events/export` still returns the audit trail
  (section 4.2).

A7. Check that each module's data is readable without the module. Stop it,
dump its databases with the PostgreSQL client tools (section 4.1, "Paid
module databases") and list the archive:

```bash
pg_restore --list <module-db>.dump | head
```

A8. Reinstall the production licence, as in A3. Within a minute every
module is back to `valid`, with no restart, and its data is unchanged.

A9. Rehearse the end of an evaluation. Ignite Ember issues two more
exit-test licences with `kind: evaluation`: one that ended 10 days before
the test day, and one that ended more than 30 days before it. Install each
as in A3.

* With the first, each module is `read_only` with the reason
  `evaluation_expired`: reads and exports work, changes are refused, as in
  A5. The portal banner names the date the modules stop.
* With the second, each module is `unlicensed`: only the operations that
  always work remain (health, the licence's own status, GDPR erasure and
  subject access, signing in and out, disconnecting the module). Reads are
  refused. Nothing is deleted: dump the module's databases as in A7 and
  list the archive.
* The base and the models still work, as in A6.

Then reinstall the production licence, as in A8.

**Expected result**

* With the grace licence, every module works and warns: the portal banner,
  the `igni-licence` header and the daily audit event.
* With the read-only licence, every module reads and exports and refuses
  changes and new work. It is never switched off.
* The base, sign-in, conversations, inference and the audit trail work
  throughout.
* Each module's data is intact in its own databases and readable with
  standard PostgreSQL tools.
* Reinstalling the production licence restores every module without a
  restart.
* With an ended evaluation, every module is read-only for 30 days and then
  stops, and its data is still in its databases and readable with standard
  PostgreSQL tools.

**Evidence for the auditor**

* Output of A1, A6 and A8, timestamped, and the version tested
  (`GET /version`).
* The `/v1/portal/licence` output after A3, A5 and A8.
* The response headers and bodies from A4 and A5, including at least one
  refused write.
* The `licence:daily` audit events from A4.
* The `pg_restore --list` output from A7, and from A9 after the
  evaluation stopped.
* The `/v1/portal/licence` output for both evaluation licences in A9.

### Scenario B: Ignite Ember stops supporting igni

This scenario has two halves. B1 to B5 prove the base does not need Ignite
Ember. B6 to B8 rehearse the escrow release for the paid modules.

**Preconditions**

* A copy of the igni server source at the version you run. The base is
  Apache-2.0, so this copy is yours to keep. Store it in your own source
  control, with the release tag.
* Your own container registry.
* For B6 and B7: a signed tri-party escrow agreement. **None exists yet.** The
  escrow terms are a proposal dated 2026-10-22, and no escrow agent has been
  chosen (section 6, G7). Ignite Ember's deposit tooling exists and has
  been dry-run, so the steps below say what will happen once an agreement
  does.

**Steps**

B1. Confirm nothing in the running system calls Ignite Ember. Run both
egress tests from your copy of the source:

```bash
uv run pytest tests/test_nothing_phones_home.py tests/test_igni_reaches_nothing_outside_the_network.py
```

The second needs the test PostgreSQL and Redis the suite uses.

Then, in the test environment, block all egress except to your own
PostgreSQL, Redis, identity provider, mail relay and model nodes. In
Kubernetes, `networkPolicy.enabled: true` with `networkPolicy.egress` set (one
entry per destination, with its ports) does this (`charts/igni/templates/networkpolicy.yaml`). The chart warns that
the policy does nothing on a CNI that does not enforce NetworkPolicy, so
confirm enforcement first.

B2. Build the server and portal images yourself, from source, without any
Ignite Ember registry.

```bash
docker build -f containers/be/Dockerfile -t registry.example.internal/igni-api:<tag> .
docker build -f portal/Dockerfile -t registry.example.internal/igni-portal:<tag> portal
docker push registry.example.internal/igni-api:<tag>
docker push registry.example.internal/igni-portal:<tag>
```

The compose stack requires an explicit image reference (`IGNI_IMAGE`,
`IGNI_PORTAL_IMAGE` in `containers/docker-compose.prod.yml`), so it accepts
your registry without change.

B3. Mirror the paid-module images and the model files you already hold into
your own registry and storage. The paid modules are private images supplied
by Ignite Ember. Once support stops, no new versions of them will arrive
until escrow is released.

B4. Restore the latest production backup into the test environment using the
self-built images (section 4.1, "Restoring"). Then migrate:

```bash
alembic upgrade head && python -m app.db.sql.tenant_migrations
```

This is the command the `migrate` service runs
(`containers/docker-compose.prod.yml`). On a restored archive from the same
release it applies nothing.

B5. Run the restore drill shipped with the source. It uses the shipped backup
script, restores the shared plane and two organisations into fresh
databases, compares schema, row counts and stored credentials byte for byte,
checks that the append-only trigger on `audit_events` still refuses an
UPDATE, and boots the API against the result:

```bash
tools/restore-drill.sh
```

B6. Ask the escrow agent for the verification report of the latest deposit.
Under the proposed terms Ignite Ember deposits each paid module per release,
made with Ignite Ember's deposit tooling, and the agent rebuilds it offline and
confirms it runs. Check the report against the deposit's own instructions:
every deposit carries a `BUILD_VERIFY.md` listing the exact commands, and
`DEPOSIT.json` naming the commits. Compare those commits with the module
versions you run. **Blocked:** no escrow agreement is signed, so there is no
agent and no report. Until there is, ask Ignite Ember for the log of its own
dry run of the same verification, which builds the deposit with no network
in a clean container and smoke-tests the result.

B7. Rehearse a test release, if the agreement allows one. Take the released
deposit and run, on a host with no network:

```bash
# after decrypting the deposit with your key, the deposit's own steps:
sha256sum --quiet -c MANIFEST
build/build-offline.sh        # docker load + docker build --network=none
build/smoke-test.sh
```

Push the image to your registry, deploy it beside the restored base, and
approve it on the Services page. **Blocked**, for the same reason as B6: no
agreement exists, so no deposit can be released to you. The tooling has been
dry-run by Ignite Ember (G7).

B8. Disable the desktop client's update check, or point it at your own
mirror. By default the desktop client checks
`https://github.com/ignite-ember/igni/releases/latest/download/latest.json` on
launch (`ember-code/clients/tauri/src-tauri/tauri.conf.json`).
`update_check_ttl: 0` turns the check off, and `update_endpoint` points it
at a mirror inside your network. Under `air_gapped: true` the app asks only
that mirror, or nothing (`ember-code/docs/CONFIGURATION.md`, "Air-gapped
mode"). Updates stay signed, so a mirror cannot substitute a build.

**Expected result**

* The egress test passes, and the deployment works with egress blocked.
* Images built from source run the deployment.
* A production backup restores and the application reports ready against it.
* The restore drill passes.
* Not testable until an escrow agreement exists: B6 and B7. What can be
  tested before then is Ignite Ember's dry run of the deposit (G7).

**Evidence for the auditor**

* The commit hash and tag of the source copy you hold.
* The egress test output and the network policy you applied.
* Build logs and image digests from B2.
* The restore log, the `MANIFEST` of the backup run used, and the output of
  `GET /ready` after restore.
* The full output of `tools/restore-drill.sh`.
* When available: the escrow agent's verification report and the build log
  of the test release, with the `DEPOSIT.json` and `MANIFEST` of the deposit
  it covers. Until then: the log of Ignite Ember's own verification dry run.

### Scenario C: the customer leaves igni entirely

**Preconditions**

* A destination system chosen for each kind of data, or a decision to archive
  it.
* Your retention obligations for the audit trail and for conversations are
  known, including any legal holds (`GET /v1/portal/orgs/{org_id}/legal-holds`).

**Steps**

C1. Take a final full backup with the shipped script (section 4.1). This is
the complete copy of the igni server's data in PostgreSQL's custom archive
format, one archive per organisation.

C2. Export the audit trail as JSON Lines for your SIEM or archive (section
4.2). Page through with `since` until a batch returns fewer than `limit`
lines.

C3. Dump the paid-module databases (section 4.1, "Paid module databases").

C4. Export documents from Artifacts in a readable format if the destination
needs them as files rather than as a database (section 4.4).

C5. Copy every model you own: the merged weights, the adapters, the dataset
and the evaluation set (section 4.5). Load one into a model server outside
igni and send it a request, to prove the weights work without igni.

C6. Prove the archives are usable without igni. Restore one organisation's
archive into a plain PostgreSQL 17 instance with no igni software, and query
it:

```bash
createdb exit_check
pg_restore -d exit_check --exit-on-error igni-<stamp>/org-<org_id>-igni_t<n>.dump
psql -d exit_check -c '\dt'
psql -d exit_check -c 'SELECT count(*) FROM audit_events'
```

C7. Handle requests from individuals before you decommission. For a person
who asks for their data, `POST /v1/portal/subject-access` returns everything
the deployment holds about them, with the GDPR Article 15 context, and asks
each approved service (Artifacts among them) for its part. For erasure,
`POST /v1/portal/erasure`.

C8. Decommission. Revoke and remove every registered service
(`POST /v1/portal/clients/{client_id}/revoke`, then
`DELETE /v1/portal/clients/{client_id}`), stop all workloads, then drop the
databases and delete the backup directory under your own disposal
procedure. igni has no single "destroy everything" command. Dropping the
databases is the PostgreSQL operation, and it is yours to run. Deleting an
organisation through the portal (`DELETE /v1/portal/organizations/{org_id}`)
does not destroy its data: the organisation is detached and its database is
kept (`containers/backup.sh`, header).

C9. Uninstall the clients from developer machines.

**Expected result**

* Every archive restores into plain PostgreSQL without igni.
* The audit trail is in your SIEM or archive as JSON Lines.
* The models load and answer in a model server with no igni component.
* No igni workload, database or backup remains, and Ignite Ember holds
  nothing that needs returning or deleting (the EULA §4).

**Evidence for the auditor**

* The backup `MANIFEST`, the archive sizes and their checksums.
* The `pg_restore` and `psql` output from C6.
* The audit export files and the count of lines in each.
* The model file listing with checksums, and the request and response from
  C5.
* Your disposal record for the databases and the backup directory.

## 4. Data and model export

### 4.1 Database backups (the complete export)

The full export of the igni server is the backup produced by
`containers/backup.sh`. It is PostgreSQL's custom archive format (`pg_dump
-Fc`), readable by `pg_restore` without any igni software.

One run writes one directory:

```
igni-20261024T030000Z/
  MANIFEST                       kind, org_id, tenant number, state, database, archive
  shared-igni.dump               the shared plane, including extension assets (large objects)
  org-<org_id>-igni_t1.dump      one organisation's database
  org-<org_id>-igni_t2.dump      detached (deleted) organisations too
```

Each archive is checked with `pg_restore --list` and must contain a known
table before the run counts. A run is complete only if every archive in it
verified. Until then the directory stays `igni-<stamp>.part`.

**Running it.** Compose:

```bash
docker compose --profile bundled --profile backup -f containers/docker-compose.prod.yml up -d
# one run now, from the same image:
docker compose --profile backup -f containers/docker-compose.prod.yml run --rm backup once
```

The service's entrypoint is the backup script, so `once` is its argument.
Helm: set `backup.enabled: true` and `backup.existingClaim`
in `charts/igni/values.yaml`. The chart runs the same script as a CronJob
(`charts/igni/templates/backup-cronjob.yaml`). Runs are retained for 14 days
by default.

**Restoring.** `charts/igni/README.md`, "Restoring one organisation", gives
the commands. To rebuild a whole server: restore `shared-<db>.dump` into
`POSTGRES_DB`, then each organisation's archive into the database name its
`MANIFEST` line gives, then run the migrate job, which recreates the tenant
template.

**Paid module databases.** CodeIndex and Artifacts keep their own PostgreSQL
databases, with their own `tenants` registries and per-organisation databases.
`containers/backup.sh` does not dump them, and the chart does not deploy them.
Dump each one with `pg_dump -Fc` yourself (section 6, G5).

### 4.2 Audit trail

`GET /v1/portal/audit-events/export` returns the organisation's audit events
as JSON Lines (`application/x-ndjson`), oldest first, streamed.

```bash
curl -fsS -H "Authorization: Bearer $ADMIN_TOKEN" \
  "$IGNI/v1/portal/audit-events/export?limit=50000" > audit-0001.jsonl
# next batch: since=<occurred_at of the last line>
```

`limit` is at most 50,000 per batch. The export is itself recorded in the
audit trail. Events with no organisation (sign-ins, and deployment admins'
changes) are in the shared plane and are not returned by this endpoint. They
are in `shared-<db>.dump`.

### 4.3 Per-person exports

* `POST /v1/portal/subject-access` with `{"email": "..."}` returns one
  person's data as JSON, with purposes, categories, recipients, retention
  and source. Password hashes and verification tokens are withheld and named
  as withheld (`app/services/subject_access.py`).
* `POST /v1/portal/orgs/{org_id}/producers/person/{user_id}/exports` queues an
  export of a removed member's data while it waits for erasure, downloaded
  as `.tar.gz` from `GET /v1/portal/orgs/{org_id}/exports/{export_id}/download`.
  The archive is produced by the cloud node agent running
  `igni-cloud db export-producer` (`app/services/cloud/producer_exports.py`),
  so it depends on Fleet being deployed. It does not include the workspace
  volume.

Neither is a whole-organisation export. For that, use 4.1.

### 4.4 Documents (Artifacts)

The Artifacts client extension exports a document to a file: the rendered
content of the chosen version, its name, kind and version number, confirmed
derivations within the exported set, external citations, creation time and
author display name. It drops chunks, embeddings and edges to documents
outside the set (`igni__artifacts/extension/src/igni_artifacts/export.py`).
This is an export of chosen documents. There is no bulk export of every
document in an organisation. For a complete copy, dump the Artifacts
databases (4.1).

### 4.5 Models

The released igni 1.0 4B model is packaged as follows (from its model card):

| Item | Format |
|---|---|
| Deployable model | One standalone bf16 model: `config`, `model.safetensors` (about 8.41 GB), tokenizer, `chat_template.jinja`, `generation_config`. Base, SFT and DPO adapters merged in. No adapter needed at serve time |
| Adapters | LoRA adapters (weights and config) for each training stage, kept for reproducibility |
| Training data | JSONL |
| Evaluation set | JSON scenarios (`category_eval.json` for igni 1.0) |

igni 1.0 also comes in a 27B size, tuned the same way from a larger Qwen
base. It is offered in the same two forms, a whole merged model and its LoRA
adapters. No model card for it is in the repositories reviewed for this
runbook, so check its file list and checksums at hand-over the same way
([MODEL_PROVENANCE.md](MODEL_PROVENANCE.md)).

The file list contains no licence key and no expiry. igni serves models
through vLLM's OpenAI-compatible endpoint, registered as a node
(`POST /v1/nodes`, [gpu-clusters.md](gpu-clusters.md)). Nothing in the igni
server checks a licence before routing to a node. The same files can be
served by any software that reads safetensors.

Domain models built by the factory are expected to be delivered in the same
shape. No factory delivery code exists to confirm this (section 6, G9).
Before signing an acceptance, confirm the hand-over includes the merged
weights, the adapters, the dataset and the frozen evaluation set, with
checksums.

## 5. Timings and roles

| Step | Customer | Integrator | Ignite Ember | Timing |
|---|---|---|---|---|
| Plan the test and choose the environment | Owns | Advises | Supplies this runbook | Before each annual exit test |
| Restore a production backup into a test environment | Approves | Runs | None | Hours, depending on database size |
| Scenario A: grace and read-only, check the base | Witnesses, signs off | Runs | Issues the two exit-test licence files; answers questions (L3, business hours) | Half a day |
| Scenario B: egress test, build from source, restore drill | Witnesses, signs off | Runs | None needed | One to two days |
| Scenario B: escrow verification report | Requests from agent | Reviews build steps against the deposit's BUILD_VERIFY.md | Deposits per release; pays the agent for the first customers (proposed) | Per release, once an agreement exists. In the dry run an offline build took one to two and a half minutes per module, after a deposit of 0.05 to 1.1 GB |
| Scenario B: real escrow release | Proves the trigger to the agent | Builds and deploys modules | Can dispute the trigger | Weeks, sometimes longer (escrow terms). Cure period for a support failure: typically 30 to 90 days, set by the customer |
| Scenario C: export, verify, decommission | Owns; disposes of data | Runs | Nothing to return or delete | Days, depending on data volume |
| Lapsed licence, per the commercial terms | Renews or exits | Supports the exit | Issues a new licence file on renewal | 90 days running with warnings, then read-only |
| Ended evaluation | Orders, or decommissions (scenario C) | Runs the decommission | Issues a licence on order | 30 days read-only, then the paid modules stop |
| Support during the test | Raises requests | L1 and L2 | L3 response: 4 business hours if unusable, 2 business days otherwise (the EULA §5) | Business hours |
| Store evidence | Owns | Supplies logs and outputs | None | With the DORA register of information |

Ignite Ember has no access to the deployment at any point. It cannot run any
step for you, and it receives none of the evidence unless you send it.

## 6. Gaps

Each gap is a step the code does not support today. References are to files
that were checked for this runbook.

**G1. The licence check: built, without a release key yet.** Each paid
module verifies an offline licence file, Ed25519-signed by Ignite Ember,
against a public key built into the release, and decides for itself what it
may do: `igni__code-index/app/licensing.py`, `igni__artifacts/app/licensing.py`
and, for Fleet, the gateway in `igni__cloudvm/crates/gateway`. igni server
only shows the licence, installs an upload and records the daily audit
event; a module never asks it. The format, the states and the per-module
rules are in [licence-file.md](licence-file.md). What remains:

* Ignite Ember has not generated its release signing key, so the release
  key list is empty. Until it is, only a development copy accepts a licence
  (signed with the published test key), and scenario A can be rehearsed only
  there.
* The licence uses the deployment's own clock. Setting it back delays a
  lapse; the contract and the audit right are the control.
* Factory and Autopilot are in the licence and shown on the portal, and
  Autopilot without the Factory is refused, but neither has code that can be
  gated yet.
* The user band is reported, never enforced, as the commercial terms
  require: each module counts its people and warns when over the band.

**G2. The 90-day warning period: built.** For 30 days before expiry (7 for
an evaluation) and the 90 days after it, the portal shows a banner on every page, every answer from
a paid module carries an `igni-licence` header, each module's
`GET /v1/licence` reports its state, and the deployment's audit trail
records a `licence:daily` event once a day. Not yet: the CLI and desktop
clients do not show the header; they show what the modules answer.

**G3. Read-only: built, and defined per operation.** After the 90 days each
module reads, searches and exports and refuses changes, new indexing and
new agent runs, for good. It is never switched off. An ended evaluation is
read-only for 30 days and then `unlicensed`
(`app/services/licence/status.py`, `EVALUATION_READ_ONLY`, and its copies in
each module). Which operations are
which, per module, is written down in [licence-file.md](licence-file.md)
and pinned by each module's tests. Not yet: a Fleet node agent resumes its
own suspended VMs at their timed wake without asking the gateway, so those
are not refused in read-only (`igni__cloudvm/crates/node-agent`).

**G4. The EULA contradicts the commercial terms.** Its §4 says
that on expiry the customer must stop using and remove the Premium Features,
with no 90-day period. Its §0 lists the Premium Features as SAML, SCIM,
audit-log sinks, custom RBAC and air-gap update bundles, and lists CodeIndex
as Apache-2.0 core. The product catalogue says CodeIndex and Artifacts are
the paid modules, SCIM is free and SAML is not built. An auditor reading the
EULA, the catalogue and the escrow scope together will find different
answers to "what is proprietary". They need to agree before the escrow
deposit is defined.

The code and package metadata now agree with the catalogue:
`igni__artifacts/pyproject.toml` and `igni__code-index/pyproject.toml`
declare a proprietary licence, and `igni__code-index`, `igni__artifacts`
and `igni__cloudvm` each have a `LICENSE` file. Its text is a proposal
marked as pending legal review. The EULA itself is unchanged, because a
lawyer must review it. The changes proposed for that review:

* §0 and §1: the Premium Features are the paid modules (CodeIndex,
  Artifacts, Fleet, Autopilot) and the factory's service. SCIM, OIDC, roles
  and audit are in the Apache-2.0 base. Remove SAML until it exists.
* §4: on expiry a paid module keeps working, with warnings, for 90 days, then
  becomes read-only; read-only continues for the purpose of reading,
  exporting and leaving, with no further fee. Evaluation licences go
  read-only for 30 days at expiry, then stop. Models built by the factory are the customer's and are unaffected.
* State that the licence is enforced by an offline signed file, that
  nothing contacts Ignite Ember, and that the user band is settled at
  renewal, forward only, and never blocks anybody.

**G5. Backups do not cover the paid modules.** `containers/backup.sh` dumps
the igni server's shared plane and the organisations in its own `tenants`
table. CodeIndex and Artifacts have their own databases and registries, and
neither repository ships a backup script for a customer deployment (each has
only a local compose file). The Helm chart does not deploy Artifacts
(`charts/igni/values.yaml`, "Documents"). Whether `containers/backup.sh` works
pointed at a module's database has not been tested.

**G6. No whole-organisation export in an application format.** The complete
export is a PostgreSQL archive (4.1). That is an open format, but the
receiving system needs PostgreSQL to read it. The API offers the audit
trail (4.2), one person at a time (4.3), and chosen documents (4.4). There is
no endpoint that exports all of an organisation's conversations as files.
The removed-member export depends on Fleet's node agent and excludes the
workspace volume (`app/services/cloud/producer_exports.py`).

**G7. No escrow agreement or agent; deposit tooling built and dry-run.**
**No escrow agreement is signed.** No agent is chosen and nothing has been
deposited with anyone, so steps B6 and B7 cannot be executed. The proposal
also notes that release can take weeks and that Dutch insolvency law on
licence disaffirmation needs confirming with counsel.

What exists: Ignite Ember's deposit tooling, kept with its release
tooling rather than in this repository, because it reads the paid modules'
private repositories.

* The deposit is one per module per release tag: the source (`git archive`,
  refused if it holds a private key or an `.env`), the SDK source it is
  built against, lock files, every dependency, the toolchain as saved
  container images, a CycloneDX SBOM, `BUILD_VERIFY.md` with the exact
  offline build commands, `DEPOSIT.json` naming the commits, and a SHA-256
  `MANIFEST`. It is encrypted with age or GPG to the agent's key.
* The verification does what the agent's verifier would: unpacks, checks
  the manifest (and that a one-byte change is refused), loads the
  toolchain, builds with `docker build --network=none --no-cache`, and
  smoke-tests the result.

A dry run on 2026-10-29, from the untagged `HEAD` of each module, built
every module offline. It also found problems in the modules' lock files,
which the deposit tooling now refuses; showed that no repository has a
release tag yet; and showed that some components, such as the embedder
image and Fleet's virtual-machine images, cannot yet be built offline. Each
deposit's `BUILD_VERIFY.md` says what it does and does not cover.

**G8. Closed: the desktop client's update endpoint is configurable.**
`update_endpoint` points the desktop updater at a customer mirror, and
`air_gapped: true` stops it asking anything else (B8; DEC-11 in
[DECISIONS.md](DECISIONS.md)). What remains is the default: an unconfigured
install still asks github.com.

**G9. No factory delivery format in code.** Section 4.5 is based on the igni
1.0 4B model card. There is no code that packages a domain model for hand-over
with its dataset, evaluation set and checksums. The catalogue's ownership
term is a contract term until that exists.

**G10. Server images are signed in CD, but no signing key exists yet.**
CD signs every igni image with cosign and the release bundle with minisign
([OFFLINE_UPDATES.md](OFFLINE_UPDATES.md)), but the key ceremony has not
happened, so no signed image or bundle has been produced. Until it has, a
customer cannot verify a mirrored image against an Ignite Ember signature. A
customer that builds from source (B2) does not depend on it.

**G11. Fleet's exit is not rehearsed.** Fleet (`igni__cloudvm`) is on the
price list and enforces the same licence states at its gateway, but it
is not packaged for customers: there are no Helm or compose definitions for
the gateway or node agents ([NETWORK_DIAGRAM.md](NETWORK_DIAGRAM.md), Gaps,
item 4). Its escrow deposit builds (G7). This runbook does not yet test its
backup, export or decommission.
