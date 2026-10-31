# SOC 2 Type II, ISO 27001 and GDPR — where igni actually stands

**What this document is.** A self-assessment of the *technical* controls in
igni, mapped to what the three regimes ask for, with the evidence behind
each claim. It argues the *why* and is written to be read once;
[COMPLIANCE_TRACKER.md](COMPLIANCE_TRACKER.md) is the register kept
between audits — one row per control, with a status, an owner and a review
date — and is written to be updated. Statuses live there, not here. Every "verified" row points at something that was tested, not
read — the F-numbers refer to the pre-production review, indexed in
[REVIEW_FINDINGS.md](REVIEW_FINDINGS.md) with the tests that guard each
finding.

**Where igni stands.** igni is starting on the path to ISO 27001, SOC 2
Type I and Type II, and GDPR compliance. It holds no certificate or
attestation report yet.

**What it is not.** It is not an audit, and it does not say igni is
compliant with anything. That distinction is not a formality:

* **SOC 2 Type I** is a report by a licensed CPA firm on whether controls
  are suitably designed at one point in time. It is usually the first
  report, and it too needs an auditor, not a code change.
* **SOC 2 Type II** is a report by a licensed CPA firm on whether your
  controls *operated effectively over a period* — typically three to twelve
  months of collected evidence. There is no code change that produces one.
  Software can only make the controls achievable and the evidence
  collectable.
* **ISO 27001** certifies an **ISMS**, not a product: a risk assessment, a
  Statement of Applicability against Annex A, defined roles, internal
  audit, management review, then a certification audit by an accredited
  body. Most of Annex A is about people and process. Software touches
  perhaps a third of it.
* **GDPR** is law, not certification. Nobody issues a GDPR certificate.
  Compliance is a legal posture: lawful basis, records of processing,
  data-subject rights, DPAs with processors, breach notification inside 72
  hours, DPIAs where required.

So the useful question is not "are we compliant" but **"when an auditor
asks for evidence of control X, what do we hand them?"** That is what
follows.

---

## The architectural advantage, and the thing it depends on

igni is **self-hosted only**. The customer runs the server, the database
and the inference; their source code and their users' data never reach us.
Under GDPR that usually makes the **customer the controller** and us
**neither controller nor processor** for that data — which removes the
largest and most expensive part of a GDPR posture: no DPA to negotiate per
customer, no international-transfer mechanism, no processor obligations
under Article 28, and a breach on our side cannot expose their code.

**That advantage is entirely contingent on nothing phoning home**, which
makes the air-gap claim a compliance asset rather than a marketing one.
This review has treated it that way:

| Threat to the claim | Status |
|---|---|
| Application code contacting us | `self-hosting.md` §8: nothing in `app/` phones home, and the portal loads no analytics tag. The one it carried, gated on the vendor's hostname, belonged to the public site, which is its own repository now; `tests/test_nothing_phones_home.py` fails if a tag comes back |
| A plugin README exfiltrating via CSS | **Was possible** — `background-image: url(...)` survived the sanitizer. Fixed, F75 |
| An error-reporting SDK added later | Guarded by DP-5. `tests/test_nothing_phones_home.py` fails the build if a telemetry or error-reporting package enters a lock file, and `tests/test_igni_reaches_nothing_outside_the_network.py` boots the server and fails on any connection outside the network. Unguarded until gap 4 below was closed |
| The client's default-on calls: update checks, a plugin marketplace clone, the agent's web tools | None goes to an Ignite Ember host, and `air_gapped: true` turns all of them off. They are on by default, so an air-gapped install must set it. Each flow and its switch is in [NETWORK_DIAGRAM.md](NETWORK_DIAGRAM.md), section 3 |

---

## SOC 2 — the Trust Services Criteria software can speak to

Only the criteria a product can evidence are listed. CC1 (control
environment), CC2 (communication), CC3–CC5 (risk assessment and monitoring
activities) are organisational and are not addressed here at all.

| Criterion | What it asks | Evidence | Verdict |
|---|---|---|---|
| CC6.1 — logical access | Access is restricted to authorised users | RS256 JWTs signed with a server-generated key kept encrypted under `SECRET_ENCRYPTION_KEY`, which startup refuses to run without; 30 parametrised portal endpoints swept for cross-organisation access, 27 refused and the 3 that answer are each verified for *why* (§38) | **Verified** |
| CC6.1 — privileged access | Privileged actions are restricted | `require_admin` on admin routes; a dev bypass that left a live admin account now refuses startup (F87) | **Verified** |
| CC6.2 — registration | Access is granted on authorisation | Invite-first membership; a sign-in code could once claim somebody else's pending admin invitation (F29), fixed | **Verified** |
| CC6.3 — least privilege | Access is removed when no longer needed | Roles and membership status; removal takes effect on the next request (AC-11); a periodic access review an auditor can sample, `GET /v1/portal/access-review` (AC-8) | **Verified** |
| CC6.6 — boundary protection | External access is protected | API binds loopback by default and expects a reverse proxy (F52); nothing but the API and the portal is published, asserted over whatever the compose files and the chart declare rather than per service (F22, generalised when DEC-13 removed the one service the original rule named); request body limits (F56) | **Verified — TLS is the operator's** |
| CC6.7 — data in transit | Transmission is protected | Provider calls are HTTPS. **The deployment's own TLS is the operator's reverse proxy; nothing in the product enforces or checks it** | **Operator's** |
| CC6.7 — data at rest | Storage is protected | Secrets the deployment stores are Fernet-encrypted, authenticated ciphertext, a missing key refuses the write rather than storing plaintext (F84). **Database and volume encryption is the operator's** | **Partly verified** |
| CC7.1 — vulnerability management | Vulnerabilities are identified | `pip-audit` + `bandit` + `npm audit --omit=dev` gate every pull request (F42); **image scanning added in this pass** (F89) | **Verified** |
| CC7.2 — monitoring | Anomalies are detected | `/health` and `/ready` with documented semantics (F19); structured logs; `/metrics` behind a token with shipped alert rules (`charts/igni/files/alerts.yaml`); the audit trail exportable as JSON Lines for a SIEM (LG-7) | **Verified** |
| CC7.2 — audit trail | Activity is logged for review | `audit_events`: actor, action, target, outcome, source, timestamp for every mutation on an authenticated surface plus every authentication attempt. Append-only enforced by a database trigger. Readable by an admin at `GET /v1/portal/audit-events`. Coverage is structural — a middleware rule, asserted against the live router (F90) | **Verified** |
| CC7.4 / CC7.5 — incident response | Incidents are handled and recovered | Backup **restore verified end to end** (§39): schema, row counts, encrypted secrets byte-for-byte, sequences, constraints, and the app boots against the restored database. A written incident response plan, generated so every step names something that exists ([INCIDENT_RESPONSE.md](INCIDENT_RESPONSE.md)). LG-8 stays partial: on-call, severities and contacts are the organisation's to fill in | **Partly verified** |
| CC8.1 — change management | Changes are authorised, tested, approved | CI gates lint, types, security audit, the server test suite, the portal suite; CD now **cannot build an untested commit** (F74); migrations round-trip tested against populated data | **Verified** |
| A1.2 — availability | Recovery is supported | Restore drill (§39); health checks distinguish liveness from readiness so a database blip does not restart-loop the fleet | **Verified** |
| C1.1 / C1.2 — confidentiality | Confidential data is protected and disposed of | Encryption above. Disposal is built: a retention runner applies `AUDIT_RETENTION_DAYS` rather than merely offering the setting (DP-6), and a named person can be erased on request without destroying other people's history (DP-7) | **Verified** |

---

## ISO 27001 Annex A — the controls with a technical component

| Control | Evidence | Verdict |
|---|---|---|
| A 5.15 Access control | §38 sweep, F19, F29 | Verified |
| A 5.16 Identity management | Invite-first, OAuth + OTP; F87 removed a standing identity nobody created deliberately | Verified |
| A 5.17 Authentication information | Bcrypt with a malformed-hash guard, timing equalised for unknown accounts (F44); OTP attempt-limited and cryptographically generated (F28) | Verified |
| A 5.33 Protection of records | Fernet at rest (F84); key rotation with both keys accepted during the change (DP-2) | Verified |
| A 8.8 Vulnerability management | F42, F89 | Verified |
| A 8.9 Configuration management | Startup checks refuse unsafe configurations; compose files require explicit image and env file (F51, F52) | Verified |
| A 8.12 Data leakage prevention | F75, F83 | Verified |
| A 8.13 Backup | §39 restore drill | Verified |
| A 8.15 Logging | `audit_events`, append-only, admin-readable (F90) | Verified |
| A 8.16 Monitoring | `/metrics`, shipped alert rules, audit export for a SIEM (LG-7) | Verified |
| A 8.24 Cryptography | Fernet; RS256 tokens signed with a key the server generates and stores encrypted, algorithms pinned; no home-grown crypto | Verified |
| A 8.25–8.29 Secure development | CI gates, the test suites of each repository, adversarial review indexed in [REVIEW_FINDINGS.md](REVIEW_FINDINGS.md); the secure development policy in [POLICIES.md](POLICIES.md) | Verified |
| A 8.31 Separation of environments | `APP_ENV` gates six checks; F87 closed the one path by which a development artefact reached production | Verified |

---

## GDPR — the articles a product can affect

| Article | Requirement | Status |
|---|---|---|
| 5(1)(c) minimisation | Collect no more than needed | What is held is accounts and memberships, the audit trail's actor and source address, and what each organisation's own database holds — usage, notifications, group membership. Every personal column is classified against the live schema (F115), so the inventory cannot drift from what is stored |
| 5(1)(e) **storage limitation** | Keep no longer than necessary | `AUDIT_RETENTION_DAYS`, with `app/services/retention.py` doing the pruning. It defaults to 0 — keep everything — deliberately: discarding a customer's history by surprise would be worse than the gap it closes, so switching it on is the operator's policy decision (DP-6) |
| 15 right of access | Provide a subject's data | `POST /v1/portal/subject-access` returns everything held about an address, from a column classification that is asserted against the live schema rather than maintained by hand — every personal column is marked included, withheld or dead, and a new column fails the test until it is classified (F115). It also asks each approved service for its part (DP-8) |
| 17 **right to erasure** | Delete on request | Deleting a `users` row works and provenance degrades to NULL rather than cascading (F60, F85), and `POST /v1/portal/erasure` tells every approved service — igni__artifacts among them — to erase or redact the person, with a token igni signs for that service; it answers 502 naming any service that did not confirm, and lists the ones that did (`services_told`). This replaced a single call to igni__artifacts over a shared secret (see `docs/service-identity.md`) |
| 25 data protection by design | Technical measures by default | Email off by default, loopback binds, encryption at rest, air-gap-first defaults — genuinely strong here |
| 30 records of processing | Maintain a record | Customer's obligation as controller. [RECORDS_OF_PROCESSING.md](RECORDS_OF_PROCESSING.md) is the inventory for it: purposes, categories of data subject, personal data column by column and retention, generated from the code. The legal basis and the controller's details are the customer's to fill in (DP-9) |
| 32 security of processing | Appropriate measures | The SOC 2 table above is the evidence |
| 33 breach notification | Notify within 72 hours | Customer's obligation, and now answerable: `audit_events` records who did what, to what, from where. Scoping a breach was impossible before F90 |
| 44+ transfers | Mechanism for transfers | Not applicable: no data reaches us |

---

## The gaps, in the order they should be closed

**1. ~~There is no audit trail.~~ Built — see F90.**

`audit_events` records actor, action, target, outcome, source and
timestamp for every mutation on an authenticated surface and every
authentication attempt, including the failures. `UPDATE` is rejected by a
database trigger rather than by convention, an administrator can read it
at `GET /v1/portal/audit-events`, and `AUDIT_RETENTION_DAYS` bounds how
long it is kept.

The design decision worth knowing for an audit conversation: **coverage is
structural, not a list of call sites.** A middleware records every
qualifying request whether or not the handler knows it exists, and a test
asserts that property against the live router — so "is endpoint X audited"
is answered by the rule rather than by someone's memory. Two findings in
this review, F80 and F88, are records of what happens to the alternative.

Two limits stated rather than glossed:

* **Reads are not recorded** on the portal. That is a judgement about
  volume against value — many reads per page view, and "who looked at the
  team list" is not what an auditor asks. Reads that do matter — a
  registered service resolving people (`POST /v1/directory/people/resolve`)
  or writing to their feeds (`POST /v1/service-notifications`) — are
  `POST`s and are captured. *Superseded:* this used to read "`POST` under
  `/v1/internal`"; those routes and the shared `ARTIFACTS_SERVICE_TOKEN`
  that guarded them are gone, replaced by the registration, approval and
  per-organisation tokens in `docs/service-identity.md`. The directory's
  `GET`s are not recorded, by the same rule as the portal's. If an auditor
  asks for read auditing, the rule is one line.
* **The recorder fails open.** A write failure logs `AUDIT WRITE FAILED`
  with the lost event and does not refuse the request. The alternative
  turns a database hiccup into an outage on every privileged endpoint at
  once. It is detectable — alert on that string — but a deployment that
  needs fail-closed should say so.

**2. ~~Nothing else has a retention period.~~ Built — DP-6.**

`AUDIT_RETENTION_DAYS` bounds the audit trail through
`app/services/retention.py`, and `POST /v1/portal/erasure` handles a
single person. The window defaults to 0 — keep everything — so no
deployment loses history by upgrading.

**3. ~~No SBOM.~~ Built — CM-4.**

`cd.yml` generates a CycloneDX SBOM for the shipped image, not from the
lock file — the lock file is half of what ships. Both marketplaces
increasingly expect one and it is the artefact auditors ask for, where
`pip-audit`, `npm audit` and Trivy only cover detection.

**4. ~~No test defends the air-gap claim.~~ Built — DP-5.**

`tests/test_nothing_phones_home.py` is the rule `self-hosting.md` §8
asked for: every host named in either repository has to be on an
allow-list with a reason, no telemetry package may appear in a lock
file, and the check fails on its own staleness when an allow-listed
host stops being referenced. It has since caught two real outbound
requests — the CLI's PyPI check (F94) and the desktop updater's launch
call to github.com (F126).

**5. ~~Access review~~ and ~~monitoring~~.** The access review is built
— `GET /v1/portal/access-review` (AC-8), with the dormancy signal that
needed `users.last_sign_in_at` because nothing recorded a sign-in at all
(F92). Monitoring is built too, and this paragraph went on calling it
unaddressed after the fact: LG-7 covers `/metrics` behind a bearer
token, nine metrics, seven shipped alert rules and a Grafana dashboard
(F101, F116). The audit trail was the prerequisite, and it arrived
first, which is why the ordering here put monitoring last.

---

## What to do with this document

Take it to whoever runs the audit. It answers "what evidence exists" for
the technical criteria and is honest about what is still open — which,
after the B-series passes, is the policy documents nobody can generate (`Process` rows: LG-8's incident
response plan among them), and the decisions that are a customer's to
make (`Operator` rows). Token revocation, the record of processing and
the CLI's default endpoint were on this list until B5, B4 and DEC-14
closed them; the register has the evidence for each.

Four of the five gaps this document originally listed have been built,
and for five passes it went on claiming otherwise: it said that nothing
pruned, that there was no export path, no erasure mechanism, no SBOM and
no air-gap test — all five of which existed. (Past tense on purpose: a
rule below reads present-tense absence claims as claims about now.) The register beside it was correct
throughout, because the register is asserted by a test and this
document was not. It is now: every control id cited here has to exist
in the register, and a gap listed here may not name a control the
register calls `Verified`. F139. The organisational half — policies, risk register,
training, vendor management, an ISMS — is not here and is not something
this codebase can produce.

One practical note on sequencing, now partly moot: the audit trail was a
prerequisite for several controls in all three frameworks, and
retrofitting it after a Type II observation window had started would have
restarted the window. It exists now, which means an observation window can
begin whenever the organisational half is ready.
