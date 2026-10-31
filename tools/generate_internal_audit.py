"""Generate `docs/INTERNAL_AUDIT.md` — the programme and the review pack.

C9, OR-5. ISO/IEC 27001 clauses 9.2 (internal audit), 9.3 (management
review) and 10.2 (nonconformity and corrective action); SOC 2 CC4.1 and
CC4.2.

Two things are true at once and the document has to say both.

**The practice already exists.** The pre-production review is 107
sections of examining a control, finding whether it actually works, and
recording what was done about it — which is what clause 10.2 asks for,
in the shape it asks for it, written before anybody thought to call it
that. An auditor asking "show me your corrective actions" can be handed
that file.

**The cycle does not.** No schedule, no owner, no minutes, nobody who
has signed anything off. That is what OR-5 is, and it is not something a
repository can produce.

## What is derived

**The audit method, from the register's own statuses.** A `Verified`
row's evidence is a test or a demonstration, so re-auditing it means
running that and asking whether it still measures what it claims. A
`Process` row has no evidence here at all, so auditing it means asking a
person for a record — and finding none is a nonconformity rather than an
inconvenience. Those are different activities with different costs, and
the distinction is already in the register.

**The priority, from what is unproven.** ISO 9.2.1 asks that a
programme consider "the importance of the processes concerned and the
results of previous audits". A row nobody has ever verified deserves
attention ahead of one with a test that runs on every commit.

**The management review inputs**, because clause 9.3.2 lists them and
most already exist as generated documents. Each is mapped to the file
that answers it, and a test fails if the file does not exist — a review
agenda citing a document nobody wrote is the failure this repository
keeps finding.

## What is refused

Any claim that an audit has happened. The programme says what to
examine and how; it cannot say that somebody did. And **impartiality**
is stated rather than glossed: clause 9.2.2 wants auditors who do not
audit their own work, which one person cannot satisfy, so the document
says so instead of implying an independence that does not exist.
"""

from __future__ import annotations

import argparse
import collections
from dataclasses import dataclass
import pathlib
import re
import sys

_ROOT = pathlib.Path(__file__).resolve().parent.parent
_TRACKER = _ROOT / 'docs/COMPLIANCE_TRACKER.md'
_READINESS = _ROOT / 'docs/REVIEW_FINDINGS.md'
_OUTPUT = _ROOT / 'docs/INTERNAL_AUDIT.md'

PLACEHOLDER = '`__ORGANISATION_TO_COMPLETE__`'

#: What the register's group prefixes mean. Used to name audit areas
#: rather than to invent them — the grouping is the register's.
_AREAS = {
    'AC': 'Access control and authentication',
    'AV': 'Availability and operations',
    'AX': 'Accessibility',
    'CM': 'Change management and secure development',
    'DP': 'Data protection and privacy',
    'LG': 'Logging, monitoring and incident response',
    'OR': 'Organisational controls',
}


@dataclass(frozen=True)
class Method:
    """How to audit a row, given what its status says about evidence."""

    status: str
    #: What the auditor actually does. Different work per status, which
    #: is the point — a programme that says "review the control" for all
    #: of them has not been thought about.
    activity: str
    #: What makes this row a finding.
    nonconformity: str
    #: Higher runs first. Derived from how much is unproven, not from
    #: how important the control feels.
    priority: int


METHODS: tuple[Method, ...] = (
    Method(
        'Process',
        'Ask the accountable person for the record — the signature, the completed training, '
        'the minutes, the contract. The evidence for these rows is not in this repository and '
        'cannot be; the audit is the act of asking.',
        'There is no record, or nobody is accountable. Both are nonconformities against clause '
        '9.2, and the second is the more serious.',
        priority=1,
    ),
    Method(
        'Partial',
        'Establish what the missing half is, and whether it has moved since the last audit. The '
        'row states it; the audit is checking that the statement is still the true one rather '
        'than the one written when somebody last looked.',
        'The missing half is not what the row says it is, in either direction — it has been '
        'built and the row still claims a gap, or it has grown and the row does not.',
        priority=2,
    ),
    Method(
        'Operator',
        'Audit the documentation, not the control. These are controls a customer operates — TLS '
        'termination, disk encryption, backups of their database — so the only thing auditable '
        'here is whether what we tell them is accurate and complete.',
        'The instruction to the operator is missing, wrong, or describes something the software no longer does.',
        priority=3,
    ),
    Method(
        'Verified',
        'Run the evidence. Each of these cites a test, a demonstration or a measurement; the '
        'audit is executing it and then asking the harder question — **does it still measure '
        'what it claims?** A guard that has silently stopped checking anything passes exactly '
        'like one that works.',
        'The evidence no longer runs, no longer exists, or passes for a reason other than the '
        'control working. R-10 is that failure and it has been found repeatedly here.',
        priority=4,
    ),
)


@dataclass(frozen=True)
class ReviewInput:
    """A clause 9.3.2 management review input, and what answers it."""

    clause: str
    what: str
    #: A repository path that answers it, or empty when nothing does.
    source: str
    #: Said plainly when `source` is empty. An absent input declared is
    #: an agenda item; an absent input hidden is a gap in the review.
    absent: str = ''


REVIEW_INPUTS: tuple[ReviewInput, ...] = (
    ReviewInput(
        '9.3.2 a)',
        'The status of actions from previous management reviews',
        '',
        'There have been no previous management reviews, so there are no actions to carry '
        'forward. This is the first cycle and the input is empty for a reason that will stop '
        'being true after one meeting.',
    ),
    ReviewInput(
        '9.3.2 b)',
        'Changes in external and internal issues relevant to the ISMS',
        'docs/DECISIONS.md',
        '',
    ),
    ReviewInput(
        '9.3.2 c) 1)',
        'Nonconformities and corrective actions',
        'docs/REVIEW_FINDINGS.md',
        '',
    ),
    ReviewInput(
        '9.3.2 c) 2)',
        'Monitoring and measurement results',
        'charts/igni/files/alerts.yaml',
        '',
    ),
    ReviewInput(
        '9.3.2 c) 3)',
        'Audit results',
        '',
        'No internal audit has been conducted under a programme. The examinations recorded in '
        'the pre-production review (`docs/REVIEW_FINDINGS.md`) are audit work in substance — '
        'a control examined, a finding, '
        'a corrective action — but they were not conducted against a programme, by an '
        'independent auditor, or to a schedule, so calling them audit results would be '
        'claiming a form that was not followed.',
    ),
    ReviewInput(
        '9.3.2 c) 4)',
        'Fulfilment of information security objectives',
        'docs/COMPLIANCE_TRACKER.md',
        '',
    ),
    ReviewInput(
        '9.3.2 d)',
        'Feedback from interested parties',
        '',
        'There are no customers yet, so there is no feedback. Recorded rather than omitted, '
        'because an empty input a reader can see is different from one that was never '
        'considered.',
    ),
    ReviewInput(
        '9.3.2 e)',
        'Results of risk assessment and status of the risk treatment plan',
        'docs/RISK_ASSESSMENT.md',
        '',
    ),
    ReviewInput(
        '9.3.2 f)',
        'Opportunities for continual improvement',
        'docs/COMPLIANCE_TRACKER.md',
        '',
    ),
)


def register_rows() -> list[tuple[str, str]]:
    """`(ref, status)` for every control row."""
    return [
        (m.group(1), m.group(4))
        for m in re.finditer(
            r'^\|\s*([A-Z]{2}-\d+)\s*\|([^|]*)\|([^|]*)\|\s*`?([A-Za-z]+)`?\s*\|',
            _TRACKER.read_text(),
            re.M,
        )
    ]


def statuses() -> collections.Counter:
    return collections.Counter(status for _, status in register_rows())


def areas() -> dict[str, collections.Counter]:
    """Rows per area, by status, so the programme is scoped by the
    register's own grouping rather than an invented one."""
    out: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    for ref, status in register_rows():
        out[ref.split('-')[0]][status] += 1
    return dict(out)


def findings_recorded() -> int:
    """Finding rows in the index — the corrective-action log's size."""
    return len(set(re.findall(r'^\| (F\d+) \|', _READINESS.read_text(), re.M)))


# ── What the tests assert ────────────────────────────────────────────


def statuses_without_a_method() -> list[str]:
    """A status the programme does not say how to audit. The register
    gains one of these by somebody adding a word, and a programme that
    silently omits it audits everything except the new thing."""
    covered = {m.status for m in METHODS}
    return sorted(s for s in statuses() if s not in covered)


def methods_for_absent_statuses() -> list[str]:
    """The other direction: a method for a status nothing uses is
    instruction for work that will never happen."""
    present = set(statuses())
    return sorted(m.status for m in METHODS if m.status not in present)


def unknown_areas() -> list[str]:
    return sorted(a for a in areas() if a not in _AREAS)


def review_inputs_citing_a_missing_file() -> list[str]:
    """An agenda citing a document nobody wrote."""
    return sorted(f'{i.clause} → {i.source}' for i in REVIEW_INPUTS if i.source and not (_ROOT / i.source).exists())


def review_inputs_neither_sourced_nor_explained() -> list[str]:
    """An input with no answer and no statement of why. Silence in a
    review agenda reads as covered."""
    return sorted(i.clause for i in REVIEW_INPUTS if not i.source and not i.absent)


def render() -> str:
    counts = statuses()
    by_area = areas()
    ordered = sorted(METHODS, key=lambda m: m.priority)

    out: list[str] = []
    out.append('# Internal audit and management review')
    out.append('')
    out.append('**GENERATED by tools/generate_internal_audit.py — do not edit.**')
    out.append('Regenerate with `make docs`; `tests/test_internal_audit.py` fails if the')
    out.append('committed file and the generator disagree.')
    out.append('')
    out.append('C9, OR-5. ISO/IEC 27001 clauses 9.2, 9.3 and 10.2; SOC 2 CC4.1 and CC4.2.')
    out.append('')

    out.append('## The practice exists; the cycle does not')
    out.append('')
    out.append('The pre-production review recorded **' + str(findings_recorded()) + ' findings**:')
    out.append('a control examined, what was actually true, and the change that keeps it fixed.')
    out.append('That is clause 10.2 — nonconformity and corrective action — in the shape the')
    out.append('clause asks for, written before anybody thought to call it that. An auditor')
    out.append('asking to see corrective actions can be handed that log, on request; it is not')
    out.append('published, and [REVIEW_FINDINGS.md](REVIEW_FINDINGS.md) indexes it here.')
    out.append('')
    out.append('What does not exist is a **cycle**: a schedule, an owner, a scope agreed in')
    out.append('advance, and minutes signed afterwards. That is OR-5, and no repository can')
    out.append('produce it. This document is the programme those meetings would follow.')
    out.append('')

    out.append('## Impartiality, which cannot be satisfied here')
    out.append('')
    out.append('Clause 9.2.2 requires auditors selected so as to ensure objectivity, and says')
    out.append('auditors shall not audit their own work.')
    out.append('')
    out.append('**One person cannot do that.** In an organisation of this size the person')
    out.append('auditing a control is the person who built it, and every finding in the review')
    out.append('log was found by its author. Saying so is the only honest option: the')
    out.append('alternative is a programme that implies an independence it does not have, which')
    out.append('an auditor will find in the first interview.')
    out.append('')
    out.append('What partly substitutes, and does not fully: the evidence for a `Verified` row')
    out.append('is a test somebody else can run, and a reviewer who runs it is not taking the')
    out.append("author's word. That is reproducibility rather than independence. Where the")
    out.append('organisation grows, or a contractor can be used for a cycle, the gap closes.')
    out.append('')

    out.append('## Scope: the register, by area')
    out.append('')
    out.append('| Area | Rows | Verified | Needing a person |')
    out.append('|---|---|---|---|')
    for prefix in sorted(by_area):
        area = by_area[prefix]
        total = sum(area.values())
        person = area['Process'] + area['Operator']
        out.append(f'| {prefix} — {_AREAS[prefix]} | {total} | {area["Verified"]} | {person} |')
    out.append(
        f'| **All** | **{sum(counts.values())}** | **{counts["Verified"]}** | '
        f'**{counts["Process"] + counts["Operator"]}** |'
    )
    out.append('')
    out.append('Counted from `docs/COMPLIANCE_TRACKER.md` at generation, so this table cannot')
    out.append('drift from the register it scopes.')
    out.append('')

    out.append('## Method, by status')
    out.append('')
    out.append('Clause 9.2.1 asks a programme to consider the importance of what is audited and')
    out.append('the results of previous audits. The register already encodes both: a status is')
    out.append('the conclusion of the last examination, and it determines what evidence exists')
    out.append('to re-examine. So the method is derived from it, and the order is too — what')
    out.append('nobody has proven comes before what a test proves on every commit.')
    out.append('')
    for method in ordered:
        count = counts.get(method.status, 0)
        out.append(f'### {method.priority}. `{method.status}` — {count} rows')
        out.append('')
        out.append(f'**What the auditor does.** {method.activity}')
        out.append('')
        out.append(f'**What is a finding.** {method.nonconformity}')
        out.append('')

    out.append('## Management review inputs')
    out.append('')
    out.append('Clause 9.3.2 lists what a management review must consider. Most of it already')
    out.append('exists here as a generated document, so the agenda points at files rather than')
    out.append('asking somebody to assemble a pack — and a test fails if any cited file stops')
    out.append('existing.')
    out.append('')
    out.append('| Clause | Input | Where it comes from |')
    out.append('|---|---|---|')
    for item in REVIEW_INPUTS:
        source = f'`{item.source}`' if item.source else '**Nothing yet** — see below'
        out.append(f'| {item.clause} | {item.what} | {source} |')
    out.append('')
    for item in REVIEW_INPUTS:
        if item.absent:
            out.append(f'**{item.clause}, {item.what.lower()}.** {item.absent}')
            out.append('')

    out.append('## The cycle, which a person has to set')
    out.append('')
    out.append('| Field | Value |')
    out.append('|---|---|')
    for field in (
        'Organisation',
        'Audit programme owner',
        'Internal audit frequency',
        'Management review frequency',
        'Next internal audit due',
        'Next management review due',
        'Where minutes are kept',
        'Approved by',
        'Date of approval',
    ):
        out.append(f'| {field} | {PLACEHOLDER} |')
    out.append('')
    out.append('Minutes are the output an auditor asks for, and the one thing this document')
    out.append('cannot produce even in draft: a decision nobody made cannot be recorded. What')
    out.append('a set should contain is who attended, which revision of this programme was')
    out.append('followed, what was examined, what was found, and what was decided — with an')
    out.append('owner and a date against each decision, because clause 9.3.2 a) makes those the')
    out.append('first input to the next review.')
    out.append('')

    out.append('## What this document is not')
    out.append('')
    out.append('**Evidence that an audit happened.** It says what to examine and how. Nothing')
    out.append('here asserts that anybody did, and no field above is filled in.')
    out.append('')
    out.append('**A substitute for the review log.** The review log is where findings')
    out.append('live, and it should stay that way — a second place to record them is how two')
    out.append('records of the same thing start disagreeing.')
    out.append('')
    out.append('**Applicable to the certification audit.** That is OR-7 and C10: a different')
    out.append('activity, done by somebody else, needing a contract and an observation window.')
    out.append('')
    return '\n'.join(out)


def main() -> int:
    parser = argparse.ArgumentParser(description='Generate the internal audit programme.')
    parser.add_argument('--check', action='store_true', help='exit non-zero if the file is stale')
    args = parser.parse_args()

    for label, offenders in (
        ('are register statuses the programme cannot audit', statuses_without_a_method()),
        ('are methods for statuses no row uses', methods_for_absent_statuses()),
        ('are register areas with no name', unknown_areas()),
        ('cite a file that does not exist', review_inputs_citing_a_missing_file()),
        ('have neither a source nor an explanation', review_inputs_neither_sourced_nor_explained()),
    ):
        if offenders:
            print(f'refusing: {offenders} {label}', file=sys.stderr)
            return 1

    rendered = render()
    if args.check:
        if _OUTPUT.exists() and _OUTPUT.read_text() == rendered:
            print(f'{_OUTPUT} is current')
            return 0
        print(f'{_OUTPUT} is stale; regenerate it', file=sys.stderr)
        return 1

    _OUTPUT.write_text(rendered)
    print(f'wrote {_OUTPUT} ({len(rendered.splitlines())} lines, {len(register_rows())} rows scoped)')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
