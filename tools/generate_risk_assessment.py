"""Generate `docs/RISK_ASSESSMENT.md` from the findings and the register.

C2, the other half of OR-1. ISO/IEC 27001:2022 clauses 6.1.2 and 6.1.3
ask for a risk assessment and a treatment plan.

**Most risk registers are guesses.** A row says "likelihood 3, impact 4"
and nobody can say where either number came from. This one has an input
almost no register has: the pre-production review holds 140 findings
(indexed in `docs/REVIEW_FINDINGS.md`), and
every one of them is a risk that **already happened here**, with the
consequence demonstrated and the fix recorded. So likelihood is not
estimated for those — it is `observed`, and the evidence is a finding id
a reader can go and read.

That is the whole design:

* risks are **themes**, not one row per finding. Twenty-two of them, each
  citing the findings that realised it;
* the **state of treatment is derived**, never declared: it is a function
  of the statuses of the register rows a risk cites, and the register's
  statuses are asserted by `test_compliance_tracker_is_current.py`. A
  risk cannot claim to be treated while its evidence is `Process`;
* every cited finding, register row and Annex A control is checked to
  exist — and an Annex A control the Statement of Applicability marks
  `excluded` cannot be a treatment, because declaring a control
  inapplicable and then relying on it is the contradiction an auditor
  looks for;
* **every `Process` row in the register must appear as an untreated
  risk.** A risk assessment that omits a known gap is worse than none.
  That rule is what stops this document being an inventory of solved
  problems;
* risk **appetite, acceptance criteria and approval are placeholders**.
  Whether a residual risk is acceptable is the organisation's judgement
  and nothing else's — the same refusal the Article 30 record makes
  about a legal basis and the SoA makes about scope.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
import pathlib
import re
import sys

_ROOT = pathlib.Path(__file__).resolve().parent.parent
_TRACKER = _ROOT / 'docs/COMPLIANCE_TRACKER.md'
_READINESS = _ROOT / 'docs/REVIEW_FINDINGS.md'
_SOA_GENERATOR = _ROOT / 'tools/generate_statement_of_applicability.py'
_OUTPUT = _ROOT / 'docs/RISK_ASSESSMENT.md'

PLACEHOLDER = '`__ORGANISATION_TO_COMPLETE__`'

#: Derived states, in order of how much is left to do. Never declared by
#: hand: `state_of` computes each from the register.
STATES = ('treated', 'partial', 'open', 'operator', 'organisation')


@dataclass(frozen=True)
class Risk:
    ref: str
    name: str
    #: What could happen, in one or two sentences. Present tense.
    threat: str
    #: What happened *here*, with the finding ids as evidence. Empty when
    #: the risk is real but has not been realised in this codebase — and
    #: that distinction is the most useful column in the table.
    observed: str = ''
    findings: tuple[str, ...] = field(default_factory=tuple)
    #: Review sections that realised it, for the occurrences that are
    #: not numbered findings. Needed because several risks were realised
    #: during the goal-by-goal passes rather than the finding sweep, and
    #: without this the prose said "four times" beside one citation —
    #: which is the overclaim this document is built to prevent, made in
    #: its own first draft.
    sections: tuple[str, ...] = field(default_factory=tuple)
    #: Register rows that treat it. The state is derived from these.
    rows: tuple[str, ...] = field(default_factory=tuple)
    #: Annex A controls the treatment rests on.
    controls: tuple[str, ...] = field(default_factory=tuple)
    owner: str = 'Engineering'


def _r(ref, name, threat, observed='', findings=(), rows=(), controls=(), owner='Engineering', sections=()):
    return Risk(ref, name, threat, observed, tuple(findings), tuple(sections), tuple(rows), tuple(controls), owner)


RISKS: tuple[Risk, ...] = (
    _r(
        'R-1',
        'Access outlives the authorisation for it',
        'Somebody who has left, been deactivated or been removed from a repository keeps reaching '
        'data through a credential or a cached relationship.',
        'Four times. A deleted account kept a working 30-day token and the first request after '
        'deletion *created* an organisation for it with admin. Deactivation stopped organisation '
        'access but not the credential. The indexing access check consulted the git provider and '
        'never the membership. And the group pack — agent prompts and hook commands that run '
        'through `bash -c` — kept flowing to a deactivated member.',
        ('F122',),
        ('AC-11', 'AC-7', 'AC-8', 'AC-4'),
        ('5.18', '5.11', '6.5', '8.3', '8.10'),
        sections=('86', '89', '90', '91'),
    ),
    _r(
        'R-2',
        "One customer's data is reachable from another's session",
        "A request authenticated for one organisation returns another organisation's data.",
        "Repository reads were unscoped, including a signed URL to another organisation's "
        'source. The sweep that found it now runs over every parametrised portal endpoint.',
        ('F19', 'F29'),
        ('AC-2', 'AC-7'),
        ('8.3', '5.15'),
    ),
    _r(
        'R-3',
        "Data leaves the customer's network",
        'A self-hosted deployment makes an outbound request, so data or metadata reaches somebody '
        'the customer did not choose. For an air-gapped install this is the difference between '
        'usable and not.',
        'Twice, both silent: the CLI checked PyPI for updates, and the desktop updater called '
        'github.com on every launch while ignoring the off switch it had been given. The desktop '
        'webview then shipped with no CSP at all, and my first replacement allowed '
        '`connect-src https:` — exfiltration permitted by the control meant to prevent it.',
        ('F94', 'F126', 'F127'),
        ('DP-5',),
        ('8.12', '5.14'),
    ),
    # A 8.23 Web filtering is deliberately *not* cited here, and the
    # first draft did cite it. The SoA excludes 8.23 because there is no
    # egress to filter, so treating this risk with it would be relying
    # on a control declared inapplicable — the contradiction an auditor
    # looks for, and the generator refused to render until it was gone.
    # The product's answer to egress is elimination (DP-5), which is
    # A 8.12 and A 5.14.
    _r(
        'R-4',
        'A credential is disclosed by the system that issued it',
        'A token, password or provider secret reaches a log, a URL, a browser history or an error message.',
        'Every OAuth and SSO callback redirected with `?token=<jwt>`, putting a bearer credential '
        'in browser history and the proxy access log — and the rule against it was later found '
        "not to cover the portal's TypeScript, where the same thing had been rebuilt. A "
        'set-password endpoint replaced the account password given only a session token. An '
        'anonymous sign-up received 484 characters of database error naming every column of '
        '`users`.',
        ('F107', 'F124', 'F110', 'F120'),
        ('AC-1', 'AC-5', 'LG-9'),
        ('8.5', '5.17', '8.12'),
    ),
    _r(
        'R-5',
        'A privileged action leaves no record',
        'Something consequential happens and the audit trail cannot show who did it, so an '
        'incident cannot be reconstructed and a regulator cannot be answered.',
        'The audit policy named three URL prefixes and the routes that decide *where prompts go* '
        'sat under a fourth. Three internal endpoints recorded no target at all, including the '
        'one that mints a provider token for a named repository.',
        ('F90', 'F114'),
        ('LG-1', 'LG-2', 'LG-3', 'LG-6'),
        ('8.15', '5.28'),
    ),
    _r(
        'R-6',
        'Personal data is kept beyond its purpose, or cannot be removed',
        'A subject asks for access or erasure and the deployment cannot answer, or data sits '
        'indefinitely in a table nobody remembered.',
        'Webhook payloads logged commit messages and author emails; nothing pruned anything; '
        'erasure did not exist; a public waitlist collected personal data for a list the customer '
        'was not running, exported two of ten columns, and could not be erased.',
        ('F83', 'F93', 'F113', 'F115'),
        ('DP-6', 'DP-7', 'DP-8', 'DP-9', 'DP-13'),
        ('5.34', '8.10', '5.12', '5.33'),
    ),
    _r(
        'R-7',
        'Stored secrets are readable at rest',
        'A database dump, a backup or a stolen volume yields a secret the deployment keeps in its '
        'own database in usable form.',
        'Not realised — encryption was in place before this review, when the stored secrets were '
        "the customer's GitHub and GitLab tokens (those now belong to CodeIndex). What was missing "
        'was rotation, which meant a suspected key compromise had no remedy at all.',
        ('F106',),
        ('DP-1', 'DP-2'),
        ('8.24', '5.33'),
    ),
    _r(
        'R-8',
        'A shipped artefact has never been executed',
        'Something the product ships — a config file, a chart, a script — is careful, reviewed, '
        'covered by static checks, and has never once been run. It fails the first time a '
        'customer needs it, which is the worst possible moment.',
        'Four times, and it is the signature failure of this review. A Celery worker died on '
        'import because its `include` named a module that had moved — nothing had ever started '
        'it, which is also how it survived long enough for DEC-13 to delete it. The only TLS '
        'path the '
        'product ships was rejected by `caddy validate` from the day it was written. `helm '
        'install` failed on every cluster, always, for two reasons no amount of rendering could '
        'see. And the backup script — 130 lines, byte-identical in the chart, run by both '
        'deployment shapes — had been executed by nothing at all.',
        ('F54', 'F140'),
        ('CM-10', 'CM-1', 'DP-10', 'DP-11', 'CM-5'),
        ('8.19', '8.9', '8.29'),
        sections=('92', '93'),
    ),
    _r(
        'R-9',
        'A document asserts something that is not true',
        'A README, a compliance claim or a service contract describes the system as it was, or as '
        'somebody hoped. A customer or an auditor relies on it. For a self-hosted product the '
        'documents *are* the product for anyone deciding whether to buy it.',
        'Repeatedly, and it is why so much of this repository is generated rather than written. A '
        'README described routes that were not routes. A service contract described 60% of its '
        'own boundary. `COMPLIANCE.md` spent five passes telling customers that retention, '
        'subject access, erasure, an SBOM and the air-gap test did not exist — all five had been '
        "built. And the Statement of Applicability's first draft claimed TLS four times over "
        'evidence marked `Operator`; the generator refused to render it.',
        ('F72', 'F80', 'F88', 'F139'),
        ('CM-1', 'DP-9'),
        ('5.37', '8.26', '5.36'),
        sections=('94',),
    ),
    _r(
        'R-10',
        'A guard exists and measures nothing',
        'A test, scan or check passes because it is looking at the wrong thing, an empty set, or a '
        'page that never rendered. This is worse than no guard: it is a guard plus false '
        'confidence.',
        'The desktop e2e suite reported green while skipping every integration test, four of '
        'which had never run on any machine. The portal browser suite excluded two tests with a '
        'command-line filter, which produces no skipped test and no line in the report. A CSP '
        'attack probe reported zero violations against a page it had never rendered. And in one '
        'pass four revert-checks passed, of which three turned out to be the check being broken '
        'rather than the fix.\n\n'
        'Three more arrived together in B9, and they are the subtler variety — rules that '
        'measure the right kind of thing in the wrong shape. A rule stated in **one direction '
        'only**: "every Service selects a pod" is silent about a pod no Service selects, which '
        'is a workload running correctly with no way in. A rule asserting **a name rather than '
        'a referent**: the Ingress check compared its backend to the string `igni-portal` and '
        'never asked whether anything by that name existed. And a guard whose entire purpose '
        'was to prove a scan had measured something, written as a **hardcoded count** — it '
        'failed the day two templates were deleted, because the scan correctly measured less. '
        'All three passed while the portal was unreachable through the chart.\n\n'
        'And one written *while* documenting the pattern, which is the clearest evidence it is '
        'easy rather than careless. Two mechanisms guard the revocation window; a new test '
        'claimed to measure their combined effect and used the helper that writes the '
        'watermark column directly — which bypasses one of them. It re-tested the other, '
        'passed, and went on passing when the bypassed mechanism was removed. A test of two '
        'things that exercises one is indistinguishable from a test of two things until '
        'somebody reverts each separately, which is why every guard in this review is '
        'revert-checked one mechanism at a time.',
        ('F128', 'F130', 'F127', 'F145', 'F146'),
        ('CM-1',),
        ('8.29',),
    ),
    _r(
        'R-11',
        'A burst becomes an outage instead of a queue',
        'Load, a slow provider or one heavy query takes the whole process down rather than '
        'degrading, and the failure presents as unrelated errors everywhere.',
        'The connection pool was 2+3 per repository class — fourteen pools nobody chose — so one '
        'hot table capped the process at five concurrent queries while the worker ran 200 '
        'coroutines: 83 of 200 requests failed. A single OAuth sign-in blocked the entire API '
        'process for as long as the provider took, up to 35 seconds.',
        ('F116', 'F118', 'F119'),
        ('AV-1', 'LG-7'),
        ('8.6', '8.14'),
    ),
    _r(
        'R-12',
        'A shipped image or dependency carries a known vulnerability',
        'The product installs software with a published CVE, and nobody notices until a customer scans it.',
        'Not realised. Detection was built early and gates every build; what is missing is a '
        'commitment about how fast a finding gets fixed, which is CM-9 and cannot be automated.',
        (),
        ('CM-2', 'CM-3', 'CM-8', 'CM-9'),
        ('8.8', '5.7', '8.7', '5.21'),
    ),
    _r(
        'R-13',
        'Backups are absent, unverified, or unrestorable',
        'The deployment loses its database and the recovery does not work, or there is nothing to recover from.',
        'The restore itself was sound when first drilled — but the drill was one afternoon by '
        'hand, and it never used the script the product actually ships, which had been executed '
        'by nothing. Reading what came back from a restored database is also what found a live '
        'admin account left behind by a dev flag.',
        ('F87',),
        ('DP-10', 'DP-11', 'DP-12'),
        ('8.13', '5.29', '5.30'),
        sections=('39', '93'),
    ),
    _r(
        'R-14',
        'A migration cannot be undone',
        'An upgrade goes wrong and the schema cannot be rolled back, so recovery means restoring '
        'a backup and losing everything since.',
        'A full `downgrade base` could not be rolled forward again.',
        ('F3',),
        ('CM-7', 'CM-1'),
        ('8.32',),
    ),
    _r(
        'R-15',
        'An unsafe configuration reaches production',
        'A development affordance, a missing secret or a permissive default is deployed, and the '
        'deployment is insecure while appearing to work.',
        'An unset HMAC signing secret was a full authentication bypass; tokens are now signed with '
        'a key the server generates. `PORTAL_AUTH_BYPASS` had '
        'nothing stopping it reaching production, and it wrote a live admin row that outlived the '
        'flag — found by reading a restored backup, and now refused at startup.',
        ('F5', 'F6', 'F87'),
        ('CM-5', 'CM-6', 'AC-4'),
        ('8.9', '8.31', '8.27'),
    ),
    _r(
        'R-16',
        'A container runs with more privilege than it needs',
        'A compromise inside one container becomes a compromise of the host or the cluster.',
        'Not realised. Non-root, read-only root filesystem, dropped capabilities and no mounted '
        'service-account token were in place or added early, and the chart asserts them on the '
        'rendered manifests.',
        (),
        ('CM-4', 'CM-10'),
        ('8.18', '8.9'),
    ),
    _r(
        'R-17',
        'Two services drift apart at their boundary',
        'Two repositories that have to agree stop agreeing, and the failure appears as something '
        'else entirely — a wrong diagnosis rather than an error.',
        'The service contract described 60% of the real surface. Then the whole internal API '
        'turned out to be unreachable, in both directions, since the day it was written: both '
        'sides served `/v1/internal/v1/...` and both clients sent `/internal/v1/...`. code-index '
        'maps that 404 to "repository not linked", so a misconfigured URL presented as a '
        'confident, wrong diagnosis about a repository that was linked.',
        ('F88',),
        ('CM-1',),
        ('8.26', '8.32', '8.21'),
        sections=('90',),
    ),
    _r(
        'R-18',
        'Clocks disagree and a control silently stops working',
        'Two processes compare timestamps they each made. When their clocks differ, a security '
        'decision comes out wrong and nothing reports it.',
        "Session revocation compared a token's `iat` against a watermark and both were app "
        'clocks: a replica five seconds ahead minted tokens that survived a withdrawal, '
        "demonstrated against the real code. The audit trail's keyset pagination and "
        "retention's windows depended on the same clocks.",
        (),
        ('LG-7', 'AC-11'),
        ('8.17',),
        sections=('91',),
    ),
    _r(
        'R-19',
        'An incident happens and nobody knows what to do',
        'A breach or a suspected compromise occurs and there is no plan: no named roles, no '
        'notification path, no decision about when a regulator is told.',
        'Not realised, and untreated. Every prerequisite is built — an immutable audit trail, an '
        'export for a SIEM, alerting, and the levers a response needs — but the plan itself is '
        'LG-8 and needs people, not code.',
        (),
        ('LG-8', 'LG-1', 'LG-4'),
        ('5.24', '5.26', '5.27'),
        'Security',
    ),
    _r(
        'R-20',
        "The deployment's own infrastructure is misconfigured",
        "TLS, disk encryption, network policy or off-host backups are the customer's to arrange, "
        'and a deployment can be running and exposed at the same time.',
        "The product's half of this was found broken — the TLS terminator it ships never "
        "started — and is fixed and validated in CI. What remains is genuinely the operator's: "
        "the DNS record, the certificate's trust chain, volume encryption, and getting backups "
        'off the host.',
        ('F140',),
        ('DP-3', 'DP-4', 'DP-12'),
        ('8.20', '8.21', '8.22', '7.10'),
        'Operations',
        sections=('87',),
    ),
    _r(
        'R-21',
        'The interface excludes people who use assistive technology',
        'A procurement requirement fails, and separately, somebody cannot use the product. Annex A '
        'has no control for this, which is why it is here: a risk register organised only around '
        'ISO would not contain it.',
        '354 violation nodes across the two clients when anybody first looked — 200 in the '
        "portal, 154 in the desktop app's dark theme, and 52 more once the light theme was "
        'checked. Three primary buttons sat at 2.26 to 2.80:1 and axe reported them as '
        '*incomplete* rather than failing, so they were found by computing the ratio by hand.',
        ('F137', 'F138'),
        ('AX-1',),
        (),
        'Product',
    ),
    _r(
        'R-22',
        "The vendor's own organisation is unassessed",
        'Everything above is about the software. The organisation that builds it has policies, '
        'staff, suppliers and offices, and none of that is visible from here. For a customer '
        'evaluating the product this is the largest unquantified risk, and it is the one this '
        'repository can say least about.',
        'Not applicable — a repository cannot observe its own organisation.',
        (),
        ('OR-1', 'OR-2', 'OR-3', 'OR-4', 'OR-5', 'OR-6', 'OR-7'),
        ('5.1', '5.2', '5.4', '5.19', '5.35', '6.1', '6.3'),
        'Security',
    ),
)


def _soa_controls() -> dict[str, str]:
    """Annex A reference -> disposition, from the SoA's own generator."""
    import importlib.util

    spec = importlib.util.spec_from_file_location('soa_for_risk', _SOA_GENERATOR)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules['soa_for_risk'] = module
    spec.loader.exec_module(module)
    return {c.ref: c.disposition for c in module.ALL_CONTROLS}


def register_rows() -> dict[str, str]:
    rows: dict[str, str] = {}
    for match in re.finditer(
        r'^\|\s*([A-Z]{2}-\d+)\s*\|([^|]*)\|([^|]*)\|\s*`?([A-Za-z]+)`?\s*\|', _TRACKER.read_text(), re.M
    ):
        rows[match.group(1)] = match.group(4)
    return rows


def findings() -> set[str]:
    """Every finding id `REVIEW_FINDINGS.md` indexes."""
    return set(re.findall(r'^\| (F\d+) \|', _READINESS.read_text(), re.M))


def state_of(risk: Risk, statuses: dict[str, str]) -> str:
    """Derived, never declared.

    A risk's state is a function of the register rows it cites, and those
    statuses are asserted elsewhere. Declaring it by hand would make this
    document the one place in the chain that can lie.
    """
    if not risk.rows:
        return 'organisation'
    cited = [statuses.get(row, 'missing') for row in risk.rows]
    if all(s == 'Verified' for s in cited):
        return 'treated'
    if all(s == 'Operator' for s in cited):
        return 'operator'
    if all(s == 'Process' for s in cited):
        return 'organisation'
    if any(s == 'Verified' for s in cited):
        return 'partial'
    return 'open'


def unknown_findings() -> list[str]:
    known = findings()
    return sorted({f for r in RISKS for f in r.findings if f not in known})


def review_sections() -> set[str]:
    """Every numbered review section `REVIEW_FINDINGS.md` indexes."""
    return set(re.findall(r'^\| §(\d+) \|', _READINESS.read_text(), re.M))


def unknown_sections() -> list[str]:
    known = review_sections()
    return sorted({s for r in RISKS for s in r.sections if s not in known})


#: Words a justification uses to count occurrences, and what each means.
#: A risk whose prose says "four times" must cite four pieces of
#: evidence — the check that caught R-1 claiming four with one citation
#: and R-18 reading "not realised" beside the word "demonstrated".
_COUNT_WORDS = {'once': 1, 'twice': 2, 'three times': 3, 'four times': 4, 'five times': 5}


def understated_evidence() -> list[str]:
    offenders = []
    for risk in RISKS:
        evidence = len(risk.findings) + len(risk.sections)
        lowered = risk.observed.lower()
        for word, count in _COUNT_WORDS.items():
            if word in lowered and evidence < count:
                offenders.append(f'{risk.ref} says "{word}" and cites {evidence} piece(s) of evidence')
    return sorted(offenders)


def unclaimed_observations() -> list[str]:
    """Prose describing something that happened, with nothing cited.

    "demonstrated", "found", "was broken" are claims about events. An
    event with no id is not checkable, and R-18 shipped in the first
    draft reading `not realised` in the table while its own prose said
    the failure had been demonstrated.
    """
    offenders = []
    for risk in RISKS:
        if risk.findings or risk.sections:
            continue
        lowered = risk.observed.lower()
        if lowered.startswith('not realised') or lowered.startswith('not applicable'):
            continue
        for word in ('demonstrated', 'was found', 'were found', 'happened'):
            if word in lowered:
                offenders.append(f'{risk.ref} describes an event ({word!r}) and cites nothing')
                break
    return sorted(offenders)


def unknown_rows() -> list[str]:
    known = register_rows()
    return sorted({row for r in RISKS for row in r.rows if row not in known})


def unknown_controls() -> list[str]:
    known = _soa_controls()
    return sorted({c for r in RISKS for c in r.controls if c not in known})


def excluded_controls_relied_on() -> list[str]:
    """A treatment resting on a control the SoA declared inapplicable.

    The contradiction an auditor looks for: you cannot exclude a control
    and then treat a risk with it.
    """
    dispositions = _soa_controls()
    offenders = []
    for risk in RISKS:
        for control in risk.controls:
            if dispositions.get(control) == 'excluded':
                offenders.append(f'{risk.ref} treats a risk with A {control}, which the SoA excludes')
    return sorted(offenders)


def untreated_process_rows() -> list[str]:
    """`Process` rows no risk mentions.

    The rule that stops this being an inventory of solved problems: a
    known gap in the register has to appear here as a risk somebody owns.
    """
    process = {row for row, status in register_rows().items() if status == 'Process'}
    cited = {row for r in RISKS for row in r.rows}
    return sorted(process - cited)


def render() -> str:
    statuses = register_rows()
    states = {r.ref: state_of(r, statuses) for r in RISKS}
    counts = {s: sum(1 for v in states.values() if v == s) for s in STATES}
    finding_count = len(findings())
    observed = sum(1 for r in RISKS if r.findings or r.sections)

    out: list[str] = []
    out.append('# Risk assessment and treatment plan')
    out.append('')
    out.append('**GENERATED by tools/generate_risk_assessment.py — do not edit.**')
    out.append('Regenerate with `python tools/generate_risk_assessment.py`;')
    out.append('`tests/test_risk_assessment.py` fails if the committed file and the generator')
    out.append('disagree.')
    out.append('')
    out.append('ISO/IEC 27001:2022 clauses 6.1.2 and 6.1.3. Companion to')
    out.append('[`STATEMENT_OF_APPLICABILITY.md`](STATEMENT_OF_APPLICABILITY.md), which says')
    out.append('which controls apply; this says what they are for.')
    out.append('')
    out.append('## Method, and why this register is unusual')
    out.append('')
    out.append('Most risk registers are estimates. A row says "likelihood 3, impact 4" and')
    out.append('nobody can say where either number came from, so the arithmetic that follows is')
    out.append('decoration.')
    out.append('')
    out.append('This one has an input almost none have. The pre-production review recorded')
    out.append(f'**{finding_count} findings**, and each is a risk that already happened in this')
    out.append('codebase, with the consequence demonstrated and the fix recorded. So for')
    out.append(f'{observed} of the {len(RISKS)} risks below, likelihood is not estimated — it is')
    out.append('**observed**, and the evidence is a finding id, indexed in')
    out.append('[REVIEW_FINDINGS.md](REVIEW_FINDINGS.md) with the tests that guard it.')
    out.append('')
    out.append('That changes what the register is for. It is not a prediction exercise; it is a')
    out.append('statement of what this software has actually been caught doing, what was done')
    out.append('about it, and what is left. The rows with no findings are the honest other half:')
    out.append('risks that are real and have not been realised here, which is a weaker claim and')
    out.append('is marked as one.')
    out.append('')
    out.append('**Treatment state is derived, not asserted.** Each risk cites rows of')
    out.append('[`COMPLIANCE_TRACKER.md`](COMPLIANCE_TRACKER.md), whose statuses are themselves')
    out.append('held by tests. A risk showing `treated` means every row it cites is `Verified`.')
    out.append('It is not possible for this document to claim a risk is handled while its own')
    out.append('evidence says otherwise.')
    out.append('')
    out.append('| Field | Value |')
    out.append('|---|---|')
    out.append(f'| Risk appetite | {PLACEHOLDER} |')
    out.append(f'| Acceptance criteria (when is a residual risk tolerable) | {PLACEHOLDER} |')
    out.append(f'| Scale and scoring method, if the organisation wants one | {PLACEHOLDER} |')
    out.append(f'| Review interval | {PLACEHOLDER} |')
    out.append(f'| Risk owner (accountable) | {PLACEHOLDER} |')
    out.append(f'| Approved by | {PLACEHOLDER} |')
    out.append(f'| Date of approval | {PLACEHOLDER} |')
    out.append('')
    out.append('Those seven are deliberately blank. Whether a residual risk is *acceptable* is a')
    out.append('judgement about a business, and a vendor filling it in would be deciding')
    out.append("something on a customer's behalf — the same refusal the Article 30 record makes")
    out.append('about a legal basis.')
    out.append('')
    out.append('| State | Count | Meaning |')
    out.append('|---|---|---|')
    for state, meaning in (
        ('treated', 'Every register row this risk cites is `Verified`.'),
        ('partial', 'Some evidence is verified and some is not. The gap is named in the plan below.'),
        ('open', 'Cited evidence exists and none of it is verified.'),
        ('operator', "Treated by decisions in the customer's own deployment."),
        ('organisation', 'Needs people, policy or signatures. No code can close it.'),
    ):
        out.append(f'| `{state}` | {counts[state]} | {meaning} |')
    out.append('')

    out.append('## The register')
    out.append('')
    out.append('| # | Risk | Observed here | State | Treatment | Annex A | Owner |')
    out.append('|---|---|---|---|---|---|---|')
    for risk in RISKS:
        evidence = [f'`{f}`' for f in risk.findings] + [f'§{s}' for s in risk.sections]
        seen = ', '.join(evidence) if evidence else 'not realised'
        rows = ', '.join(f'`{row}`' for row in risk.rows) if risk.rows else '—'
        controls = ', '.join(f'A {c}' for c in risk.controls) if risk.controls else '— (none applies)'
        out.append(
            f'| {risk.ref} | **{risk.name}.** {risk.threat} | {seen} | `{states[risk.ref]}` '
            f'| {rows} | {controls} | {risk.owner} |'
        )
    out.append('')

    out.append('## What each one has actually done here')
    out.append('')
    out.append('The impact column of a normal risk register is where the invention happens. These')
    out.append('are not estimates.')
    out.append('')
    for risk in RISKS:
        out.append(f'### {risk.ref} — {risk.name}')
        out.append('')
        out.append(f'**State:** `{states[risk.ref]}`. **Owner:** {risk.owner}.')
        if risk.findings or risk.sections:
            cited = list(risk.findings) + [f'§{s}' for s in risk.sections]
            out.append(f'**Realised as:** {", ".join(cited)}.')
        out.append('')
        out.append(risk.observed if risk.observed else 'No observation recorded.')
        out.append('')

    out.append('## Treatment plan — what is left')
    out.append('')
    out.append('Only the risks that are not `treated`. A treatment plan listing solved problems')
    out.append('is a progress report, not a plan.')
    out.append('')
    out.append('| # | Risk | State | What remains | Owner |')
    out.append('|---|---|---|---|---|')
    for risk in RISKS:
        state = states[risk.ref]
        if state == 'treated':
            continue
        unfinished = [
            f'`{row}` ({statuses.get(row, "missing")})' for row in risk.rows if statuses.get(row) != 'Verified'
        ]
        remaining = ', '.join(unfinished) if unfinished else 'nothing in the register; see the note above'
        out.append(f'| {risk.ref} | {risk.name} | `{state}` | {remaining} | {risk.owner} |')
    out.append('')
    out.append('Nothing in that table can be closed by code. `Process` rows are policy documents,')
    out.append('an incident plan and a remediation commitment; `Operator` rows are decisions in')
    out.append("the customer's own infrastructure that a self-hosted product must leave to them.")
    out.append('Where each is tracked:')
    out.append('')
    out.append('| Row | Tracked as |')
    out.append('|---|---|')
    out.append('| `LG-8` | C4 — the incident response plan |')
    out.append('| `CM-9` | C6 — a vulnerability remediation SLA |')
    out.append('| `OR-1` | C1 (the SoA is built) and this document; the ISMS scope statement remains |')
    out.append('| `OR-2` | C3 — policies |')
    out.append('| `OR-3` | Awareness training |')
    out.append('| `OR-4` | C7 — vendor and sub-processor register |')
    out.append('| `OR-5`, `OR-7` | Internal audit, and an auditor |')
    out.append('| `OR-6` | C5 — a DPIA |')
    out.append("| `DP-3`, `DP-4`, `DP-12` | The operator's: disk encryption, TLS trust, off-host backups |")
    out.append('')
    out.append('## What this assessment does not cover')
    out.append('')
    out.append('**Risks nobody has thought of.** 140 findings is evidence that this codebase has')
    out.append('been looked at hard, and no evidence at all about what a look from a different')
    out.append('angle would find. Every one of those findings was invisible until somebody ran')
    out.append('the thing rather than reading it.')
    out.append('')
    out.append('**The organisation.** R-22 is one row for what is, for a customer evaluating this')
    out.append('product, the largest unquantified risk on the page. A repository cannot assess')
    out.append('its own company.')
    out.append('')
    out.append('**Quantification.** There are no scores, and adding them would make this document')
    out.append('worse: a likelihood for an observed event is 1, and an impact number for "another')
    out.append('organisation\'s source code was reachable" is a decision about a business, not a')
    out.append('measurement. The scale field above is there for an organisation that needs one.')
    out.append('')
    return '\n'.join(out)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='exit non-zero if the file is stale')
    args = parser.parse_args()

    for label, offenders in (
        ('cite findings that do not exist', unknown_findings()),
        ('cite review sections that do not exist', unknown_sections()),
        ('count more occurrences than they cite', understated_evidence()),
        ('describe an event without citing it', unclaimed_observations()),
        ('cite register rows that do not exist', unknown_rows()),
        ('cite Annex A controls the SoA does not have', unknown_controls()),
        ('rely on a control the SoA excludes', excluded_controls_relied_on()),
    ):
        if offenders:
            print(f'these {label}: {offenders}', file=sys.stderr)
            return 2

    omitted = untreated_process_rows()
    if omitted:
        print(f'these `Process` rows are known gaps that no risk mentions: {omitted}', file=sys.stderr)
        return 2

    rendered = render()
    if args.check:
        if not _OUTPUT.exists() or _OUTPUT.read_text() != rendered:
            print(f'{_OUTPUT} is stale; regenerate it', file=sys.stderr)
            return 1
        print(f'{_OUTPUT} is current')
        return 0

    _OUTPUT.write_text(rendered)
    print(f'wrote {_OUTPUT} ({len(rendered.splitlines())} lines, {len(RISKS)} risks)')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
