"""Generate `docs/STATEMENT_OF_APPLICABILITY.md` from the register.

C1, OR-1. ISO/IEC 27001:2022 asks for a Statement of Applicability: all
93 Annex A controls, each with a decision about whether it applies, a
justification, and — where it applies — what implements it.

**Generated, for B4's reason.** A hand-written SoA is a list of 93 rows
citing evidence that lives somewhere else, which is precisely the
document that goes stale on the next change. F139 recorded what that
costs: `COMPLIANCE.md` spent five passes telling customers that
retention, subject access, erasure, an SBOM and the air-gap test did not
exist, all five of which had been built. So the evidence here is not
retyped: a control marked `product` names rows in
`docs/COMPLIANCE_TRACKER.md`, and the generator refuses to render if a
row it names is missing or is not `Verified`.

## What this document is the SoA *for*

Two things get conflated in a self-hosted product's compliance story,
and conflating them is how an SoA becomes untrue:

* **the software** this repository builds and ships, as a customer runs
  it in their own infrastructure;
* **the vendor's own operations** — offices, laptops, employment,
  training, supplier contracts.

Every disposition below answers for the *software*. The vendor's own
ISMS needs its own scope statement and its own SoA, and no repository
can contain them: nothing here knows which legal entity, which
locations, or which staff. Those are marked `organisation` and carry a
placeholder a person has to replace.

That is the same discipline as the Article 30 record: state what the
code does, and refuse to invent the parts only a controller can supply.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
import pathlib
import re
import sys

_ROOT = pathlib.Path(__file__).resolve().parent.parent
_TRACKER = _ROOT / 'docs/COMPLIANCE_TRACKER.md'
_OUTPUT = _ROOT / 'docs/STATEMENT_OF_APPLICABILITY.md'

#: What only the organisation adopting this can state. The same marker
#: the Article 30 record uses, for the same reason: a generated document
#: that filled these in would read as a decision somebody had made.
PLACEHOLDER = '`__ORGANISATION_TO_COMPLETE__`'

#: The five dispositions, and what each one obliges.
#:
#: ``product``    the software implements this. Must cite register rows,
#:                and every row cited must exist and be `Verified`.
#: ``shared``     the software does a defined part and the organisation
#:                must do the rest. Cites rows *and* carries the
#:                placeholder — both halves, because either alone is a
#:                claim that the other half does not exist.
#: ``operator``   a decision in the customer's deployment: their
#:                cluster, their network, their facility. May cite
#:                `Operator` rows.
#: ``organisation`` the vendor's own ISMS. Placeholder; no code can do it.
#: ``excluded``   does not apply, with the reason. Never used to mean
#:                "not done".
DISPOSITIONS = ('product', 'shared', 'operator', 'organisation', 'excluded')


@dataclass(frozen=True)
class Control:
    ref: str
    title: str
    disposition: str
    justification: str
    rows: tuple[str, ...] = field(default_factory=tuple)


def _c(ref: str, title: str, disposition: str, justification: str, *rows: str) -> Control:
    return Control(ref, title, disposition, justification, tuple(rows))


# ── A.5 Organizational controls (37) ─────────────────────────────────
ORGANIZATIONAL: tuple[Control, ...] = (
    _c(
        '5.1',
        'Policies for information security',
        'organisation',
        "OR-2. Policy documents are the organisation's to write and sign. What the software "
        'contributes is that several policies would otherwise be unenforceable claims: the '
        'access-control policy is asserted by AC-1 to AC-11, and the cryptography policy by '
        'DP-1 and DP-2.',
    ),
    _c(
        '5.2',
        'Information security roles and responsibilities',
        'organisation',
        'Named people. The register carries an owner per control, which is the input, not the assignment.',
    ),
    _c(
        '5.3',
        'Segregation of duties',
        'organisation',
        'The mechanism exists — AC-3 gates every privileged action on an admin role and AC-2 '
        'keeps organisations apart — but deciding which person holds which role is the '
        "organisation's.",
    ),
    _c('5.4', 'Management responsibilities', 'organisation', "Management's own commitment."),
    _c(
        '5.5',
        'Contact with authorities',
        'organisation',
        'Who notifies which regulator, and within what window. GDPR Art. 33 is 72 hours; the '
        'audit trail (LG-1) is what makes a notification factual rather than a guess.',
    ),
    _c('5.6', 'Contact with special interest groups', 'organisation', 'Memberships and mailing lists.'),
    _c(
        '5.7',
        'Threat intelligence',
        'shared',
        'The software ingests the part that can be automated: dependency advisories on every '
        'build (CM-2) and image CVEs (CM-3), both gating. Human threat intelligence — sector '
        "advisories, an attacker who is interested in this customer — is the organisation's.",
        'CM-2',
        'CM-3',
    ),
    _c(
        '5.8',
        'Information security in project management',
        'organisation',
        'How work is planned. `docs/COMPLIANCE_TRACKER.md` and `docs/REVIEW_FINDINGS.md` are evidence '
        'that security work is tracked, not a policy that it must be.',
    ),
    _c(
        '5.9',
        'Inventory of information and other associated assets',
        'shared',
        'For the data this software holds the inventory is complete and asserted: DP-9 covers '
        'all 106 classified personal-data columns across 20 tables plus the one JSON payload, '
        'generated from the live schema so a migration cannot outdate it. Assets beyond that '
        "data — hosts, laptops, accounts — are the organisation's.",
        'DP-9',
    ),
    _c(
        '5.10',
        'Acceptable use of information and other associated assets',
        'organisation',
        'A rule about people, not a mechanism.',
    ),
    _c(
        '5.11',
        'Return of assets',
        'organisation',
        "Offboarding. The software's half is that access can actually be withdrawn — AC-11, "
        'three levers including revocation of the credential itself.',
    ),
    _c(
        '5.12',
        'Classification of information',
        'product',
        'Every column of every personal-data table is classified, and the classification is a '
        'rule rather than a list: F115 asserts it against the live schema, so a new column '
        'fails the build until somebody decides what it is.',
        'DP-9',
        'DP-8',
    ),
    _c(
        '5.13',
        'Labelling of information',
        'excluded',
        'The software stores structured records in named columns, not documents that could carry '
        'a classification label. There is no artefact here to label. Labelling of the '
        "organisation's own documents belongs to its own SoA.",
    ),
    _c(
        '5.14',
        'Information transfer',
        'shared',
        "The server does not transfer information out of the customer's network — DP-5, "
        'asserted by a source scan and a runtime test in each repository after two outbound '
        "calls were found. The client's update checks and web tools reach the internet by "
        'default and are all off under `air_gapped: true`, which the organisation decides. In-transit '
        'protection of the traffic it does carry is DP-4, which is `Operator`: the product ships '
        "a TLS terminator but the DNS record and the certificate's trust chain are the "
        "customer's. Transfers the organisation chooses to make from its own systems are its own.",
        'DP-5',
    ),
    _c(
        '5.15',
        'Access control',
        'product',
        'Authentication on every route, organisation scoping swept from the live router, and '
        'admin-only gating on privileged actions. Account existence is not disclosed to an '
        'anonymous caller either (AC-10), which is the part of access control that leaks before '
        'anyone is authenticated.',
        'AC-1',
        'AC-2',
        'AC-3',
        'AC-7',
        'AC-10',
    ),
    _c(
        '5.16',
        'Identity management',
        'product',
        'No standing or default accounts (AC-4), and identities either local or from the '
        "customer's IdP over OIDC (AC-9).",
        'AC-4',
        'AC-9',
    ),
    _c(
        '5.17',
        'Authentication information',
        'product',
        'Passwords bcrypt-hashed, one-time codes rate-limited and constant-time compared, and no credential in a URL.',
        'AC-5',
        'AC-6',
        'AC-1',
    ),
    _c(
        '5.18',
        'Access rights',
        'product',
        'Granted on authorisation (AC-7), reviewed periodically (AC-8), and revocable three ways '
        '— deactivation, credential withdrawal, deletion (AC-11).',
        'AC-7',
        'AC-8',
        'AC-11',
    ),
    _c(
        '5.19',
        'Information security in supplier relationships',
        'organisation',
        "OR-4. The product's own supplier surface is unusually small and is stated: no "
        'sub-processors, because nothing leaves the deployment (DP-5).',
    ),
    _c(
        '5.20',
        'Addressing information security within supplier agreements',
        'organisation',
        "Security clauses in the organisation's own supplier contracts. OR-4. Worth noting what "
        'is *not* needed here: no Article 28 processor agreement with this vendor, because no '
        'data reaches it (DP-5) and there is therefore no processing to contract for.',
    ),
    _c(
        '5.21',
        'Managing information security in the ICT supply chain',
        'shared',
        "The software's supply chain is asserted: an SBOM per release (CM-8), dependency "
        'auditing (CM-2), image scanning (CM-3), and a declared licence (CM-11). Suppliers the '
        'organisation contracts with are its own.',
        'CM-8',
        'CM-2',
        'CM-3',
        'CM-11',
    ),
    _c(
        '5.22',
        'Monitoring, review and change management of supplier services',
        'organisation',
        "Review of the organisation's suppliers. OR-4.",
    ),
    _c(
        '5.23',
        'Information security for use of cloud services',
        'operator',
        'The customer chooses the cloud and configures it. What the software guarantees is that '
        'the choice stays theirs: nothing phones home, and the marketplace deployments run '
        'entirely in their project (DP-5).',
    ),
    _c(
        '5.24',
        'Information security incident management planning and preparation',
        'organisation',
        'LG-8, and the largest remaining gap in the register. The prerequisite is built — an '
        'immutable audit trail (LG-1, LG-2), a reviewable and exportable one (LG-4), and alerting '
        '(LG-7) — but a plan naming people and steps is not something code produces. C4.',
    ),
    _c(
        '5.25',
        'Assessment and decision on information security events',
        'shared',
        'The software decides which events are worth recording and refuses to lose them: every '
        'privileged mutation, every authentication attempt including failures, and a write '
        'failure that is itself counted and alerted. Triage of what those events mean is the '
        "organisation's.",
        'LG-1',
        'LG-3',
        'LG-6',
    ),
    _c(
        '5.26',
        'Response to information security incidents',
        'shared',
        "The levers a response needs exist and are demonstrated: withdraw one person's sessions, "
        'deactivate a membership, rotate the encryption key with both keys live, take a node out '
        'of rotation. Who pulls them, and when, is the plan in C4.',
        'AC-11',
        'DP-2',
    ),
    _c(
        '5.27',
        'Learning from information security incidents',
        'organisation',
        'The pre-production review (`docs/REVIEW_FINDINGS.md`) is exactly this for defects rather than '
        'incidents — each one what was found, what it cost, and the rule that keeps it fixed. The '
        'practice for real incidents needs the plan first.',
    ),
    _c(
        '5.28',
        'Collection of evidence',
        'product',
        'The audit trail is append-only in the database rather than by convention: `UPDATE` is '
        'refused by a trigger, demonstrated, and the refusal survives a restore — re-tested by '
        'the drill in F5. Exportable as JSON Lines for a SIEM.',
        'LG-2',
        'LG-4',
    ),
    _c(
        '5.29',
        'Information security during disruption',
        'shared',
        'The software degrades rather than cascading (AV-1) and its backups are restorable, '
        'proven by a drill that runs in CI using the shipped script (DP-10, DP-11). A continuity '
        'plan for the organisation is separate.',
        'AV-1',
        'DP-10',
        'DP-11',
    ),
    _c(
        '5.30',
        'ICT readiness for business continuity',
        'shared',
        'Recovery of the software is exercised: dump, restore, compare, and boot the application '
        'on the restored database. The recovery-time objective and who declares a disaster are '
        "the organisation's.",
        'DP-10',
        'DP-11',
    ),
    _c(
        '5.31',
        'Legal, statutory, regulatory and contractual requirements',
        'organisation',
        'Which regimes apply to this organisation. The software supplies what GDPR asks of it: '
        'records of processing, subject access, erasure and retention.',
    ),
    _c(
        '5.32',
        'Intellectual property rights',
        'product',
        'The software declares its own licence where tooling reads it — both images said '
        '`NOASSERTION`, the SPDX string for "nobody said", and there was no LICENSE file at all.',
        'CM-11',
    ),
    _c(
        '5.33',
        'Protection of records',
        'product',
        'Audit records cannot be altered (LG-2) and are retained by a runner that applies the '
        'configured window rather than merely offering the setting (DP-6). Provider credentials '
        'among those records are encrypted at rest with a rotatable key (DP-1, DP-2).',
        'LG-2',
        'DP-6',
        'DP-1',
        'DP-2',
    ),
    _c(
        '5.34',
        'Privacy and protection of PII',
        'product',
        'Records of processing generated from the schema (DP-9), subject access covering every '
        'classified column (DP-8), erasure that redacts a named person without destroying other '
        "contributors' history (DP-7), retention applied (DP-6), and a feature removed outright "
        'because it collected data the deployment could not use (DP-13).',
        'DP-9',
        'DP-8',
        'DP-7',
        'DP-6',
        'DP-13',
    ),
    _c(
        '5.35',
        'Independent review of information security',
        'organisation',
        'OR-5, OR-7. An auditor, and an observation window.',
    ),
    _c(
        '5.36',
        'Compliance with policies, rules and standards for information security',
        'shared',
        'Unusually strong on the software side, and worth stating precisely: the register itself '
        'is asserted by tests — every finding it cites exists, every status is from the '
        'vocabulary, the summary counts match, and no absence claim may name a control the '
        "register has marked `Verified`. Compliance of people with policy is the organisation's.",
        'CM-1',
    ),
    _c(
        '5.37',
        'Documented operating procedures',
        'shared',
        "`docs/self-hosting.md`, `docs/gpu-clusters.md`, the chart's README and the backup "
        "script's own header document how to run it, and `make check-configs` plus the kind "
        "install assert that the documented procedures work. The organisation's internal "
        'procedures are its own.',
        'CM-10',
    ),
)

# ── A.6 People controls (8) ──────────────────────────────────────────
PEOPLE: tuple[Control, ...] = (
    _c('6.1', 'Screening', 'organisation', 'Employment. Nothing in a repository can do this.'),
    _c(
        '6.2',
        'Terms and conditions of employment',
        'organisation',
        'Employment contracts stating security responsibilities. Nothing in a repository can '
        'create an obligation on a person.',
    ),
    _c(
        '6.3',
        'Information security awareness, education and training',
        'organisation',
        "OR-3, and the register's only People row. Training is delivered to people by people; "
        'the closest the software comes is refusing unsafe configuration at startup rather than '
        'relying on somebody having been taught not to set it.',
    ),
    _c(
        '6.4',
        'Disciplinary process',
        'organisation',
        "A process for responding to a person's breach of policy. The software's contribution "
        'is evidence rather than process: an append-only audit trail that says who did what '
        '(LG-1, LG-2).',
    ),
    _c(
        '6.5',
        'Responsibilities after termination or change of employment',
        'shared',
        'The technical half is built and was the subject of three findings in this review: '
        'deactivation takes effect on the next request, a credential can be withdrawn outright, '
        'and deletion ends every session it issued. The process around offboarding is the '
        "organisation's.",
        'AC-11',
    ),
    _c(
        '6.6',
        'Confidentiality or non-disclosure agreements',
        'organisation',
        'Agreements with staff and third parties. The product reduces how much is disclosed in '
        'the first place — nothing leaves the deployment, so no vendor NDA covers customer data '
        'here (DP-5).',
    ),
    _c('6.7', 'Remote working', 'organisation', 'A policy about people and their devices.'),
    _c(
        '6.8',
        'Information security event reporting',
        'shared',
        'The software reports events it can see — nine metrics, eight alert rules shipped with '
        'the chart, and a red healthcheck when a backup goes stale. A person reporting something '
        "they noticed needs a channel, which is the organisation's.",
        'LG-7',
        'DP-11',
    ),
)

# ── A.7 Physical controls (14) ───────────────────────────────────────
#
# Every one of these is about a place. The software runs in the
# customer's infrastructure, so for this SoA's scope they are the
# customer's — and for the vendor's own ISMS they are the vendor's, in a
# document this one deliberately is not.
_PHYSICAL_NOTE = (
    "A property of the place the software runs, which is the customer's data centre or cloud "
    'region. The software cannot affect it and does not depend on assumptions about it beyond '
    'those DP-3 and DP-4 state.'
)
PHYSICAL: tuple[Control, ...] = (
    _c('7.1', 'Physical security perimeters', 'operator', _PHYSICAL_NOTE),
    _c('7.2', 'Physical entry', 'operator', _PHYSICAL_NOTE),
    _c('7.3', 'Securing offices, rooms and facilities', 'operator', _PHYSICAL_NOTE),
    _c('7.4', 'Physical security monitoring', 'operator', _PHYSICAL_NOTE),
    _c('7.5', 'Protecting against physical and environmental threats', 'operator', _PHYSICAL_NOTE),
    _c('7.6', 'Working in secure areas', 'operator', _PHYSICAL_NOTE),
    _c(
        '7.7',
        'Clear desk and clear screen',
        'organisation',
        "A rule about people at desks. The software's adjacent contribution is that a session "
        'can be withdrawn from a device left unattended (AC-11).',
    ),
    _c('7.8', 'Equipment siting and protection', 'operator', _PHYSICAL_NOTE),
    _c('7.9', 'Security of assets off-premises', 'organisation', "The organisation's own equipment."),
    _c(
        '7.10',
        'Storage media',
        'operator',
        'The disks the database and the backups sit on. DP-3 and DP-12 state what the software '
        'expects of them — encryption at rest, and backups that leave the host — and both are '
        'marked `Operator` in the register for exactly this reason.',
    ),
    _c('7.11', 'Supporting utilities', 'operator', _PHYSICAL_NOTE),
    _c('7.12', 'Cabling security', 'operator', _PHYSICAL_NOTE),
    _c('7.13', 'Equipment maintenance', 'operator', _PHYSICAL_NOTE),
    _c(
        '7.14',
        'Secure disposal or re-use of equipment',
        'operator',
        "Disposal of the customer's hardware. What the software guarantees is that data can be "
        'removed from it on request — DP-7 erasure, and DP-6 retention that actually prunes.',
    ),
)

# ── A.8 Technological controls (34) ──────────────────────────────────
TECHNOLOGICAL: tuple[Control, ...] = (
    _c(
        '8.1',
        'User endpoint devices',
        'organisation',
        'Developer laptops. The one thing the software controls here is what it puts on them, and '
        "that is asserted: the desktop app's webview is confined by a CSP that permits no remote "
        'origin in any directive, after a first attempt allowed `connect-src https:`.',
    ),
    _c(
        '8.2',
        'Privileged access rights',
        'product',
        'Every privileged action is admin-gated, and the sweep is generated from the live router '
        'rather than from a list of endpoints.',
        'AC-3',
        'AC-7',
    ),
    _c(
        '8.3',
        'Information access restriction',
        'product',
        'Organisation scoping on every parametrised portal endpoint, swept; and B7 closed the one '
        'authorisation path that decided on the git provider alone without asking whether the '
        'person was still a member.',
        'AC-2',
        'AC-7',
    ),
    _c(
        '8.4',
        'Access to source code',
        'shared',
        "The software's own handling of a customer's source is the part it can answer for: "
        'source passes through to the model endpoint the customer chose and is not stored, and '
        "credentials never appear in a response. Repository access is CodeIndex's; access to "
        "*this* repository is the organisation's.",
        'AC-5',
        'DP-5',
    ),
    _c(
        '8.5',
        'Secure authentication',
        'product',
        'Bcrypt passwords, rate-limited one-time codes, OIDC with PKCE and pinned algorithms, and '
        'no token in a URL — the last of which was found twice, in two repositories.',
        'AC-1',
        'AC-5',
        'AC-6',
        'AC-9',
    ),
    _c(
        '8.6',
        'Capacity management',
        'product',
        'The connection pool is sized against the server and says so at startup, exhaustion is '
        'counted and alerted rather than surfacing as unexplained 500s, and a burst degrades '
        'instead of cascading.',
        'AV-1',
        'LG-7',
    ),
    _c(
        '8.7',
        'Protection against malware',
        'shared',
        "The software's own artefacts are scanned every build and the build fails on a HIGH or "
        "CRITICAL finding (CM-3). Endpoint anti-malware on the organisation's machines is its "
        'own.',
        'CM-3',
    ),
    _c(
        '8.8',
        'Management of technical vulnerabilities',
        'shared',
        'Detection is automated and gating: dependency auditing, image scanning, and an SBOM so a '
        'customer can answer "what is in this" without asking. How fast a finding must be fixed '
        'is a commitment, not a mechanism — CM-9, and C6.',
        'CM-2',
        'CM-3',
        'CM-8',
    ),
    _c(
        '8.9',
        'Configuration management',
        'product',
        'Unsafe configuration is refused at startup rather than warned about, the chart refuses '
        'ten inputs rather than defaulting them, and the deployment artefact is now installed on '
        'a real cluster in CI — which found that it never could have been. Every container runs '
        'as a non-root user with a read-only root filesystem and dropped capabilities (CM-4).',
        'CM-5',
        'CM-10',
        'CM-6',
        'CM-4',
    ),
    _c(
        '8.10',
        'Information deletion',
        'product',
        'Retention applied by a runner (DP-6), erasure that redacts a named person from stored '
        "payloads without destroying other contributors' history (DP-7), and account deletion "
        'that ends every session the account issued (AC-11).',
        'DP-6',
        'DP-7',
        'AC-11',
    ),
    _c(
        '8.11',
        'Data masking',
        'product',
        'Logs carry no personal data they do not need — enforced by an AST rule over all of '
        '`app/`, after the chat endpoint was found logging the prompt — and error responses '
        'disclose nothing about the system.',
        'LG-5',
        'LG-9',
    ),
    _c(
        '8.12',
        'Data leakage prevention',
        'product',
        'The strongest claim in this document, and the most tested: nothing leaves the '
        'deployment for the vendor. Two outbound calls were found and put behind a switch, a '
        'source scan and a runtime test in each repository defend it, and the desktop webview '
        'is confined by a CSP that permits no remote origin. '
        'Error responses disclose nothing either (LG-9), after an anonymous sign-up received 484 '
        'characters of database error naming every column of `users`.',
        'DP-5',
        'LG-5',
        'LG-9',
    ),
    _c(
        '8.13',
        'Information backup',
        'product',
        'Backups are taken by a shipped script that verifies each archive before counting it and '
        'prunes only after a success, and they are restorable — a drill that runs in CI with that '
        'same script, comparing schema, rows, encrypted credentials byte for byte, sequences, and '
        'the append-only trigger.',
        'DP-10',
        'DP-11',
    ),
    _c(
        '8.14',
        'Redundancy of information processing facilities',
        'operator',
        "Replica counts, multi-zone scheduling and a standby database are the customer's "
        'topology. The software is stateless per process and horizontally scalable, and the chart '
        'exposes the replica counts.',
    ),
    _c(
        '8.15',
        'Logging',
        'product',
        'One audit event per privileged request, by a middleware rule over the live router rather '
        'than a call in each handler; authentication attempts including failures; and a write '
        'that fails is counted and alerted rather than lost quietly. The records cannot be '
        'altered once written (LG-2), enforced by a database trigger rather than by convention.',
        'LG-1',
        'LG-3',
        'LG-6',
        'LG-2',
    ),
    _c(
        '8.16',
        'Monitoring activities',
        'product',
        'Ten metrics and eight alert rules shipped with the chart, every alerted metric asserted '
        'to exist and every metric asserted to have a rule, and labels allowlisted so none can '
        'carry personal data. The customer runs the monitoring; nothing phones home.',
        'LG-7',
        'LG-4',
    ),
    _c(
        '8.17',
        'Clock synchronization',
        'product',
        'Added by B8, which found this control was load-bearing and unmeasured. Session '
        "revocation compares a token's `iat` against a watermark, and with both taken from app "
        'clocks a replica five seconds ahead minted tokens that survived a withdrawal — '
        'demonstrated. The watermark comes from the database now, and the remaining app-to-'
        'database gap is sampled every minute, published as `igni_clock_skew_seconds` and alerted '
        "on by `IgniClockSkew`. The audit trail's ordering and retention's windows depended on "
        'the same clocks.',
        'LG-7',
        'AC-11',
    ),
    _c(
        '8.18',
        'Use of privileged utility programs',
        'shared',
        'The software ships none: containers run as a non-root user with a read-only root '
        'filesystem and dropped capabilities, and no pod mounts a service-account token. Utilities '
        'an operator installs on the host are theirs.',
        'CM-4',
        'CM-10',
    ),
    _c(
        '8.19',
        'Installation of software on operational systems',
        'product',
        'Images are pinned by digest-bearing tags rather than `latest`, which the chart refuses; '
        'migrations run as a pre-install hook that must succeed before anything serves; and CD '
        'cannot build an untested commit.',
        'CM-1',
        'CM-10',
        'CM-7',
    ),
    _c(
        '8.20',
        'Networks security',
        'shared',
        'The chart ships a NetworkPolicy and a TLS terminator, and B2 found the Caddyfile it '
        'shipped had never started — `caddy validate` rejected it from the day it was written. '
        'Both are validated in CI now. The network the customer runs it on is theirs, and DP-4 '
        'is `Operator` for the parts that genuinely are — the DNS record, and TLS anywhere they '
        'put their own proxy in front.',
        'CM-10',
    ),
    _c(
        '8.21',
        'Security of network services',
        'shared',
        'The services the software exposes are enumerated and each is authenticated: user tokens, '
        'organisation-scoped tokens for services a super admin has approved, a node token, and a '
        'super-admin key. B7 found a service-token subtree unreachable at the URL its clients '
        'used, in both directions; that subtree and its shared secret have since been replaced '
        'by service registration and approval. '
        'Transport protection of those services is DP-4, `Operator`.',
        'AC-1',
    ),
    _c(
        '8.22',
        'Segregation of networks',
        'operator',
        "The chart's NetworkPolicy segregates what it can inside a namespace; the surrounding "
        "network design is the customer's.",
    ),
    _c(
        '8.23',
        'Web filtering',
        'shared',
        "The server makes no request to the web (DP-5). The client's agent has two web tools, "
        'a fetch that is allowed by default and a search that asks first, and both are off '
        'under `air_gapped: true`, `permissions.web_fetch: deny` and `permissions.web_search: '
        "deny`, which an organisation can publish as group policy. Which sites a deployment that "
        "keeps them on may reach is the organisation's own proxy and its own SoA.",
        'DP-5',
    ),
    _c(
        '8.24',
        'Use of cryptography',
        'product',
        'Fernet for provider credentials at rest, with a missing key refusing the write rather '
        'than storing plaintext; rotation with both keys live and a tool that reports before it '
        'writes; bcrypt for passwords; and pinned JWT algorithms. TLS ships as a terminator but '
        'is cited as DP-4 `Operator` rather than as evidence here, because the certificate and '
        "the DNS record are the customer's — the first version of this document claimed it and "
        'the generator refused to render.',
        'DP-1',
        'DP-2',
        'AC-5',
    ),
    _c(
        '8.25',
        'Secure development life cycle',
        'shared',
        'CI gates lint, types, a security audit, the whole server suite, the portal suite, both '
        'browser suites, an accessibility pass over every page, a chart install on a real '
        'cluster and a restore drill. A count of tests is deliberately not quoted here: a number '
        'in a generated document is the staleness this file exists to avoid. The policy that '
        'says all of this must happen is OR-2.',
        'CM-1',
    ),
    _c(
        '8.26',
        'Application security requirements',
        'shared',
        'This register is the requirement set, and it is asserted rather than asserted-to: every '
        'control cites evidence, and the review sections indexed in `REVIEW_FINDINGS.md` record what each '
        'requirement cost. Requirements the organisation adds are its own. No count is quoted: '
        'the first version derived one, which meant writing a new section made this document '
        'stale — a coupling that bought nothing, since the claim is that the record exists and '
        'not how long it is.',
        'CM-1',
    ),
    _c(
        '8.27',
        'Secure system architecture and engineering principles',
        'product',
        'Stated and enforced rather than aspirational: no data leaves the deployment, no pod runs '
        'as root, unsafe configuration is refused at startup, and authorisation is checked at one '
        'choke point per question so a new route cannot forget.',
        'DP-5',
        'CM-4',
        'CM-5',
        'AC-2',
    ),
    _c(
        '8.28',
        'Secure coding',
        'product',
        'Static analysis and typing gate every change, and the recurring pattern of this review '
        'is that a fix ships as a rule over the live router or the live schema rather than as a '
        'corrected call site — because a list of call sites rots.',
        'CM-1',
        'CM-2',
    ),
    _c(
        '8.29',
        'Security testing in development and acceptance',
        'product',
        'Security behaviour is tested, not reviewed: cross-organisation access swept across every '
        'parametrised endpoint, revocation swept across every authenticated route, an air-gap '
        'rule in both repositories, and every fix in this review revert-checked to prove the test '
        'fails without it.',
        'CM-1',
        'AC-2',
        'AC-11',
    ),
    _c(
        '8.30',
        'Outsourced development',
        'organisation',
        'Who writes the code. If development is outsourced, the gates in CM-1 apply to their '
        'commits identically — that is the point of gating in CI rather than in review.',
    ),
    _c(
        '8.31',
        'Separation of development, test and production environments',
        'product',
        'Separated, and the separation is enforced rather than conventional: production refuses '
        "the dev authentication bypass, `/docs` and `/redoc` 404, and the bypass's durable side "
        'effect — a live admin row that outlived the flag — is refused at startup.',
        'CM-6',
        'CM-5',
    ),
    _c(
        '8.32',
        'Change management',
        'product',
        'Migrations are reversible and round-trip tested against populated data, CD cannot build '
        "an untested commit, and the chart's migration hook must succeed before anything serves.",
        'CM-7',
        'CM-1',
    ),
    _c(
        '8.33',
        'Test information',
        'product',
        'No production data in tests: fixtures are synthetic, and the one dev affordance with a '
        'durable side effect was found by reading a restored backup and then refused at startup.',
        'CM-6',
        'DP-13',
    ),
    _c(
        '8.34',
        'Protection of information systems during audit testing',
        'organisation',
        'How an audit is conducted against a running deployment. What the software provides is a '
        'read-only way to answer most of it: the audit-trail export, the access review, and the '
        'subject-access report.',
    ),
)

ALL_CONTROLS: tuple[Control, ...] = ORGANIZATIONAL + PEOPLE + PHYSICAL + TECHNOLOGICAL

THEMES: tuple[tuple[str, str, tuple[Control, ...]], ...] = (
    ('A.5', 'Organizational controls', ORGANIZATIONAL),
    ('A.6', 'People controls', PEOPLE),
    ('A.7', 'Physical controls', PHYSICAL),
    ('A.8', 'Technological controls', TECHNOLOGICAL),
)

#: What each theme must contain, per ISO/IEC 27001:2022 Annex A. Written
#: down so a control lost in an edit is a failure rather than a smaller
#: table.
EXPECTED_COUNTS = {'A.5': 37, 'A.6': 8, 'A.7': 14, 'A.8': 34}


def register_rows() -> dict[str, str]:
    """Every control id in the register, with its status."""
    rows: dict[str, str] = {}
    text = _TRACKER.read_text()
    for match in re.finditer(r'^\|\s*([A-Z]{2}-\d+)\s*\|([^|]*)\|([^|]*)\|\s*`?([A-Za-z]+)`?\s*\|', text, re.M):
        rows[match.group(1)] = match.group(4)
    return rows


def unknown_citations() -> list[str]:
    """Rows this document cites that the register does not have."""
    known = register_rows()
    return sorted({row for c in ALL_CONTROLS for row in c.rows if row not in known})


def unverified_citations() -> list[str]:
    """Rows cited as implementing a control while not `Verified`.

    The whole load-bearing rule. A `product` or `shared` disposition is a
    claim that something works, and the only evidence this document has
    is a register row — so if that row is `Process`, `Operator` or
    `Partial`, the claim is not supported and this refuses to render.
    """
    statuses = register_rows()
    offenders = []
    for control in ALL_CONTROLS:
        if control.disposition not in ('product', 'shared'):
            continue
        for row in control.rows:
            if statuses.get(row) != 'Verified':
                offenders.append(f'{control.ref} cites {row}, which is {statuses.get(row, "missing")}')
    return sorted(offenders)


def _rows_cell(control: Control) -> str:
    """Evidence for the software's half, and a marker for anyone else's.

    A `shared` row carries both: the register rows that back what the
    software does, *and* the placeholder for the part it does not. The
    docstring at the top of this file said so before the renderer did —
    the same kind of claim-without-a-mechanism this review keeps
    finding — so it is here now, and a test asserts it.
    """
    cited = ', '.join(f'`{row}`' for row in control.rows)
    if control.disposition == 'shared':
        return f'{cited} + {PLACEHOLDER}' if cited else PLACEHOLDER
    return cited or '—'


def _disposition_cell(control: Control) -> str:
    return f'`{control.disposition}`'


def review_sections() -> int:
    """How many numbered review sections `REVIEW_FINDINGS.md` indexes.

    Counted rather than quoted, so 8.26's justification cannot be the
    kind of stale number this whole document is designed against.
    """
    text = (_ROOT / 'docs/REVIEW_FINDINGS.md').read_text()
    return len(re.findall(r'^\| §(\d+) \|', text, re.M))


def render() -> str:
    counts = {d: sum(1 for c in ALL_CONTROLS if c.disposition == d) for d in DISPOSITIONS}
    sections = review_sections()

    out: list[str] = []
    out.append('# Statement of Applicability')
    out.append('')
    out.append('**GENERATED by tools/generate_statement_of_applicability.py — do not edit.**')
    out.append('Regenerate with `python tools/generate_statement_of_applicability.py`;')
    out.append('`tests/test_statement_of_applicability.py` fails if the committed file and the')
    out.append('generator disagree.')
    out.append('')
    out.append('ISO/IEC 27001:2022 Annex A, all 93 controls. For each: whether it applies, why,')
    out.append('and — where the software implements it — which rows of')
    out.append('[`COMPLIANCE_TRACKER.md`](COMPLIANCE_TRACKER.md) are the evidence. Those rows are')
    out.append('themselves asserted by tests, which is the point of citing them rather than')
    out.append('restating them: this document cannot claim something the register has not')
    out.append('verified, because the generator refuses to render if it does.')
    out.append('')
    out.append('## Scope')
    out.append('')
    out.append('Two things get conflated in a self-hosted product, and conflating them is how a')
    out.append('Statement of Applicability becomes untrue.')
    out.append('')
    out.append('**This document is the SoA for the software** — what this repository builds and')
    out.append('ships, as a customer runs it in their own infrastructure. Every disposition below')
    out.append('answers for that.')
    out.append('')
    out.append('**The adopting organisation needs its own**, covering its offices, its laptops,')
    out.append('its employment practices and its suppliers. No repository can contain that: this')
    out.append('one does not know the legal entity, the locations or the staff. Controls that')
    out.append('depend on those are marked `organisation` below and are the input to that')
    out.append('document, not a substitute for it.')
    out.append('')
    out.append('| Field | Value |')
    out.append('|---|---|')
    out.append(f'| ISMS scope statement | {PLACEHOLDER} |')
    out.append(f'| Legal entity | {PLACEHOLDER} |')
    out.append(f'| Locations in scope | {PLACEHOLDER} |')
    out.append(f'| Information systems in scope | {PLACEHOLDER} |')
    out.append(f'| Exclusions from scope, with justification | {PLACEHOLDER} |')
    out.append(f'| Risk assessment this SoA follows | {PLACEHOLDER} (C2) |')
    out.append(f'| Approved by | {PLACEHOLDER} |')
    out.append(f'| Date of approval | {PLACEHOLDER} |')
    out.append('')
    out.append('Those eight are deliberately blank. A generated SoA that filled in a scope')
    out.append('statement would be describing an organisation it knows nothing about — the same')
    out.append('refusal `RECORDS_OF_PROCESSING.md` makes about a legal basis.')
    out.append('')
    out.append('## Dispositions')
    out.append('')
    out.append('| Disposition | Count | Meaning |')
    out.append('|---|---|---|')
    for name, meaning in (
        ('product', 'The software implements this. Cites register rows, every one of which must be `Verified`.'),
        ('shared', 'The software does a defined part; the organisation must do the rest. Both are stated.'),
        ('operator', "A decision in the customer's own deployment or facility."),
        ('organisation', "The adopting organisation's to do. No code can."),
        ('excluded', 'Does not apply, with the reason. Never used to mean "not done yet".'),
    ):
        out.append(f'| `{name}` | {counts[name]} | {meaning} |')
    out.append('')
    out.append(
        f'{counts["product"] + counts["shared"]} of the 93 controls have something in this '
        'software behind them. That is not a score: most of Annex A is about people, places'
    )
    out.append('and paperwork, and a product claiming most of it would be claiming things it')
    out.append('cannot see. The number worth reading is the 2 marked `excluded` — a control')
    out.append('declared not applicable is the only kind an auditor argues with.')
    out.append('')

    for prefix, title, controls in THEMES:
        out.append(f'## {prefix} {title} ({len(controls)})')
        out.append('')
        out.append('| Control | Title | Applies | Evidence | Justification |')
        out.append('|---|---|---|---|---|')
        for control in controls:
            justification = control.justification.replace('{sections}', str(sections))
            out.append(
                f'| {control.ref} | {control.title} | {_disposition_cell(control)} '
                f'| {_rows_cell(control)} | {justification} |'
            )
        out.append('')

    out.append('## What is still open')
    out.append('')
    out.append('The `organisation` rows are not a to-do list this repository can work through.')
    out.append('The ones with a tracked place to go:')
    out.append('')
    out.append('| Control | Where it is tracked |')
    out.append('|---|---|')
    out.append('| 5.1, 5.36 | OR-2 — policies. C3 |')
    out.append('| 5.24, 5.26, 5.27 | LG-8, OR-1 — the incident response plan. C4 |')
    out.append('| 5.19, 5.20, 5.22 | OR-4 — suppliers and sub-processors. C7 |')
    out.append('| 5.35 | OR-5, OR-7 — internal audit, and an auditor |')
    out.append('| 6.3 | OR-3 — awareness training |')
    out.append('| 8.8 | CM-9 — a remediation SLA is a commitment, not a mechanism. C6 |')
    out.append('')
    out.append('And the risk assessment this SoA is supposed to follow does not exist yet (C2).')
    out.append('The order is the wrong way round on purpose: ISO expects risk assessment then')
    out.append("SoA, and this was written first because the software's half is knowable now and")
    out.append('the assessment needs a person. Stated rather than hidden, and the scope table')
    out.append('above will not let it be forgotten.')
    out.append('')
    return '\n'.join(out)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='exit non-zero if the file is stale')
    args = parser.parse_args()

    unknown = unknown_citations()
    if unknown:
        print(f'these cite register rows that do not exist: {unknown}', file=sys.stderr)
        return 2
    unverified = unverified_citations()
    if unverified:
        print('these claim implementation and cite a row that is not Verified:', file=sys.stderr)
        for line in unverified:
            print(f'  {line}', file=sys.stderr)
        return 2

    rendered = render()
    if args.check:
        if not _OUTPUT.exists() or _OUTPUT.read_text() != rendered:
            print(f'{_OUTPUT} is stale; regenerate it', file=sys.stderr)
            return 1
        print(f'{_OUTPUT} is current')
        return 0

    _OUTPUT.write_text(rendered)
    print(f'wrote {_OUTPUT} ({len(rendered.splitlines())} lines, {len(ALL_CONTROLS)} controls)')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
