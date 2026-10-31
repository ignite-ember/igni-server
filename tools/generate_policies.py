"""Generate `docs/POLICIES.md` from what the code actually enforces.

C3, OR-2. ISO A 5.1 wants topic-specific policies; SOC 2 CC1 wants them
signed. Four are named on the road: access control, cryptography, secure
development, and incident response.

**Policies are the most reliably worthless document in a compliance
pack.** They are downloaded, the company name is replaced, and every
sentence says "shall" about something nobody checks. An auditor reads
one, compares it with the system, and finds the two have never met.

So the rule here is one thing, and everything else follows from it:

> **Every clause names what enforces it, or is marked `unenforced` with
> an owner.**

A clause claiming enforcement must cite register rows — all `Verified` —
or test files that exist, and the generator refuses to render if either
is untrue. A clause nothing enforces is not deleted and not softened: it
is stated, marked, and given an owner, because "we intend to" is useful
and "we do" would be a lie.

That distinction is the whole value. A reader can see, per sentence,
whether it is a fact about the software or a commitment by an
organisation.

**Nothing here is invented.** The numbers are read out of the code:
eight characters with four character classes because that is what
`_validate_password_strength` requires, thirty days because that is what
`create_token` sets, thirty-two characters of shared secret because
that is what `startup_checks` warns below. Where the software
enforces nothing — multi-factor authentication, for instance — the policy
says so rather than describing a control that does not exist.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
import pathlib
import re
import sys

_ROOT = pathlib.Path(__file__).resolve().parent.parent
_TRACKER = _ROOT / 'docs/COMPLIANCE_TRACKER.md'
_SOA_GENERATOR = _ROOT / 'tools/generate_statement_of_applicability.py'
_OUTPUT = _ROOT / 'docs/POLICIES.md'

PLACEHOLDER = '`__ORGANISATION_TO_COMPLETE__`'

POLICIES = (
    ('AC', 'Access control', 'A 5.15, A 5.16, A 5.17, A 5.18, A 8.2, A 8.3, A 8.5'),
    ('CR', 'Cryptography', 'A 8.24, A 5.33, A 8.20'),
    ('SD', 'Secure development', 'A 8.25, A 8.26, A 8.27, A 8.28, A 8.29, A 8.31, A 8.32'),
    ('IR', 'Incident response', 'A 5.24, A 5.25, A 5.26, A 5.27, A 6.8, A 8.15, A 8.16'),
)


@dataclass(frozen=True)
class Clause:
    ref: str
    policy: str
    #: The requirement. Written as a requirement, in the present tense.
    shall: str
    #: What makes it true. Prose, and specific: a mechanism, not a
    #: restatement of the clause.
    enforcement: str = ''
    #: Register rows. All must be `Verified` for the clause to count as
    #: enforced.
    rows: tuple[str, ...] = field(default_factory=tuple)
    #: Test files, relative to the repository root. Checked to exist.
    tests: tuple[str, ...] = field(default_factory=tuple)
    #: Set when nothing in the software enforces this. Names who owns it.
    unenforced_owner: str = ''


def _clause(ref, policy, shall, enforcement='', rows=(), tests=(), unenforced_owner=''):
    return Clause(ref, policy, shall, enforcement, tuple(rows), tuple(tests), unenforced_owner)


CLAUSES: tuple[Clause, ...] = (
    # ── Access control ───────────────────────────────────────────────
    _clause(
        'AC-P1',
        'AC',
        'Every endpoint that returns or changes anything requires an authenticated caller.',
        'Not a convention: a sweep generated from the live router asks every route in the '
        'application, so an endpoint added tomorrow is covered without anybody remembering. The '
        'public routes are enumerated with a reason each.',
        ('AC-1',),
        ('tests/test_a_deleted_user_cannot_come_back.py',),
    ),
    _clause(
        'AC-P2',
        'AC',
        "A request authenticated for one organisation cannot read or change another organisation's data.",
        'Every parametrised portal endpoint is swept with a token scoped to a different '
        'organisation, and the list of endpoints comes from the router rather than from a '
        'maintained table.',
        ('AC-2',),
        ('tests/test_no_cross_org_access.py',),
    ),
    _clause(
        'AC-P3',
        'AC',
        'Privileged actions — changing a role or status, removing a member, reading the audit '
        'trail — require an administrator of the organisation acted upon.',
        '`require_admin` resolves the caller against the organisation the action names, and the '
        'membership check is separate from the role check because holding an admin role '
        'somewhere is not authority anywhere.',
        ('AC-3', 'AC-7'),
        ('tests/test_no_cross_org_access.py',),
    ),
    _clause(
        'AC-P4',
        'AC',
        'Local passwords are at least eight characters and contain an upper-case letter, a '
        'lower-case letter, a digit and a symbol.',
        'That is what `_validate_password_strength` requires, and it is stated here because it '
        'is what the code does — not because eight is a recommendation. An organisation wanting '
        'a longer minimum changes the validator, and this sentence with it. Stored with bcrypt.',
        ('AC-5',),
        ('tests/test_the_policies_are_enforced.py',),
    ),
    _clause(
        'AC-P5',
        'AC',
        'Access is withdrawn when authorisation ends, and takes effect on the next request.',
        'Three levers, each proven on the whole authenticated surface: deactivating a membership, '
        'withdrawing every token a person holds, and deleting the account. This review found '
        'three paths that had an exception to it — an access check that consulted only the git '
        'provider, a group pack keyed on a group membership rather than an organisation one, and '
        'a clock skew that let a token outlive its own revocation — and each is closed and swept.',
        ('AC-11', 'AC-7'),
        ('tests/test_withdrawn_sessions_are_refused.py', 'tests/test_group_pack_needs_a_membership.py'),
    ),
    _clause(
        'AC-P6',
        'AC',
        'Sessions expire. A token is valid for thirty days from issue and is refused after that with no grace period.',
        "Thirty days is `create_token`'s default, and the expiry is checked on every request by "
        'the decoder. Asserted for the first time in B8 — it had been correct and unproven, '
        'which for the only bound on a leaked credential is not the same as correct.',
        ('AC-1',),
        ('tests/test_token_expiry_is_enforced.py',),
    ),
    _clause(
        'AC-P7',
        'AC',
        'There are no default, shared or standing accounts, and no account exists that the '
        'deployment did not create deliberately.',
        'Enforced at startup rather than reviewed: the development authentication bypass wrote a '
        'live administrator row that outlived the flag, and a deployment whose database contains '
        'that identity now refuses to start.',
        ('AC-4', 'CM-5'),
        ('tests/test_startup_checks.py',),
    ),
    _clause(
        'AC-P8',
        'AC',
        'Access is reviewed periodically, and the review shows who has access to what and when they last used it.',
        '`GET /v1/portal/access-review` reports members, roles and last sign-in, which needed '
        '`users.last_sign_in_at` because nothing recorded a sign-in at all.',
        ('AC-8',),
        ('tests/test_access_review.py',),
    ),
    _clause(
        'AC-P9',
        'AC',
        'Where the organisation runs an identity provider, authentication is delegated to it and '
        'this deployment issues no password.',
        'Any OIDC provider from one issuer URL, with signature, issuer, audience, expiry and '
        'nonce all checked, algorithms pinned, and PKCE with S256. `GET /v1/auth/methods` drives '
        'the sign-in page so the button exists when it is configured.',
        ('AC-9',),
        ('tests/test_oidc.py',),
    ),
    _clause(
        'AC-P10',
        'AC',
        'Multi-factor authentication is required for administrative access.',
        '',
        (),
        (),
        'Security. **The software does not implement MFA.** Where an OIDC provider is configured '
        'this is satisfied by delegating to it (AC-P9) and is the recommended arrangement. For '
        'local password accounts there is no second factor, so this clause is a commitment the '
        'organisation makes through its IdP and not something this deployment can enforce. '
        'Stating it as enforced would be the exact kind of policy sentence that has never met '
        'the system it describes.',
    ),
    # ── Cryptography ─────────────────────────────────────────────────
    _clause(
        'CR-P1',
        'CR',
        'Provider credentials and other third-party secrets are encrypted before they are '
        'stored, and a deployment without a key refuses to store them rather than storing them '
        'in the clear.',
        'Fernet — authenticated ciphertext, so a modified value fails to decrypt rather than '
        'decrypting to something else. A missing key refuses the write.',
        ('DP-1',),
        ('tests/test_encryption_key_rotation.py', 'tests/test_startup_checks.py'),
    ),
    _clause(
        'CR-P2',
        'CR',
        'The encryption key can be rotated without downtime and without re-linking every integration by hand.',
        'Both keys live during the change: the new key encrypts, the previous one still '
        'decrypts, and `tools/rotate_secret_key.py` re-encrypts stored rows — reporting what it '
        'would do before it writes.',
        ('DP-2',),
        ('tests/test_encryption_key_rotation.py',),
    ),
    _clause(
        'CR-P3',
        'CR',
        'Passwords are stored only as bcrypt hashes, never encrypted and never reversible.',
        'Encryption would be the wrong primitive: a reversible password store is a password '
        'store somebody can reverse.',
        ('AC-5',),
        ('tests/test_no_secrets_in_responses.py',),
    ),
    _clause(
        'CR-P4',
        'CR',
        'Tokens are signed with a 2048-bit RSA key the server generates for itself and stores '
        'encrypted; a deployment without the key that encrypts it does not start. Shared secrets '
        'presented in a header are at least thirty-two characters.',
        'Nobody mints, pastes or holds the signing key, so there is no weak one to choose: '
        '`RsaKeyPair.KEY_SIZE` is 2048 and the private half exists only encrypted under '
        '`SECRET_ENCRYPTION_KEY`, which `startup_checks` requires outside a `try`. Thirty-two is '
        '`_MIN_SHARED_SECRET_LENGTH`. An unset HMAC signing secret was once a full '
        'authentication bypass, which is why signing no longer depends on a setting.',
        ('CM-5',),
        ('tests/test_startup_checks.py',),
    ),
    _clause(
        'CR-P5',
        'CR',
        'Token signing algorithms are pinned, and a token naming a different algorithm is refused.',
        'RS256, pinned in `JWTService.ALGORITHMS`; a client assertion is checked with the '
        'algorithm its registered key type dictates, never the one its header names. An '
        'unpinned decoder accepts `alg: none`, or HS256 keyed with the public key — a signature '
        'check that checks nothing.',
        ('AC-1',),
        ('tests/test_token_expiry_is_enforced.py',),
    ),
    _clause(
        'CR-P6',
        'CR',
        'No secret is written to a log, a URL, an error message or an audit record.',
        'Four separate mechanisms, because this failed in four separate ways: an AST rule over '
        'all of `app/` against logging request bodies, a fixed field list in the JSON formatter '
        'so nothing attached with `extra=` reaches the output, a rule against tokens in '
        "redirect URLs that sweeps the portal's TypeScript as well as the API's Python, and a "
        'rule that a broad exception handler may not describe itself to a caller.',
        ('LG-5', 'LG-9', 'AC-1'),
        ('tests/test_an_error_says_nothing_it_should_not.py', 'tests/test_no_secrets_in_responses.py'),
    ),
    _clause(
        'CR-P7',
        'CR',
        'Data in transit is protected by TLS, and data at rest by the storage layer.',
        '',
        (),
        (),
        'Operations. The product **ships** a TLS terminator with automatic certificate issuance '
        'and an 80→443 redirect, and `make check-configs` validates it in CI after the shipped '
        "Caddyfile turned out never to have started. But the DNS record, the certificate's "
        "trust chain, and disk or volume encryption are the deployment's — DP-3 and DP-4 are "
        '`Operator` in the register for exactly this reason, and a policy claiming the software '
        "enforces them would be claiming somebody else's work.",
    ),
    # ── Secure development ───────────────────────────────────────────
    _clause(
        'SD-P1',
        'SD',
        'No change reaches a release without passing lint, type checking, a dependency audit and the full test suite.',
        'Gated in CI rather than asked for in review, which is what makes it apply to every '
        'commit including an outsourced one. CD additionally cannot build a commit that has not '
        'passed.',
        ('CM-1', 'CM-2'),
        ('tests/test_the_policies_are_enforced.py',),
    ),
    _clause(
        'SD-P2',
        'SD',
        'A security fix ships as a rule over the live system, not as a corrected call site.',
        "The stated lesson of this review, and the reason it is a policy clause: F80's rename "
        "was fixed instance by instance and grew twenty-five new instances; F88's contract "
        'drifted to sixty per cent accuracy for the same reason. So authorisation sweeps are '
        'generated from the router, the personal-data classification from the schema, and the '
        'audit policy from a rule about URL prefixes rather than a list of endpoints.',
        ('CM-1',),
        ('tests/test_no_cross_org_access.py', 'tests/test_subject_access.py'),
    ),
    _clause(
        'SD-P3',
        'SD',
        'A fix is not accepted until the test for it has been seen to fail without the fix.',
        'Every fix in this review was revert-checked. It is in the policy because it caught its '
        'own failures: in one pass, three of four revert-checks turned out to be the check being '
        'broken rather than the fix — a palette that never opened, a scroller found by a '
        'constant, and a "was it served?" check reading a compiled file.',
        ('CM-1',),
        ('tests/test_the_helm_chart_deploys_igni.py',),
    ),
    _clause(
        'SD-P4',
        'SD',
        'Shipped artefacts are executed, not only rendered or linted.',
        'R-8 in the risk assessment, and four occurrences: a worker that died on import, a TLS '
        'config rejected by its own validator, a chart that could never install, and a backup '
        'script nothing had ever run. CI now installs the chart on a real cluster and runs the '
        'restore drill with the shipped script.',
        ('CM-10', 'DP-10', 'DP-11'),
        ('tests/test_the_helm_chart_deploys_igni.py', 'tests/test_compose_deployment.py'),
    ),
    _clause(
        'SD-P5',
        'SD',
        'Development, test and production are separated, and a development affordance cannot '
        'take effect in production.',
        'Enforced rather than conventional: production refuses the authentication bypass, the '
        "API documentation routes answer 404, and the bypass's durable side effect is refused "
        'at startup.',
        ('CM-6', 'CM-5'),
        ('tests/test_startup_checks.py',),
    ),
    _clause(
        'SD-P6',
        'SD',
        'Dependencies and shipped images are audited on every build, and a high or critical finding fails it.',
        'Dependency auditing, image scanning and an SBOM per release, so a customer can answer '
        '"what is in this" without asking.',
        ('CM-2', 'CM-3', 'CM-8'),
        ('tests/test_every_release_has_an_sbom.py',),
    ),
    _clause(
        'SD-P7',
        'SD',
        'A schema change can be rolled back, and rollback is tested against populated data '
        'rather than an empty database.',
        'Round-trip tested with rows present, because a downgrade that works on an empty schema '
        'proves nothing about the one case it is needed in.',
        ('CM-7',),
        ('tests/test_migration_round_trip.py',),
    ),
    _clause(
        'SD-P8',
        'SD',
        'Documents that describe the system are generated from it, or asserted against it.',
        'R-9, and the reason this policy file is itself generated. `COMPLIANCE.md` spent five '
        'passes denying five things that existed; the register, the records of processing, the '
        'Statement of Applicability, the risk assessment and this document are all held to the '
        'code by tests.',
        ('DP-9', 'CM-1'),
        ('tests/test_compliance_tracker_is_current.py', 'tests/test_records_of_processing.py'),
    ),
    _clause(
        'SD-P9',
        'SD',
        'Changes are reviewed by somebody other than their author before merge.',
        '',
        (),
        (),
        'Engineering. Branch protection and required reviewers are settings on the source '
        'host, not properties of this repository, so nothing here can assert it. The gates in '
        'SD-P1 apply regardless of who reviews, which is deliberate — but a policy sentence '
        "claiming enforced review would be describing a checkbox in somebody else's console.",
    ),
    # ── Incident response ────────────────────────────────────────────
    _clause(
        'IR-P1',
        'IR',
        'Privileged activity is recorded with the actor, the action, the target, the outcome and '
        'the source, and the record cannot be altered afterwards.',
        'One audit event per privileged request, written by a middleware rule over the live '
        'router so a handler cannot forget; `UPDATE` refused by a database trigger; and the '
        'refusal re-tested after a restore, because a control that a recovery silently drops is '
        'a control the register would still be claiming.',
        ('LG-1', 'LG-2', 'LG-3'),
        ('tests/test_audit_trail.py',),
    ),
    _clause(
        'IR-P2',
        'IR',
        'A failed audit write is detected rather than lost.',
        'The recorder fails **open** — refusing would turn a database hiccup into an outage on '
        'every privileged endpoint at once — so the failure is counted, alerted on a single '
        'occurrence, and logged with the event it could not store.',
        ('LG-6', 'LG-7'),
        ('tests/test_audit_trail.py',),
    ),
    _clause(
        'IR-P3',
        'IR',
        'The audit trail is reviewable by an administrator and exportable for a SIEM.',
        'Filterable by actor, target and outcome in the portal, and exportable as JSON Lines. '
        'The export is itself audited, because it hands somebody a copy of the whole trail.',
        ('LG-4',),
        ('tests/test_audit_trail.py',),
    ),
    _clause(
        'IR-P4',
        'IR',
        'Conditions that would otherwise be silent are alerted on.',
        'Metrics and alert rules ship with the chart, with every alerted metric asserted to '
        'exist and every metric asserted to have a rule — so the set can grow without either '
        'half going quiet. The customer runs the monitoring; nothing phones home. B8 added the '
        'clock-skew gauge after finding that skew could defeat session revocation with nothing '
        'reporting it.',
        ('LG-7',),
        ('tests/test_monitoring_and_alerts.py', 'tests/test_clock_skew.py'),
    ),
    _clause(
        'IR-P5',
        'IR',
        'When a credential is suspected compromised it can be withdrawn immediately, without '
        'deleting the person or waiting for expiry.',
        'The lever AC-11 said did not exist until B5 built it: one column, compared against the '
        "token's issue time, so every token a person holds stops working on the next request.",
        ('AC-11',),
        ('tests/test_withdrawn_sessions_are_refused.py',),
    ),
    _clause(
        'IR-P6',
        'IR',
        'A suspected breach of personal data is assessed and, where required, notified to the '
        'supervisory authority within seventy-two hours.',
        '',
        (),
        (),
        "Legal. GDPR Article 33's window is a fact; meeting it needs a named person, a decision "
        'procedure and a contact, none of which exist here. What the software supplies is the '
        'input: an immutable trail (IR-P1), an export (IR-P3), and a subject-access report that '
        'can establish who was affected. C4.',
    ),
    _clause(
        'IR-P7',
        'IR',
        'Incidents are responded to according to a documented plan naming roles, severities, '
        'communication paths and escalation.',
        '',
        (),
        (),
        'Security. **The plan is written**: `docs/INCIDENT_RESPONSE.md`, by C4; every route, '
        'alert, file and capability it names is checked to exist. This clause said '
        "it was not.\n\nStill a person's commitment rather than an enforced control, and still "
        '`Partial` in the register (LG-8): the fields naming who declares an incident, who '
        'leads it and who may authorise deletion are the organisation\'s to fill in, and every '
        'lever in it is tested individually while nobody has run them in sequence.',
    ),
    _clause(
        'IR-P8',
        'IR',
        'What is learned from an incident changes the system, and the change is recorded.',
        '',
        (),
        (),
        'Security. The pre-production review applied precisely this practice to defects, finding '
        'by finding: what was found, what it cost, and the rule that keeps it fixed '
        '(`REVIEW_FINDINGS.md` indexes it). A count of '
        'them was here and was already wrong when this document was committed, so there is '
        'none. Applying the practice to real incidents starts with the plan in IR-P7 being '
        'followed, and it has not yet been rehearsed.',
    ),
    _clause(
        'IR-P9',
        'IR',
        'Anybody may report a suspected security problem, and knows how.',
        '',
        (),
        (),
        'Security. **`SECURITY.md` is written**, by C6: where to report, what to include, what '
        'is in scope and what is not, and what a reporter can expect. This clause said it was '
        "not.\n\nStill a person's commitment rather than an enforced control, and the part "
        'that has to be true is not in the file: somebody has to be reading the address. A 6.8.',
    ),
)


def register_rows() -> dict[str, str]:
    rows: dict[str, str] = {}
    for match in re.finditer(
        r'^\|\s*([A-Z]{2}-\d+)\s*\|([^|]*)\|([^|]*)\|\s*`?([A-Za-z]+)`?\s*\|', _TRACKER.read_text(), re.M
    ):
        rows[match.group(1)] = match.group(4)
    return rows


def is_enforced(clause: Clause) -> bool:
    return not clause.unenforced_owner


def unknown_rows() -> list[str]:
    known = register_rows()
    return sorted({row for c in CLAUSES for row in c.rows if row not in known})


def unverified_rows() -> list[str]:
    statuses = register_rows()
    offenders = []
    for clause in CLAUSES:
        if not is_enforced(clause):
            continue
        for row in clause.rows:
            if statuses.get(row) != 'Verified':
                offenders.append(f'{clause.ref} cites {row}, which is {statuses.get(row, "missing")}')
    return sorted(offenders)


def missing_tests() -> list[str]:
    """Test files a clause names that are not on disk.

    A clause pointing at a test that does not exist is the policy
    equivalent of a citation to nothing: it reads as evidence.
    """
    offenders = []
    for clause in CLAUSES:
        for path in clause.tests:
            if not (_ROOT / path).exists():
                offenders.append(f'{clause.ref} names {path}, which does not exist')
    return sorted(offenders)


def clauses_claiming_without_evidence() -> list[str]:
    """The rule the whole document exists for.

    A clause that is not marked `unenforced` must name a mechanism *and*
    cite something checkable. A "shall" with neither is the sentence that
    makes policy documents worthless.
    """
    offenders = []
    for clause in CLAUSES:
        if not is_enforced(clause):
            continue
        if not clause.enforcement:
            offenders.append(f'{clause.ref} claims enforcement and describes no mechanism')
        if not clause.rows and not clause.tests:
            offenders.append(f'{clause.ref} claims enforcement and cites nothing')
    return sorted(offenders)


def unenforced_without_an_owner() -> list[str]:
    """An unenforced clause with no owner is a wish."""
    return sorted(c.ref for c in CLAUSES if c.unenforced_owner and not c.unenforced_owner.split('.')[0].strip())


def render() -> str:
    enforced = [c for c in CLAUSES if is_enforced(c)]
    outstanding = [c for c in CLAUSES if not is_enforced(c)]

    out: list[str] = []
    out.append('# Policies')
    out.append('')
    out.append('**GENERATED by tools/generate_policies.py — do not edit.**')
    out.append('Regenerate with `python tools/generate_policies.py`; `tests/test_policies.py`')
    out.append('fails if the committed file and the generator disagree.')
    out.append('')
    out.append('Four topic-specific policies (ISO A 5.1, SOC 2 CC1): access control,')
    out.append('cryptography, secure development, incident response.')
    out.append('')
    out.append('## How to read this')
    out.append('')
    out.append('Policies are the most reliably worthless document in a compliance pack. They are')
    out.append('downloaded, the company name is replaced, and every sentence says "shall" about')
    out.append('something nobody checks. An auditor reads one, compares it with the system, and')
    out.append('finds the two have never met.')
    out.append('')
    out.append('So there is one rule here, and everything else follows from it:')
    out.append('')
    out.append('> **Every clause names what enforces it, or is marked `unenforced` with an')
    out.append('> owner.**')
    out.append('')
    out.append('A clause claiming enforcement cites rows of')
    out.append('[`COMPLIANCE_TRACKER.md`](COMPLIANCE_TRACKER.md) — all of which must be')
    out.append('`Verified` — or test files, which must exist. The generator refuses to render if')
    out.append('either is untrue.')
    out.append('')
    out.append(f'**{len(enforced)} of {len(CLAUSES)} clauses are enforced by the software.** The')
    out.append(f'other {len(outstanding)} are stated anyway, marked, and given an owner, because')
    out.append('"we intend to" is useful and "we do" would be a lie. A reader can tell per')
    out.append('sentence which they are looking at.')
    out.append('')
    out.append('**Nothing here is invented.** The numbers come out of the code: eight characters')
    out.append('with four character classes because that is what `_validate_password_strength`')
    out.append('requires, thirty days because that is what `create_token` sets, a 2048-bit')
    out.append('signing key because that is what the server generates. Where the software')
    out.append('enforces nothing — multi-factor')
    out.append('authentication, for instance — the policy says so.')
    out.append('')
    out.append('| Field | Value |')
    out.append('|---|---|')
    out.append(f'| Organisation these policies bind | {PLACEHOLDER} |')
    out.append(f'| Policy owner | {PLACEHOLDER} |')
    out.append(f'| Approved by | {PLACEHOLDER} |')
    out.append(f'| Date of approval | {PLACEHOLDER} |')
    out.append(f'| Review interval | {PLACEHOLDER} |')
    out.append(f'| Exceptions process | {PLACEHOLDER} |')
    out.append('')
    out.append('Six blank fields. A policy is binding because an organisation adopted it, and')
    out.append('this generator cannot adopt anything.')
    out.append('')

    for code, title, controls in POLICIES:
        clauses = [c for c in CLAUSES if c.policy == code]
        met = sum(1 for c in clauses if is_enforced(c))
        out.append(f'## {title} policy')
        out.append('')
        out.append(f'*Annex A: {controls}. {met} of {len(clauses)} clauses enforced by the software.*')
        out.append('')
        for clause in clauses:
            out.append(f'### {clause.ref} — {clause.shall}')
            out.append('')
            if is_enforced(clause):
                evidence = [f'`{r}`' for r in clause.rows] + [f'`{t}`' for t in clause.tests]
                out.append(f'**Enforced.** Evidence: {", ".join(evidence)}.')
                out.append('')
                out.append(clause.enforcement)
            else:
                owner, _, reason = clause.unenforced_owner.partition('.')
                out.append(f'**Not enforced by the software. Owner: {owner.strip()}.** {PLACEHOLDER}')
                out.append('')
                out.append(reason.strip())
            out.append('')

    out.append('## Everything not enforced here, in one place')
    out.append('')
    out.append('An organisation adopting these policies is committing to this list. It is short')
    out.append('on purpose, and none of it can be closed by code.')
    out.append('')
    out.append('| Clause | Requirement | Owner |')
    out.append('|---|---|---|')
    for clause in outstanding:
        owner = clause.unenforced_owner.partition('.')[0].strip()
        out.append(f'| {clause.ref} | {clause.shall} | {owner} |')
    out.append('')
    out.append('The register rows behind them: `OR-2` for the policies themselves, `LG-8` for the')
    out.append("incident response plan (C4), `DP-3` and `DP-4` for the deployment's own transport")
    out.append('and storage. Nothing in that table is a defect in the software; each is a')
    out.append('sentence a person has to stand behind.')
    out.append('')
    out.append('## What these policies deliberately do not say')
    out.append('')
    out.append('**No "shall be appropriate".** A requirement whose test is somebody\'s judgement')
    out.append('is not a requirement. Where the software enforces a specific number, the number')
    out.append('is here; where it enforces nothing, the clause says so.')
    out.append('')
    out.append('**No commitments about people.** Screening, training, disciplinary process and')
    out.append("acceptable use are real ISO controls and belong in an organisation's own")
    out.append('policies. The Statement of Applicability marks them `organisation` for the same')
    out.append('reason. A vendor writing an employment policy for its customers would be writing')
    out.append('fiction.')
    out.append('')
    out.append('**No claim of certification.** These are the policies a certification would')
    out.append('examine, not evidence of one.')
    out.append('')
    return '\n'.join(out)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='exit non-zero if the file is stale')
    args = parser.parse_args()

    for label, offenders in (
        ('cite register rows that do not exist', unknown_rows()),
        ('claim enforcement on a row that is not Verified', unverified_rows()),
        ('name a test file that does not exist', missing_tests()),
        ('claim enforcement with no mechanism or no evidence', clauses_claiming_without_evidence()),
        ('are unenforced with no owner', unenforced_without_an_owner()),
    ):
        if offenders:
            print(f'these {label}:', file=sys.stderr)
            for line in offenders:
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
    print(f'wrote {_OUTPUT} ({len(rendered.splitlines())} lines, {len(CLAUSES)} clauses)')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
