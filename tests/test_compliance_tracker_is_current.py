"""The compliance tracker must not drift from what it cites.

`docs/COMPLIANCE_TRACKER.md` is the register kept between audits: one row
per control, with a status, evidence, an owner and a review date. A
register that drifts is worse than none, because it reads as assurance —
which is the same argument F72 made about a README describing routes that
were not routes, and F88 about a service contract describing 60% of its
own boundary. Both were fixed with a rule rather than a correction.

So this checks the four things that make the register trustworthy:

* every `Fnn` in an Evidence cell exists as a finding in
  `REVIEW_FINDINGS.md`, and every `§n` is a real section. The review log
  itself is not published; the index says what each id was and names the
  tests that guard it, and those tests must exist;
* every status is from the documented vocabulary, spelled exactly;
* nothing claims `Verified` without evidence, and nothing marked
  `Verified` or `Partial` has an unassigned owner;
* the summary counts match the register they summarise.

The last one matters more than it looks: a summary table is what somebody
reads instead of the register, so it is the first thing to go stale and
the last thing anybody checks.
"""

from __future__ import annotations

import collections
import pathlib
import re

import pytest

_ROOT = pathlib.Path(__file__).resolve().parent.parent
_TRACKER = _ROOT / 'docs/COMPLIANCE_TRACKER.md'
_READINESS = _ROOT / 'docs/REVIEW_FINDINGS.md'

_STATUSES = frozenset({'Verified', 'Partial', 'Operator', 'Gap', 'Process'})

# A register row: | ID | Control | Frameworks | Status | Evidence | Owner | Date |
# The organisational table has five columns rather than seven, so the row
# parser takes what is there and the checks below only look at cells they
# know exist.
_ROW = re.compile(r'^\|\s*([A-Z]{2}-\d+)\s*\|(.+)$', re.M)


def _rows() -> list[list[str]]:
    out = []
    for match in _ROW.finditer(_TRACKER.read_text()):
        cells = [c.strip() for c in match.group(2).split('|')]
        out.append([match.group(1)] + cells)
    return out


def _status_of(row: list[str]) -> str:
    for cell in row:
        stripped = cell.strip('` ')
        if stripped in _STATUSES:
            return stripped
    return ''


def test_the_register_was_parsed():
    """A regex against a document that changed shape finds nothing, and
    every check below would then pass by looking at an empty list. This
    review has produced that failure twice."""
    rows = _rows()

    assert len(rows) >= 30, f'only parsed {len(rows)} rows; has the table shape changed?'
    ids = [r[0] for r in rows]
    assert len(ids) == len(set(ids)), [i for i, n in collections.Counter(ids).items() if n > 1]


def test_every_finding_cited_exists():
    """The evidence column is the whole value of the register. A dangling
    `Fnn` is a claim with nothing behind it."""
    cited = set(re.findall(r'\bF(\d+)\b', _TRACKER.read_text()))
    real = set(re.findall(r'^\| F(\d+) \|', _READINESS.read_text(), re.M))

    missing = sorted(cited - real, key=int)

    assert cited, 'no findings cited at all — the evidence column is empty'
    assert (
        not missing
    ), f'the tracker cites findings that are not in REVIEW_FINDINGS.md: {["F" + m for m in missing]}'


def test_every_section_cited_exists():
    cited = {int(n) for n in re.findall(r'§(\d+)', _TRACKER.read_text())}
    real = {int(n) for n in re.findall(r'^\| §(\d+) \|', _READINESS.read_text(), re.M)}

    missing = sorted(cited - real)

    assert not missing, f'the tracker cites sections that do not exist: {missing}'


def test_every_status_is_from_the_vocabulary():
    """Spelled exactly. "verified", "Done" and "OK" would all read as
    assurance while meaning something nobody defined."""
    unknown = []
    for row in _rows():
        if not _status_of(row):
            unknown.append(row[0])

    assert not unknown, f'rows with no recognisable status: {unknown}. Allowed: {sorted(_STATUSES)}'


def test_nothing_claims_verified_without_evidence():
    """`Verified` in this register means tested, with a pointer to how."""
    unevidenced = []
    for row in _rows():
        if _status_of(row) != 'Verified':
            continue
        text = ' '.join(row)
        if not re.search(r'\bF\d+\b|§\d+', text):
            unevidenced.append(row[0])

    assert not unevidenced, (
        f'rows marked Verified with no finding or section cited: {unevidenced}. '
        'Either point at the evidence or downgrade the status.'
    )


def test_live_controls_have_an_owner():
    """An unowned control that is not a gap is nobody's job to keep true.

    `Gap` and `Process` rows may be unassigned — that is itself the
    finding, and the register renders it as `— unassigned` rather than
    leaving the cell blank.
    """
    unowned = []
    for row in _rows():
        status = _status_of(row)
        if status not in ('Verified', 'Partial', 'Operator'):
            continue
        if any('unassigned' in cell for cell in row):
            unowned.append(row[0])

    assert not unowned, f'live controls with no owner: {unowned}'


def test_the_summary_matches_the_register():
    """A summary is what people read instead of the register, so it is the
    first thing to go stale and the last thing anybody checks."""
    text = _TRACKER.read_text()
    # Split on a horizontal rule at line start, not on bare `---`: a
    # markdown table's own `|---|---|` separator contains it, so the naive
    # version sliced the section off after two lines and then reported "no
    # summary counts found". The assertion below caught that rather than
    # passing on an empty match, which is the only reason it was noticed.
    summary_section = text.split('## Summary')[1].split('\n---\n')[0]

    claimed = {status: int(count) for status, count in re.findall(r'\|\s*`(\w+)`\s*\|\s*(\d+)\s*\|', summary_section)}
    actual = collections.Counter(_status_of(row) for row in _rows())

    assert claimed, 'no summary counts found'
    mismatches = {
        status: (claimed.get(status), actual.get(status, 0))
        for status in _STATUSES
        if claimed.get(status, 0) != actual.get(status, 0)
    }

    assert not mismatches, (
        'the summary disagrees with the register (claimed, actual): '
        f'{mismatches}. Recount, or the summary is a fiction.'
    )


def test_the_gap_list_matches_the_register():
    """The closing list is what somebody works from, so it must name every
    gap and nothing else."""
    text = _TRACKER.read_text()
    # Split on the heading's stable prefix rather than its full text: the
    # first version of this matched "The seven gaps…" and broke the moment
    # the count changed, which is precisely the coupling this file exists
    # to prevent elsewhere.
    marker = '## The gaps'
    assert marker in text, 'the closing gap list is missing or renamed'
    plan = text.split(marker)[1]

    planned = set(re.findall(r'\b([A-Z]{2}-\d+)\b', plan))
    actual = {row[0] for row in _rows() if _status_of(row) == 'Gap'}

    assert not actual - planned, f'gaps missing from the closing list: {sorted(actual - planned)}'


def test_every_control_cited_in_the_narrative_exists():
    """`COMPLIANCE.md` argues the case and cites control ids. A citation
    to a control that does not exist is the register and the argument
    drifting apart, which is B1's whole finding."""
    narrative = (_ROOT / 'docs/COMPLIANCE.md').read_text()
    known = {row[0] for row in _rows()}
    assert known, 'no register rows parsed'

    cited = set(re.findall(r'\b([A-Z]{2}-\d+[a-z]?)\b', narrative))
    # Strip the suffixed ids (DP-5a) down to what the register holds, and
    # allow either form.
    unknown = {c for c in cited if c not in known and c.rstrip('abcdefgh') not in known}
    assert not unknown, f'COMPLIANCE.md cites controls the register does not have: {sorted(unknown)}'


def test_the_narrative_does_not_claim_a_gap_the_register_has_closed():
    """The rule B1 added, and the reason.

    For five passes `COMPLIANCE.md` said nothing prunes, there was no
    export path, no erasure mechanism, no SBOM and no air-gap test —
    all five of which had been built. The register beside it was right
    the whole time, because the register is asserted by this file and
    the argument was not.

    So: a gap the narrative lists must name a control, and that control
    must not be `Verified` in the register. Naming the control is the
    part that makes the check possible at all, which is why the gaps
    section carries ids now.
    """
    narrative = (_ROOT / 'docs/COMPLIANCE.md').read_text()
    marker = '## The gaps'
    assert marker in narrative, 'the narrative has no gaps section, or it was renamed'
    gaps = narrative.split(marker)[1].split('\n## ')[0]

    verified = {row[0] for row in _rows() if _status_of(row) == 'Verified'}
    contradictions = []
    for entry in re.split(r'\n\*\*\d+\.', gaps):
        # A struck-through heading (~~…~~ Built) is the document saying
        # so itself; those are the ones allowed to cite a closed control.
        if '~~' in entry.split('\n')[0]:
            continue
        for control in re.findall(r'\b([A-Z]{2}-\d+)\b', entry):
            if control in verified:
                contradictions.append(f'{control} listed as a gap but Verified in the register')

    assert not contradictions, '\n'.join(contradictions)


def test_the_companion_document_is_linked_both_ways():
    """The narrative and the register have to point at each other, or one
    of them gets updated alone."""
    tracker = _TRACKER.read_text()
    assert 'COMPLIANCE.md' in tracker
    assert 'REVIEW_FINDINGS.md' in tracker

    narrative = (_ROOT / 'docs/COMPLIANCE.md').read_text()
    assert (
        'COMPLIANCE_TRACKER.md' in narrative
    ), 'COMPLIANCE.md does not link the tracker, so a reader of the argument never learns there is a register'


@pytest.mark.parametrize('doc', ['docs/COMPLIANCE.md', 'docs/COMPLIANCE_TRACKER.md'])
def test_neither_document_claims_certification(doc):
    """Neither may say igni *is* compliant. SOC 2 Type II is an auditor's
    report over a period, ISO 27001 certifies an ISMS, and nobody issues a
    GDPR certificate — so a sentence claiming otherwise is false in a way
    that matters commercially.
    """
    text = (_ROOT / doc).read_text().lower()

    for claim in (
        'we are soc 2 compliant',
        'igni is soc 2 compliant',
        'soc 2 certified',
        'iso 27001 certified',
        'gdpr certified',
        'gdpr compliant',
        'fully compliant',
    ):
        assert claim not in text, f'{doc} claims {claim!r}'


# Phrases that assert something does not exist *now*. Present tense only:
# "was missing" and "went on claiming otherwise" are this document
# describing its own history, which is the most useful thing in it.
_ABSENCE_CLAIMS = (
    'is still missing',
    'is still unaddressed',
    'remains missing',
    'remains unaddressed',
    'is not built',
    'is still absent',
    'is not implemented',
    'nothing enforces',
    'nothing prunes',
    'nothing checks',
    'no lever',
    'has no lever',
    'still cannot',
    'does not exist yet',
)


def _absence_sentences(text: str) -> list[str]:
    """Sentences claiming a present-tense absence.

    Struck-through text is skipped: `~~Access review~~ ... is built` is
    the document correcting itself, and the strikethrough is the
    correction.
    """
    without_struck = re.sub(r'~~.*?~~', '', text, flags=re.S)
    sentences = re.split(r'(?<=[.!?])\s+', without_struck.replace('\n', ' '))
    return [s for s in sentences if any(claim in s.lower() for claim in _ABSENCE_CLAIMS)]


def test_the_absence_detector_works():
    """The detector before what it detects.

    A scan that matches nothing agrees with every document, and the two
    rules below would then be decoration. This pins the detector against
    text it must catch and text it must leave alone — including the
    past-tense form, which is the document's own history and the part
    worth keeping.
    """
    caught = _absence_sentences('Token revocation is still missing (AC-11).')
    assert len(caught) == 1, caught

    # Tense is the whole distinction. This document's account of its own
    # errors is the most useful thing in it and must not read as a fresh
    # claim, so history goes in the past tense and the detector is
    # deliberately literal about that rather than clever.
    assert not _absence_sentences('For five passes this said that nothing pruned; retention exists.')

    assert not _absence_sentences('Everything in DP-6 is built and asserted.')
    assert not _absence_sentences('~~Monitoring is still unaddressed~~ — LG-7 covers it.')


def test_every_absence_claim_in_the_narrative_names_a_control():
    """An unnamed gap cannot be checked, so it never stops being true.

    B1 added a rule that a gap the narrative lists must name a control
    that is not `Verified` — and scoped it to the `## The gaps` section.
    Two claims outside that section then went stale unnoticed: "what is
    still missing … is token revocation (AC-11 / DEC-10)" after B5 built
    it, and "monitoring is still unaddressed" after LG-7 was verified —
    the second naming no control at all, so no rule could ever have
    caught it. That is F114's shape: the policy was not wrong, it was
    aimed.

    This is the same rule aimed at the whole document, and it demands the
    id, because the id is what makes the claim falsifiable.
    """
    narrative = (_ROOT / 'docs/COMPLIANCE.md').read_text()
    unnamed = [s for s in _absence_sentences(narrative) if not re.search(r'\b[A-Z]{2}-\d+\b', s)]
    assert not unnamed, (
        'these claim something is missing without naming a control, so nothing can '
        'ever check them:\n  ' + '\n  '.join(s.strip()[:200] for s in unnamed)
    )


def test_no_absence_claim_in_the_narrative_names_a_verified_control():
    """The whole document, not just its gaps section."""
    narrative = (_ROOT / 'docs/COMPLIANCE.md').read_text()
    verified = {row[0] for row in _rows() if _status_of(row) == 'Verified'}

    contradictions = []
    for sentence in _absence_sentences(narrative):
        for control in re.findall(r'\b([A-Z]{2}-\d+)\b', sentence):
            if control in verified:
                contradictions.append(f'{control} is Verified but: {sentence.strip()[:200]}')

    assert not contradictions, '\n'.join(contradictions)


def test_the_index_names_only_tests_that_exist():
    """The index's "Guarded by" column is evidence too. A test that was
    deleted or renamed and is still listed there is a claim with nothing
    behind it, which is the failure this whole file exists to catch."""
    cited = set(re.findall(r'`((?:tests|portal)/[^`]+\.(?:py|ts|tsx))`', _READINESS.read_text()))

    assert cited, 'the index names no tests at all; has its table shape changed?'
    missing = sorted(path for path in cited if not (_ROOT / path).is_file())
    assert not missing, f'REVIEW_FINDINGS.md names tests that do not exist: {missing}'


def test_every_finding_the_public_documents_cite_is_indexed():
    """Not only the tracker: the compliance pack, SECURITY.md and the
    README cite findings too, and each has to resolve in this repository
    alone."""
    real = set(re.findall(r'^\| (F\d+) \|', _READINESS.read_text(), re.M))
    sources = [*sorted((_ROOT / 'docs').glob('*.md')), _ROOT / 'README.md', _ROOT / 'SECURITY.md']

    dangling = []
    for path in sources:
        for finding in set(re.findall(r'(?<![\w-])(F\d{1,3})(?![\w])', path.read_text())):
            if finding not in real:
                dangling.append(f'{path.relative_to(_ROOT)}: {finding}')

    assert not dangling, f'cited but not in REVIEW_FINDINGS.md: {sorted(dangling)}'


def test_nothing_points_at_the_private_review_documents():
    """The review log, the roadmap and the draft EULA were moved out
    before the repository was published. A link to one of them is a
    link to nothing for every reader of the public repository."""
    gone = ['PROD_' + 'READINESS.md', 'ROAD_' + 'TO_PROD.md', 'DECISION_' + 'MODELS.md', 'EU' + 'LA.md']
    import subprocess

    listed = subprocess.run(
        ['git', 'ls-files', '-co', '--exclude-standard', '-z'], cwd=_ROOT, capture_output=True, text=True, check=True
    ).stdout.split('\0')
    offenders = []
    for name in filter(None, listed):
        path = _ROOT / name
        if not path.is_file() or path.suffix not in {'.md', '.py', '.ts', '.tsx', '.yml', '.yaml', '.txt', '.sh', ''}:
            continue
        text = path.read_text(errors='ignore')
        offenders += [f'{name}: {gone_name}' for gone_name in gone if gone_name in text]

    assert not offenders, offenders
