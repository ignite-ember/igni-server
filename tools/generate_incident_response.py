"""Generate `docs/INCIDENT_RESPONSE.md` from the live system.

C4, closing LG-8 — the largest remaining gap in the register, and the
one four clauses of the incident response policy point at.

**An incident response plan is where boilerplate does the most damage.**
It is read once, under pressure, by somebody who did not write it. A
generic plan then costs time at the exact moment there is none: "contain
the incident" is not an instruction, and "revoke access" is not an
instruction either if nobody knows which button.

So every step here names a real thing — an endpoint, an alert, a script,
a command — and **the generator refuses to render if any of them does not
exist**:

* endpoints are checked against the live router, so a renamed route
  breaks the build rather than the response;
* alerts are checked against `charts/igni/files/alerts.yaml`;
* scripts and files are checked on disk;
* register rows are checked, and a step claiming a capability must cite
  one that is `Verified`.

That is the same discipline as the Statement of Applicability and the
policies, aimed at the document with the shortest reading time and the
highest cost of being wrong.

**What it does not do** is invent the organisation. Roles, contacts,
severities' business meaning, the decision to notify a regulator and who
makes it: those are `__ORGANISATION_TO_COMPLETE__`. A plan naming a
fictional on-call rota is worse than no plan, because somebody will
follow it.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
import pathlib
import re
import sys

_ROOT = pathlib.Path(__file__).resolve().parent.parent
_TRACKER = _ROOT / 'docs/COMPLIANCE_TRACKER.md'
_ALERTS = _ROOT / 'charts/igni/files/alerts.yaml'
_OUTPUT = _ROOT / 'docs/INCIDENT_RESPONSE.md'

PLACEHOLDER = '`__ORGANISATION_TO_COMPLETE__`'


@dataclass(frozen=True)
class Step:
    ref: str
    phase: str
    #: The instruction. Imperative, and specific enough to follow at
    #: three in the morning.
    action: str
    #: Why this step, and what it costs. Short.
    note: str = ''
    #: HTTP routes, as the live router spells them.
    endpoints: tuple[str, ...] = field(default_factory=tuple)
    #: Alert names from the shipped rules.
    alerts: tuple[str, ...] = field(default_factory=tuple)
    #: Repository paths — scripts, configuration, documents.
    files: tuple[str, ...] = field(default_factory=tuple)
    #: Register rows that make this step possible.
    rows: tuple[str, ...] = field(default_factory=tuple)
    #: True when the step is a decision or an action only a person can
    #: take. Carries the placeholder instead of evidence.
    organisational: bool = False


def _s(ref, phase, action, note='', endpoints=(), alerts=(), files=(), rows=(), organisational=False):
    return Step(
        ref,
        phase,
        action,
        note,
        tuple(endpoints),
        tuple(alerts),
        tuple(files),
        tuple(rows),
        organisational,
    )


PHASES = (
    ('1', 'Detect', 'How you find out. Nothing here phones home, so every signal is one the customer owns.'),
    ('2', 'Triage', 'Decide what it is and how bad, before touching anything.'),
    ('3', 'Contain', 'Stop it continuing. Every lever here is reversible except the last.'),
    ('4', 'Investigate', 'Establish scope from records that cannot have been altered.'),
    ('5', 'Notify', 'Obligations with clocks on them.'),
    ('6', 'Recover', 'Return to a known-good state.'),
    ('7', 'Learn', 'The step that is always skipped.'),
)

STEPS: tuple[Step, ...] = (
    # ── 1. Detect ────────────────────────────────────────────────────
    _s(
        'D1',
        '1',
        'Check the alerts.',
        'The rules ship with the chart, each carrying a description that says what to do — a '
        'count is not quoted here because it was wrong within a pass of being written elsewhere. '
        'IgniAuditWriteFailing fires on a single occurrence, because one lost audit record is '
        'worth knowing about; IgniClockSkew fires when a replica drifts far enough that a session '
        'withdrawal can be outlived.',
        alerts=('IgniAuditWriteFailing', 'IgniDependencyDown', 'IgniServerErrors', 'IgniClockSkew'),
        files=('charts/igni/files/alerts.yaml',),
        rows=('LG-7',),
    ),
    _s(
        'D2',
        '1',
        'Read the audit trail, filtered to denials.',
        'Every authentication attempt is recorded including failures, and a denied privileged '
        'action is the row this trail exists for. Filterable by actor, target and outcome.',
        endpoints=('GET /v1/portal/audit-events',),
        rows=('LG-1', 'LG-3', 'LG-4'),
    ),
    _s(
        'D3',
        '1',
        'Check whether backups are still being taken.',
        '`.last-success` in the backup directory is written only after an archive has been '
        'verified, so its age is the honest signal. The compose healthcheck goes red at two '
        'intervals; a stale marker during an incident means the recovery option in phase 6 may '
        'not exist.',
        files=('containers/backup.sh',),
        rows=('DP-11',),
    ),
    _s('D4', '1', 'Somebody told you.', '', organisational=True),
    # ── 2. Triage ────────────────────────────────────────────────────
    _s(
        'T1',
        '2',
        'Write down what you know, with timestamps, before changing anything.',
        'Containment destroys evidence — a withdrawn session stops the requests you would have '
        'wanted to see. Five minutes of notes first is the difference between a scope you can '
        'state and one you have to guess.',
        organisational=True,
    ),
    _s('T2', '2', 'Classify it.', '', organisational=True),
    _s(
        'T3',
        '2',
        'Establish whether personal data is involved, because that starts a clock.',
        'The subject-access report is the fastest way to see what the deployment holds about a '
        'person; the records of processing say which tables could be in scope at all.',
        endpoints=('POST /v1/portal/subject-access',),
        files=('docs/RECORDS_OF_PROCESSING.md',),
        rows=('DP-8', 'DP-9'),
    ),
    # ── 3. Contain ───────────────────────────────────────────────────
    _s(
        'C1',
        '3',
        "Withdraw one person's sessions.",
        'Every token that person holds stops working on the next request — portal, CLI and '
        'desktop app. Reversible: they sign in again and get a new one. This is the lever for a '
        'suspected leaked credential and it does not require deleting anybody.',
        endpoints=('POST /v1/portal/team/{user_id}/revoke-sessions', 'POST /v1/portal/me/revoke-sessions'),
        rows=('AC-11',),
    ),
    _s(
        'C2',
        '3',
        'Deactivate their membership.',
        'Cuts organisation access on the next request, and is reversible. Different from C1 and '
        'both are usually right: deactivation stops them reaching this organisation, withdrawal '
        'stops the credential itself.',
        endpoints=('PATCH /v1/portal/team/{user_id}/status',),
        rows=('AC-11', 'AC-3'),
    ),
    _s(
        'C3',
        '3',
        'Rotate the encryption key.',
        'Both keys live during the change: the new one encrypts, the previous one still decrypts, '
        'and the tool re-encrypts stored rows — reporting what it would do before it writes. Do '
        'this when the key itself may have been exposed, not for a leaked user credential.',
        files=('tools/rotate_secret_key.py',),
        rows=('DP-2',),
    ),
    _s(
        'C4',
        '3',
        'Take a GPU node out of rotation.',
        'Drains it without deleting it, and the drain survives the cluster re-announcing itself — '
        'registration is idempotent and would otherwise overwrite the change within 180 seconds. '
        'Only Resume clears it.',
        endpoints=('POST /v1/portal/inference/nodes/{node_id}/drain',),
        rows=('AV-1',),
    ),
    _s(
        'C5',
        '3',
        'Rotate the shared secrets an attacker may hold.',
        'The node registration token and the super-admin key are separate values on purpose: '
        'the node token lives in Terraform and cloud-init, a much wider blast radius than the '
        'other, and each must be rotatable without taking the other down. Configuration change '
        'plus a restart; the startup checks refuse anything too short. No secret is shared with '
        'a service such as Artifacts or CodeIndex: one whose own key may be compromised is '
        "revoked on the portal's Services page, which stops its tokens at once, and approved "
        'again after it registers a new key.',
        files=('docs/self-hosting.md',),
        rows=('CM-5',),
    ),
    _s(
        'C6',
        '3',
        'Delete the account.',
        'The last resort, and the only irreversible one here. It ends every session the account '
        'issued and is swept across the whole authenticated surface. Prefer C1 and C2 while you '
        "still need the person's history to be attributable.",
        endpoints=('DELETE /v1/portal/team/{user_id}',),
        rows=('AC-11', 'DP-7'),
    ),
    # ── 4. Investigate ───────────────────────────────────────────────
    _s(
        'I1',
        '4',
        'Export the audit trail as JSON Lines and work on the copy.',
        'The table is append-only — `UPDATE` is refused by a database trigger, and the refusal '
        'survives a restore — so the original cannot be tidied up by accident. The export is '
        'itself audited, because it hands somebody the whole trail.',
        endpoints=('GET /v1/portal/audit-events/export',),
        rows=('LG-2', 'LG-4'),
    ),
    _s(
        'I2',
        '4',
        'Establish who had access, and when they last used it.',
        'Members, roles and last sign-in. Answers "was this account dormant" without a database query.',
        endpoints=('GET /v1/portal/access-review',),
        rows=('AC-8',),
    ),
    _s(
        'I3',
        '4',
        'Establish what the deployment holds about an affected person.',
        'Every classified personal-data column, from the same classification the build asserts '
        'against the live schema — so the report cannot silently omit a column a migration added.',
        endpoints=('POST /v1/portal/subject-access',),
        rows=('DP-8',),
    ),
    _s(
        'I4',
        '4',
        'Check the clocks before trusting the ordering.',
        'The trail is read newest-first with keyset pagination on `occurred_at`, which is an app '
        'clock. Skew between replicas mis-orders it and can skip a row at a page boundary — so a '
        'timeline assembled during an incident is only as good as `igni_clock_skew_seconds` says '
        'it is.',
        alerts=('IgniClockSkew',),
        rows=('LG-7',),
    ),
    # ── 5. Notify ────────────────────────────────────────────────────
    _s('N1', '5', 'Decide whether this is a personal-data breach, and record the reasoning.', '', organisational=True),
    _s(
        'N2',
        '5',
        'If it is, notify the supervisory authority within 72 hours of becoming aware.',
        '',
        organisational=True,
    ),
    _s('N3', '5', 'Consider whether affected people must be told directly.', '', organisational=True),
    _s('N4', '5', 'Tell the customer, if this deployment is not yours.', '', organisational=True),
    _s(
        'N5',
        '5',
        'Be ready for the requests that follow a notification.',
        'People told their data was involved exercise their rights, and both routes exist: a '
        'subject-access report covering every classified column, and erasure that redacts a named '
        "person from their documents without destroying other people's comments on them.",
        endpoints=('POST /v1/portal/subject-access', 'POST /v1/portal/erasure'),
        rows=('DP-8', 'DP-7'),
    ),
    # ── 6. Recover ───────────────────────────────────────────────────
    _s(
        'R1',
        '6',
        'Restore from a verified backup if data was destroyed or altered.',
        'The drill that runs in CI is the procedure: restore, compare schema and rows, check the '
        'encrypted credentials byte for byte, confirm the append-only trigger survived, and boot '
        'the application against the restored database before believing it.',
        files=('tools/restore-drill.sh', 'containers/backup.sh'),
        rows=('DP-10', 'DP-11'),
    ),
    _s(
        'R2',
        '6',
        'Re-issue what you revoked.',
        'Reactivate memberships, reconnect integrations, resume drained nodes. Everything in '
        'phase 3 except C6 is reversible, which is why C6 is last.',
        endpoints=('PATCH /v1/portal/team/{user_id}/status', 'POST /v1/portal/inference/nodes/{node_id}/drain'),
        rows=('AC-11', 'AV-1'),
    ),
    _s(
        'R3',
        '6',
        'Confirm the deployment is actually healthy, not merely running.',
        '`/ready` distinguishes liveness from readiness and names each dependency, so a green '
        'process with a dead database is visible rather than reassuring.',
        endpoints=('GET /ready',),
        rows=('AV-1',),
    ),
    # ── 7. Learn ─────────────────────────────────────────────────────
    _s(
        'L1',
        '7',
        'Write down what happened, what it cost, and the rule that keeps it fixed.',
        'The pre-production review applied that practice to defects, finding by finding '
        '(indexed in `docs/REVIEW_FINDINGS.md`). The '
        'shape transfers: a fix without a rule is a fix that comes back, and the rule is what '
        'makes the write-up worth the hour.',
        files=('docs/REVIEW_FINDINGS.md',),
    ),
    _s(
        'L2',
        '7',
        'Add the check that would have caught it.',
        'The recurring lesson of this codebase: a guard over the live router, the live schema or '
        'the shipped artefact, rather than a corrected call site. A list of call sites rots.',
        files=('docs/POLICIES.md',),
        rows=('CM-1',),
    ),
    _s(
        'L3',
        '7',
        'Update the risk assessment.',
        'It is built from realised risks, so a new incident belongs in it as observed rather than '
        'estimated. If the incident is a new risk, add it; if it is an old one, its treatment was '
        'insufficient and that is the finding.',
        files=('docs/RISK_ASSESSMENT.md',),
    ),
    _s('L4', '7', 'Review whether this plan helped.', '', organisational=True),
)


def served_operations() -> set[str]:
    """Every `METHOD /path` the live router serves.

    The method is part of it deliberately. A plan read under pressure
    that says only `/v1/portal/team/{user_id}/status` leaves somebody
    guessing between PATCH and PUT, and the wrong guess is a 405 at the
    worst moment.
    """
    if str(_ROOT) not in sys.path:
        sys.path.insert(0, str(_ROOT))
    from app.main import app

    return {
        f'{method.upper()} {path}'
        for path, operations in app.openapi()['paths'].items()
        for method in operations
        if method.upper() in ('GET', 'POST', 'PUT', 'PATCH', 'DELETE')
    }


def served_endpoints() -> set[str]:
    """Every path the live router serves.

    `sys.path` is adjusted because this runs as a script from `tools/`,
    where the repository root is not importable — the same reason the
    seed script for the restore drill has to be mounted inside `/app`.
    """
    if str(_ROOT) not in sys.path:
        sys.path.insert(0, str(_ROOT))
    from app.main import app

    return set(app.openapi()['paths'])


def declared_alerts() -> set[str]:
    return set(re.findall(r'alert:\s*(\w+)', _ALERTS.read_text()))


def register_rows() -> dict[str, str]:
    rows: dict[str, str] = {}
    for match in re.finditer(
        r'^\|\s*([A-Z]{2}-\d+)\s*\|([^|]*)\|([^|]*)\|\s*`?([A-Za-z]+)`?\s*\|', _TRACKER.read_text(), re.M
    ):
        rows[match.group(1)] = match.group(4)
    return rows


def unknown_endpoints() -> list[str]:
    """Routes the plan names that the router does not serve.

    The rule that matters most here. A plan telling somebody to POST to a
    route that was renamed is worse than a plan with no routes: they will
    try it, get a 404, and spend the worst ten minutes of the incident
    wondering what else in the document is wrong.
    """
    served = served_operations()
    return sorted({e for s in STEPS for e in s.endpoints if e not in served})


def unknown_alerts() -> list[str]:
    declared = declared_alerts()
    return sorted({a for s in STEPS for a in s.alerts if a not in declared})


def missing_files() -> list[str]:
    return sorted({f for s in STEPS for f in s.files if not (_ROOT / f).exists()})


def unknown_rows() -> list[str]:
    known = register_rows()
    return sorted({r for s in STEPS for r in s.rows if r not in known})


def unverified_rows() -> list[str]:
    statuses = register_rows()
    offenders = []
    for step in STEPS:
        if step.organisational:
            continue
        for row in step.rows:
            if statuses.get(row) != 'Verified':
                offenders.append(f'{step.ref} relies on {row}, which is {statuses.get(row, "missing")}')
    return sorted(offenders)


def steps_without_a_mechanism() -> list[str]:
    """A technical step that names nothing to do.

    "Contain the incident" is not an instruction. A step that is not
    marked organisational must point at something a person can actually
    use.
    """
    return sorted(s.ref for s in STEPS if not s.organisational and not (s.endpoints or s.alerts or s.files))


def organisational_steps_claiming_evidence() -> list[str]:
    return sorted(s.ref for s in STEPS if s.organisational and (s.endpoints or s.alerts or s.files or s.rows))


def render() -> str:
    out: list[str] = []
    out.append('# Incident response plan')
    out.append('')
    out.append('**GENERATED by tools/generate_incident_response.py — do not edit.**')
    out.append('Regenerate with `python tools/generate_incident_response.py`;')
    out.append('`tests/test_incident_response.py` fails if the committed file and the generator')
    out.append('disagree.')
    out.append('')
    out.append('LG-8, and the plan four clauses of')
    out.append('[`POLICIES.md`](POLICIES.md) point at.')
    out.append('')
    out.append('## Read this first')
    out.append('')
    out.append('A plan is read once, under pressure, by somebody who did not write it. So every')
    out.append('step below names a real thing — an endpoint, an alert, a script — and **the')
    out.append('generator refuses to render if any of them does not exist**. Routes are checked')
    out.append('against the live router, alerts against the rules the chart ships, files against')
    out.append('the repository. A renamed route breaks the build rather than the response.')
    out.append('')
    out.append('Steps marked with the placeholder are decisions or actions only a person can')
    out.append('take. They are in the plan because leaving them out would imply the software')
    out.append('handles them; they are blank because a plan naming a fictional on-call rota is')
    out.append('worse than no plan, since somebody will follow it.')
    out.append('')
    out.append('**Every containment lever is reversible except C6.** That is deliberate and it')
    out.append('is the most useful thing on this page: you can act early without deciding')
    out.append('anything permanent.')
    out.append('')
    out.append('| Field | Value |')
    out.append('|---|---|')
    for field_name in (
        'Who declares an incident',
        'Incident lead (role, not a name)',
        'Out-of-hours contact',
        'Severity definitions and response times',
        'Who may authorise C6 (account deletion)',
        'Who decides on regulator notification',
        'Supervisory authority and its contact',
        'Customer notification path',
        'Where incidents are recorded',
        'Approved by',
        'Date of approval',
    ):
        out.append(f'| {field_name} | {PLACEHOLDER} |')
    out.append('')
    out.append('Eleven blank fields, and the plan is not usable until they are filled in. That is')
    out.append('the honest state of LG-8: the levers exist and are proven, the sequence is')
    out.append('written down, and the organisation around it does not exist yet.')
    out.append('')

    statuses = register_rows()
    for number, name, blurb in PHASES:
        steps = [s for s in STEPS if s.phase == number]
        out.append(f'## {number}. {name}')
        out.append('')
        out.append(blurb)
        out.append('')
        for step in steps:
            out.append(f'### {step.ref} — {step.action}')
            out.append('')
            if step.organisational:
                out.append(f'**A decision, not a mechanism.** {PLACEHOLDER}')
                if step.note:
                    out.append('')
                    out.append(step.note)
            else:
                bits = []
                if step.endpoints:
                    bits.append('  '.join(f'`{e}`' for e in step.endpoints))
                if step.alerts:
                    bits.append('  '.join(f'`{a}`' for a in step.alerts))
                if step.files:
                    bits.append('  '.join(f'`{f}`' for f in step.files))
                out.append(f'**Use:** {" · ".join(bits)}')
                if step.rows:
                    evidence = ', '.join(f'`{r}` ({statuses.get(r, "?")})' for r in step.rows)
                    out.append('')
                    out.append(f'**Proven by:** {evidence}')
                if step.note:
                    out.append('')
                    out.append(step.note)
            out.append('')

    out.append('## What this plan is honest about')
    out.append('')
    out.append('**It has never been rehearsed.** Every lever in phase 3 is individually tested —')
    out.append('there are tests that a withdrawn session is refused on every authenticated route,')
    out.append('that a drain survives re-registration, that a rotation keeps both keys live, and')
    out.append('that a restore comes back byte for byte. Nobody has run them in sequence against')
    out.append('a deployment while pretending to be under attack. A tabletop exercise is the')
    out.append("cheapest next thing and it is the organisation's to schedule.")
    out.append('')
    out.append('**Containment destroys evidence.** T1 exists because of it: withdrawing a session')
    out.append('stops the requests you would have wanted to see. The order here puts five minutes')
    out.append('of note-taking before the first lever, which is the opposite of the instinct.')
    out.append('')
    out.append('**Nothing here phones home.** No vendor is watching this deployment and none can')
    out.append('help unless asked — every signal in phase 1 is one the customer runs. That is the')
    out.append('point of the product and it is also a responsibility that comes with it.')
    out.append('')
    out.append('**The 72-hour clock starts at awareness, not at breach.** Which means phase 2')
    out.append("is on the clock too, and T1's timestamps are what establish when awareness")
    out.append('began.')
    out.append('')
    return '\n'.join(out)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='exit non-zero if the file is stale')
    args = parser.parse_args()

    for label, offenders in (
        ('name routes the router does not serve', unknown_endpoints()),
        ('name alerts the chart does not ship', unknown_alerts()),
        ('name files that do not exist', missing_files()),
        ('cite register rows that do not exist', unknown_rows()),
        ('rely on a capability the register has not verified', unverified_rows()),
        ('give an instruction with nothing to use', steps_without_a_mechanism()),
        ('are marked a decision and claim evidence', organisational_steps_claiming_evidence()),
    ):
        if offenders:
            print(f'these steps {label}:', file=sys.stderr)
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
    print(f'wrote {_OUTPUT} ({len(rendered.splitlines())} lines, {len(STEPS)} steps)')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
