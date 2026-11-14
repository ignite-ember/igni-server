"""Generate `docs/DPIA.md` — the vendor's half of a customer's DPIA.

C5, OR-6. GDPR Article 35 asks a **controller** to carry out a data
protection impact assessment where processing is likely to result in a
high risk to people.

**The vendor is neither controller nor processor**, and that changes the
whole document. The software runs in the customer's own infrastructure,
no data reaches the vendor, and there is no Article 28 processing
agreement to write because there is no processing to contract for
(DP-5). So this is not a DPIA. A DPIA is something the customer carries
out, about their purposes, against their risk appetite.

What a vendor *can* supply is the half a controller cannot write without
us:

* **Article 35(7)(a)**, the systematic description of the processing —
  derived from the same activity list and column classification the
  Article 30 record uses, so it cannot omit a table the schema has;
* **Article 35(7)(d)**, the measures — cited to register rows that are
  `Verified`, so a safeguard claimed here is one something asserts;
* a **screening** against Article 35(3) and the EDPB's criteria, stating
  which are met by the software as shipped and which depend on how the
  customer uses it.

Everything that is a judgement about a business is a placeholder:
necessity, proportionality, whether the residual risk is acceptable, and
the conclusion of the screening itself.

The most useful thing in it is probably the data-flow section. The
software makes no outbound request of its own — but the model endpoint
is a destination the *customer* configures, and
`InferenceBackend.base_url` accepts any absolute URL. A customer who
points it at a third-party API has a processor relationship to assess,
and it is theirs rather than ours.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
import importlib.util
import pathlib
import re
import sys

_ROOT = pathlib.Path(__file__).resolve().parent.parent
_TRACKER = _ROOT / 'docs/COMPLIANCE_TRACKER.md'
_READINESS = _ROOT / 'docs/REVIEW_FINDINGS.md'
_ROP_GENERATOR = _ROOT / 'tools/generate_records_of_processing.py'
_SCHEMAS = _ROOT / 'app/services/ai/schemas.py'
_OUTPUT = _ROOT / 'docs/DPIA.md'

PLACEHOLDER = '`__CONTROLLER_TO_COMPLETE__`'


#: Article 35(3) and the EDPB's nine criteria, each with what the
#: software does and a verdict. `met` is about the software as shipped;
#: `depends` means the answer is a property of the customer's use.
@dataclass(frozen=True)
class Criterion:
    ref: str
    name: str
    verdict: str  # 'no' | 'depends' | 'yes'
    finding: str


CRITERIA: tuple[Criterion, ...] = (
    Criterion(
        '35(3)(a)',
        'Automated evaluation or profiling with legal or similarly significant effects',
        'no',
        'The software makes no decision about a person. It routes prompts and records who did '
        'what; nothing scores, ranks or evaluates anybody, and no output of it '
        'produces a legal effect. A customer who used the audit trail or the usage accounting to '
        'appraise developers would be doing something the software does not do — and that use is '
        "the controller's to assess.",
    ),
    Criterion(
        '35(3)(b)',
        'Large-scale processing of special categories, or of criminal-conviction data',
        'no',
        'No field in the schema is intended for an Article 9 category or an Article 10 conviction. '
        'The classification behind the Article 30 record covers every column of every personal-data '
        'table and holds names, addresses, roles and timestamps. **The honest caveat is free '
        'text**: a group entry or a notification carries whatever somebody typed, and the '
        'software cannot know what that is — which is exactly why erasure works by redacting a '
        'named person rather than by trusting a schema (DP-7).',
    ),
    Criterion(
        '35(3)(c)',
        'Systematic monitoring of a publicly accessible area',
        'no',
        'There is no public area and no monitoring of one. Every surface is authenticated.',
    ),
    Criterion(
        'EDPB-1',
        'Evaluation or scoring',
        'no',
        'Nothing here scores a person. Usage accounting counts requests and tokens against an '
        'organisation, not a judgement about a developer.',
    ),
    Criterion(
        'EDPB-2',
        'Automated decision-making with legal or similar effect',
        'no',
        'The model makes suggestions about *code*, and a person decides whether to take them. '
        'No output is a decision about a person, and none is applied automatically: a hook can '
        "run a command on the developer's own machine, which is a decision about their "
        'workspace rather than about them. The distinction matters because the answer would '
        'change if a customer wired the audit trail into a performance process — and that is '
        "the controller's use, not the software's function.",
    ),
    Criterion(
        'EDPB-3',
        'Systematic monitoring',
        'depends',
        '**This is the one to think about.** The software records every privileged action with an '
        'actor, every authentication attempt including failures, and every request served against '
        'the organisation that made it. That is a security audit trail and it is required by other '
        'parts of this pack (LG-1 to LG-4) — and it is also, unavoidably, a record of what '
        'identifiable employees did at work, retained for as long as `AUDIT_RETENTION_DAYS` says. '
        'Several supervisory authorities list systematic monitoring of employees among the '
        'operations requiring a DPIA. Whether this deployment amounts to that depends on how the '
        'customer uses and retains it.',
    ),
    Criterion(
        'EDPB-4',
        'Sensitive data or data of a highly personal nature',
        'depends',
        'Not by design — see 35(3)(b). But the prompts that pass through carry source code, which '
        'is commercially sensitive and can contain personal data belonging to third parties that '
        'nobody classified.',
    ),
    Criterion(
        'EDPB-5',
        'Data processed on a large scale',
        'depends',
        'A function of the deployment. One team is not large scale; an engineering organisation of '
        'several thousand, with every request recorded, plausibly is.',
    ),
    Criterion(
        'EDPB-6',
        'Matching or combining datasets',
        'no',
        "Not here. Linking a person's account to their git provider identities is CodeIndex's "
        '(igni__code-index), which connects repositories; this server holds the account and '
        'nothing it is matched against.',
    ),
    Criterion(
        'EDPB-7',
        'Data concerning vulnerable subjects',
        'depends',
        'Employees are treated as vulnerable subjects by several authorities, on the grounds that '
        "consent to an employer is rarely freely given. Every data subject here is somebody's "
        'employee or contractor.',
    ),
    Criterion(
        'EDPB-8',
        'Innovative use or new technological solutions',
        'yes',
        "A large language model reads the customer's source code and generates changes to it. "
        "Whatever else is arguable, this criterion is met, and on the EDPB's guidance two met "
        'criteria are the point at which a DPIA is expected.',
    ),
    Criterion(
        'EDPB-9',
        'Processing that prevents subjects exercising a right',
        'no',
        'The opposite: subject access, erasure and retention are all built and asserted (DP-6, '
        'DP-7, DP-8), and a person can withdraw their own sessions without asking an '
        'administrator (AC-11).',
    ),
)


@dataclass(frozen=True)
class SubjectRisk:
    ref: str
    #: The harm, described from the data subject's side rather than the
    #: system's. That is what Article 35(7)(c) asks for and it is the
    #: part a security-shaped risk register does not answer.
    harm: str
    observed: str = ''
    findings: tuple[str, ...] = field(default_factory=tuple)
    rows: tuple[str, ...] = field(default_factory=tuple)


SUBJECT_RISKS: tuple[SubjectRisk, ...] = (
    SubjectRisk(
        'S-1',
        "A developer's commit messages and email address are readable by anybody with access to "
        "the deployment's logs — including people with no need to see them, and log aggregation "
        'systems the controller may not have assessed.',
        "Realised. Every accepted webhook logged the commit message and the author's email, and "
        "the chat endpoint logged the prompt — which is the developer's own code and words. Both "
        'are shape-only now, enforced by an AST rule over the whole application rather than by a '
        'corrected call site.',
        ('F83', 'F111'),
        ('LG-5',),
    ),
    SubjectRisk(
        'S-2',
        "A person's data is kept indefinitely, so a subject-access request years later returns "
        'material nobody intended to keep and a deletion request cannot be honoured.',
        'Realised. Nothing pruned anything: `audit_events` and `webhook_events` grew without '
        'limit, and the settings that looked like retention were settings nobody applied. A '
        'runner applies them now, and the default is still "keep everything" — deliberately, '
        "because discarding a customer's history by surprise would be worse, and because the "
        "window is the controller's decision.",
        ('F93',),
        ('DP-6', 'DP-11'),
    ),
    SubjectRisk(
        'S-3',
        'A person cannot find out what the deployment holds about them, or cannot have it removed '
        '— so a right they have on paper does not exist in practice.',
        'Realised, twice over. Subject access did not exist; when it did, its coverage was a '
        'hand-maintained list that returned four columns it never named and omitted eight it '
        'claimed. Erasure did not exist either. Both are asserted against the live schema now, so '
        'a migration that adds a personal-data column fails the build until somebody classifies '
        'it.',
        ('F113', 'F115', 'F121'),
        ('DP-7', 'DP-8', 'DP-9'),
    ),
    SubjectRisk(
        'S-4',
        "A person's own words and code are disclosed to somebody in a different organisation.",
        'Realised. Repository reads were unscoped — including a signed URL to another '
        "organisation's source, which is the code a developer wrote. Swept from the live router "
        'now, over every parametrised endpoint.',
        ('F19', 'F29'),
        ('AC-2',),
    ),
    SubjectRisk(
        'S-5',
        "A person who has left keeps access to their former employer's systems, and their "
        'continued activity is attributed to them.',
        'Realised four times, and each was a different path: a deleted account whose token still '
        'worked, deactivation that did not touch the credential, an access check that asked the '
        'git provider and never the membership, and a group pack that kept flowing to a '
        'deactivated member.',
        ('F122',),
        ('AC-11',),
    ),
    SubjectRisk(
        'S-6',
        "A person's source code leaves the organisation entirely, to a model provider or an "
        'update service they never chose.',
        'Realised twice for metadata — the CLI checked PyPI on startup and the desktop updater '
        'called github.com on every launch, both silently — and the software now makes no '
        'outbound request of its own. **The model endpoint is a different matter and it is the '
        "controller's**: see the data-flow section.",
        ('F94', 'F126'),
        ('DP-5',),
    ),
)


def _rop():
    spec = importlib.util.spec_from_file_location('rop_for_dpia', _ROP_GENERATOR)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules['rop_for_dpia'] = module
    spec.loader.exec_module(module)
    return module


def register_rows() -> dict[str, str]:
    rows: dict[str, str] = {}
    for match in re.finditer(
        r'^\|\s*([A-Z]{2}-\d+)\s*\|([^|]*)\|([^|]*)\|\s*`?([A-Za-z]+)`?\s*\|', _TRACKER.read_text(), re.M
    ):
        rows[match.group(1)] = match.group(4)
    return rows


def findings() -> set[str]:
    return set(re.findall(r'^\| (F\d+) \|', _READINESS.read_text(), re.M))


def unknown_findings() -> list[str]:
    known = findings()
    return sorted({f for r in SUBJECT_RISKS for f in r.findings if f not in known})


def unknown_rows() -> list[str]:
    known = register_rows()
    return sorted({row for r in SUBJECT_RISKS for row in r.rows if row not in known})


def unverified_rows() -> list[str]:
    """A measure claimed as a safeguard must be one something asserts."""
    statuses = register_rows()
    offenders = []
    for risk in SUBJECT_RISKS:
        for row in risk.rows:
            if statuses.get(row) != 'Verified':
                offenders.append(f'{risk.ref} names {row} as a measure, and it is {statuses.get(row, "missing")}')
    return sorted(offenders)


def base_url_is_still_unrestricted() -> bool:
    """Whether `InferenceBackend.base_url` still accepts any host.

    The data-flow section says a customer can point inference at a
    third party, and that claim has to track the code. If somebody
    restricts the URL to private ranges, the document is wrong and this
    is what says so.
    """
    source = _SCHEMAS.read_text()
    if 'base_url must start with http:// or https://' not in source:
        return False
    for marker in ('is_private', 'ip_address(', 'RFC1918', 'loopback'):
        if marker in source:
            return False
    return True


def risks_without_a_harm() -> list[str]:
    """Article 35(7)(c) asks about risks *to the rights and freedoms of
    natural persons*. A row describing a system failure rather than a
    harm to somebody has answered a different question.
    """
    offenders = []
    for risk in SUBJECT_RISKS:
        if len(risk.harm) < 60:
            offenders.append(f'{risk.ref} has no real harm statement')
        lowered = risk.harm.lower()
        if not any(word in lowered for word in ('person', 'developer', 'author', 'somebody', 'their', 'they')):
            offenders.append(f'{risk.ref} describes a system failure rather than a harm to somebody')
    return sorted(offenders)


def render() -> str:
    rop = _rop()
    statuses = register_rows()
    met = [c for c in CRITERIA if c.verdict == 'yes']
    depends = [c for c in CRITERIA if c.verdict == 'depends']

    out: list[str] = []
    out.append("# Data protection impact assessment — the vendor's half")
    out.append('')
    out.append('**GENERATED by tools/generate_dpia.py — do not edit.**')
    out.append('Regenerate with `make docs`; `tests/test_dpia.py` fails if the committed file and')
    out.append('the generator disagree.')
    out.append('')
    out.append('## This is not a DPIA')
    out.append('')
    out.append('GDPR Article 35 asks a **controller** to carry one out, about their purposes,')
    out.append('against their own risk appetite. This document cannot be that, and the reason is')
    out.append("the unusual part of this product's position:")
    out.append('')
    out.append('* **the customer is the controller.** They decide why the software is deployed and')
    out.append('  what happens to what it stores;')
    out.append("* **the vendor is not a processor.** The software runs entirely in the customer's")
    out.append('  infrastructure and no data reaches the vendor — asserted by rules in two')
    out.append('  repositories after two outbound calls were found and removed (DP-5). There is no')
    out.append('  Article 28 agreement to write because there is no processing to contract for;')
    out.append('* **so there is no third party here at all**, unless the customer adds one. That')
    out.append('  is the data-flow section below, and it is the most important page for a DPIA.')
    out.append('')
    out.append('What follows is the half a controller cannot write without the vendor: the')
    out.append('systematic description under Article 35(7)(a), the measures under 35(7)(d), a')
    out.append('screening against Article 35(3), and the risks to data subjects that this')
    out.append('software has actually been observed to create. Lift it into your own assessment.')
    out.append('')
    out.append('| Field | Value |')
    out.append('|---|---|')
    for name in (
        'Controller',
        'Data protection officer, if appointed',
        'Purposes of the processing (35(7)(a))',
        'Legal basis for each activity',
        'Necessity and proportionality assessment (35(7)(b))',
        'Whether a DPIA is required for this deployment',
        'Residual risk: acceptable or not (35(7)(d))',
        'Prior consultation with the supervisory authority, if required (36)',
        'Approved by',
        'Date of approval',
        'Review date',
    ):
        out.append(f'| {name} | {PLACEHOLDER} |')
    out.append('')
    out.append('Eleven fields, and the first two are why this cannot be finished here: a')
    out.append('vendor-completed DPIA would be inventing a purpose and then assessing its own')
    out.append('invention. The Article 30 record refuses to fill in a legal basis for the same')
    out.append('reason.')
    out.append('')

    # ── Screening ────────────────────────────────────────────────────
    out.append('## Screening: is a DPIA required?')
    out.append('')
    out.append("Article 35(3) lists three cases, and the EDPB's guidelines add nine criteria with")
    out.append('the rule of thumb that **two met criteria mean a DPIA is expected**. Each row')
    out.append('below is the software as shipped. `depends` means the answer is a property of how')
    out.append("the customer uses it, which is the controller's to determine.")
    out.append('')
    out.append('| Criterion | | Verdict |')
    out.append('|---|---|---|')
    for criterion in CRITERIA:
        out.append(f'| {criterion.ref} | {criterion.name} | `{criterion.verdict}` |')
    out.append('')
    out.append(f"**{len(met)} met, {len(depends)} depending on use.** The vendor's reading, offered")
    out.append('as an input and not as a determination: **a DPIA is likely to be required.**')
    out.append("EDPB-8 is met outright — a large language model reads the customer's source code")
    out.append('and generates changes to it, which is innovative use of new technology by any')
    out.append('reading. And EDPB-3, systematic monitoring, is met the moment an audit trail of')
    out.append("identifiable employees' actions at work is retained, which the rest of this")
    out.append('compliance pack requires. Two criteria is the threshold.')
    out.append('')
    out.append('That conclusion is deliberately uncomfortable. The controls that make this')
    out.append('product auditable — LG-1 to LG-4, an immutable record of who did what — are the')
    out.append('same controls that make it a monitoring system. A DPIA is where those two facts')
    out.append('get reconciled, and it is the controller who has to do it.')
    out.append('')
    for criterion in CRITERIA:
        out.append(f'### {criterion.ref} — {criterion.name}')
        out.append('')
        out.append(f'**`{criterion.verdict}`.** {criterion.finding}')
        out.append('')

    # ── 35(7)(a) ─────────────────────────────────────────────────────
    out.append('## Article 35(7)(a) — systematic description of the processing')
    out.append('')
    out.append('Derived from the same activity list and column classification as')
    out.append('[`RECORDS_OF_PROCESSING.md`](RECORDS_OF_PROCESSING.md), which is generated from')
    out.append('the live schema. So this cannot omit a table the software has, and a migration')
    out.append('that adds one fails the build until somebody classifies it.')
    out.append('')
    out.append('| Activity | Data subjects | Tables | Retention |')
    out.append('|---|---|---|---|')
    for activity in rop.ACTIVITIES:
        tables = f'{len(activity.tables)}' + (' (JSON payload)' if activity.json_payload else '')
        out.append(f'| {activity.name} | {activity.subjects} | {tables} | {activity.retention} |')
    out.append('')
    out.append('The column-by-column categories are in the Article 30 record rather than repeated')
    out.append('here, because two copies of a list of columns is one copy that goes stale.')
    out.append('')

    # ── Data flows ───────────────────────────────────────────────────
    out.append('## Where the data goes — the section a DPIA needs most')
    out.append('')
    out.append('The server makes **no outbound request of its own**. That is DP-5, it is')
    out.append('asserted by a source scan and a runtime test in each repository, and it was false')
    out.append('twice before those rules existed. But a deployment is not a closed system, and the')
    out.append('destinations below are ones the *customer* configures or can switch off. Each is a')
    out.append('controller decision, and some of them may be processor relationships the customer')
    out.append('has to paper.')
    out.append('')
    out.append('| Destination | What reaches it | Whose decision |')
    out.append('|---|---|---|')
    for destination, reaches, whose in (
        (
            "The deployment's own Postgres and Redis",
            'Everything in the Article 30 record',
            "The customer's infrastructure",
        ),
        (
            '**The model endpoint**',
            "**Prompts, which contain the developer's source code and their words**",
            '**The customer, in `InferenceBackend.base_url`**',
        ),
        (
            'An SMTP relay, if configured',
            'Sign-in codes and invitations, containing an address',
            'The customer',
        ),
        (
            'A Prometheus scrape and log shipper, if configured',
            'Metrics with allowlisted labels; logs, which carry no personal data by rule',
            'The customer',
        ),
        (
            "A web page or search engine the client's agent reaches, allowed by default",
            'The URL or the search query the model chose, which can contain what it has read',
            'The customer: `air_gapped: true`, `permissions.web_fetch` and `permissions.web_search`, '
            "or the organisation's group policy, turn them off",
        ),
        (
            'A service a super admin approved (a paid module, or one the customer or its integrator built)',
            "Who a person is, through the directory and tokens; erasure and subject-access calls. "
            "Back from it: messages, files and events for a person's or an app's agent",
            'The customer, through each approval on the portal',
        ),
        ('The vendor', '**Nothing**', '—'),
    ):
        out.append(f'| {destination} | {reaches} | {whose} |')
    out.append('')
    out.append('**The model endpoint is the one that matters.** `base_url` accepts any absolute')
    out.append("URL: the product is built for the customer's own vLLM nodes, and the whole")
    out.append('inference stack — routing, drain, health — is about GPUs they run. Nothing stops a')
    out.append('customer pointing it at a third-party API — or choosing a cloud kind: Azure OpenAI,')
    out.append('Vertex AI, AWS Bedrock, Anthropic, Microsoft Foundry (DEC-16) — and if they do, that provider')
    out.append("receives their developers' source code and becomes a processor they must assess,")
    out.append('contract with under Article 28, and possibly list as a recipient. The software')
    out.append('cannot make that decision for them and does not try to. For a cloud backend, its')
    out.append("`region` is where the prompt is processed, and the cloud's own credentials stay on the")
    out.append('server: no client and no Fleet VM holds them.')
    out.append('')
    out.append('A DPIA for a deployment using self-hosted nodes and a DPIA for one calling a')
    out.append('public API are different documents. This is the fact that decides which one you')
    out.append('are writing.')
    out.append('')

    # ── 35(7)(b) ─────────────────────────────────────────────────────
    out.append('## Article 35(7)(b) — necessity and proportionality')
    out.append('')
    out.append(f'The assessment itself: {PLACEHOLDER}. It is a judgement about a purpose the vendor')
    out.append('does not know.')
    out.append('')
    out.append('What can be offered is evidence about minimisation, which is the part of')
    out.append('proportionality a controller can point at rather than argue:')
    out.append('')
    out.append('* **a feature was removed for collecting data the deployment could not use.** A')
    out.append('  public waitlist shipped in every self-hosted install, collecting names and')
    out.append('  addresses for a list the customer was not running. Routes, table, schema and')
    out.append('  emails are gone, with a migration that drops it (DP-13);')
    out.append('* **logs carry shape rather than content**, enforced by an AST rule over the whole')
    out.append('  application after commit messages, author addresses and whole prompts were found')
    out.append('  in them (LG-5);')
    out.append('* **the migration hook takes one password and five coordinates**, not a copy of')
    out.append('  every secret, because that is all Alembic reads;')
    out.append('* **audit `detail` never holds a request body**, deliberately, because that table')
    out.append('  is retained for the whole window;')
    out.append('* **retention exists and is applied** rather than merely configurable (DP-6) —')
    out.append('  though the default keeps everything, which is a controller decision the')
    out.append('  deployment leaves open.')
    out.append('')

    # ── 35(7)(c) ─────────────────────────────────────────────────────
    out.append('## Article 35(7)(c) — risks to the rights and freedoms of data subjects')
    out.append('')
    out.append("Written from the subject's side, which is what the article asks and what a")
    out.append('security risk register does not answer. [`RISK_ASSESSMENT.md`](RISK_ASSESSMENT.md)')
    out.append('lists what could go wrong with the system; these are what it would mean for a')
    out.append('person.')
    out.append('')
    out.append('Most of them are marked realised, because they happened here and were fixed. That')
    out.append('is not reassurance — it is the evidence a controller should weigh, since a risk')
    out.append('that has occurred once in a codebase is a different proposition from one nobody')
    out.append('has seen.')
    out.append('')
    for risk in SUBJECT_RISKS:
        out.append(f'### {risk.ref} — {risk.harm}')
        out.append('')
        if risk.findings:
            out.append(f'**Observed here:** {", ".join(risk.findings)}.')
        else:
            out.append('**Not a defect — a property of the processing.**')
        if risk.rows:
            measures = ', '.join(f'`{r}` ({statuses.get(r, "?")})' for r in risk.rows)
            out.append('')
            out.append(f'**Measures:** {measures}')
        if risk.observed:
            out.append('')
            out.append(risk.observed)
        out.append('')

    # ── 35(7)(d) ─────────────────────────────────────────────────────
    out.append('## Article 35(7)(d) — measures, safeguards and mechanisms')
    out.append('')
    out.append("Every measure below is a register row, and the register's statuses are asserted")
    out.append('by tests. The generator refuses to render if a measure named here is not')
    out.append('`Verified`, which is what stops this section from becoming a list of intentions.')
    out.append('')
    out.append('| Concern | Measures |')
    out.append('|---|---|')
    out.append('| Confidentiality of stored credentials | `DP-1`, `DP-2` |')
    out.append('| Confidentiality between organisations | `AC-2`, `AC-7` |')
    out.append('| Nothing leaves the deployment | `DP-5` |')
    out.append('| Access ends when authorisation does | `AC-11`, `AC-8` |')
    out.append('| Subject rights: access, erasure, retention | `DP-8`, `DP-7`, `DP-6` |')
    out.append('| Accountability: who did what | `LG-1`, `LG-2`, `LG-3`, `LG-4` |')
    out.append('| Data minimisation in logs and errors | `LG-5`, `LG-9` |')
    out.append('| Recoverability | `DP-10`, `DP-11` |')
    out.append('')
    out.append('The gaps a controller should know about, from the same register: transport and')
    out.append("storage encryption are the deployment's (`DP-3`, `DP-4`), off-host backups are")
    out.append("the deployment's (`DP-12`), and the incident response plan is written but not")
    out.append('rehearsed and needs eleven organisational fields (`LG-8`).')
    out.append('')
    out.append('## What a controller still has to do')
    out.append('')
    out.append('1. Decide whether a DPIA is required — the screening above is an input;')
    out.append('2. state the purpose and the legal basis for each activity in the Article 30')
    out.append('   record, which leaves them blank on purpose;')
    out.append('3. set the retention windows, because the default keeps everything;')
    out.append('4. decide where inference runs, and assess whoever operates it;')
    out.append('5. assess the employee-monitoring question honestly, because the audit trail this')
    out.append('   product is proud of is what raises it;')
    out.append('6. fill in the eleven fields at the top of this document, and the eleven in the')
    out.append('   incident response plan.')
    out.append('')
    return '\n'.join(out)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='exit non-zero if the file is stale')
    args = parser.parse_args()

    for label, offenders in (
        ('cite findings that do not exist', unknown_findings()),
        ('cite register rows that do not exist', unknown_rows()),
        ('name a measure the register has not verified', unverified_rows()),
        ('describe a system failure rather than a harm to a person', risks_without_a_harm()),
    ):
        if offenders:
            print(f'these {label}:', file=sys.stderr)
            for line in offenders:
                print(f'  {line}', file=sys.stderr)
            return 2

    if not base_url_is_still_unrestricted():
        print(
            '`InferenceBackend.base_url` no longer accepts any host, so the data-flow section '
            'is wrong: it tells a controller they can point inference at a third party.',
            file=sys.stderr,
        )
        return 2

    rendered = render()
    if args.check:
        if not _OUTPUT.exists() or _OUTPUT.read_text() != rendered:
            print(f'{_OUTPUT} is stale; regenerate it', file=sys.stderr)
            return 1
        print(f'{_OUTPUT} is current')
        return 0

    _OUTPUT.write_text(rendered)
    print(
        f'wrote {_OUTPUT} ({len(rendered.splitlines())} lines, '
        f'{len(CRITERIA)} criteria, {len(SUBJECT_RISKS)} subject risks)'
    )
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
