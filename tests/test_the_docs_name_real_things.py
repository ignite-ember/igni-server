"""Every path and every setting a document quotes must exist.

`docs/ARCHITECTURE.md` is why this file exists. It is linked from the
README as "System architecture and data flow", and by the time anybody
read it properly it described:

* a `VectorBridge` storage layer with a Weaviate backend — absent from
  this repository in any form, code or config;
* an `app/tasks/` package and `app/celery_app.py`, removed by DEC-13;
* `app/services/vector_bridge/`, `app/dataset/ast_parsing/`, and a
  five-phase analysis workflow running in this process — all of it moved
  to `igni__code-index`;
* an environment table of eleven rows in which eight named a variable
  the application does not read, including `DATABASE_URL` and
  `WEAVIATE_URL`.

Every one of those had been true. That is the whole point: none of them
became false through a mistake, they became false through a correct
change somewhere else, and nothing connected the two. Which is F139's
shape — a document describing a system the product no longer is — aimed
at the document a new engineer is pointed at first.

So the checkable half is checked. Two rules:

* **every repository path a document quotes in backticks exists.** This
  catches a moved module, a deleted package and a renamed test;
* **every environment variable a document puts in a table cell is a real
  field on `Settings`.** A table is where a reader goes to copy a name
  into their `.env`, so a wrong one there costs them a debugging session
  and reads as the product being broken.

Both are rules over every markdown file in `docs/` plus the README,
rather than a list of the documents somebody has audited — the version
that would have passed while `ARCHITECTURE.md` rotted.

**What this does not do.** It cannot tell whether a paragraph is true.
`ARCHITECTURE.md` could describe the wrong architecture entirely in prose
that quotes only real paths. The rules here remove a class of rot; they
do not make a document trustworthy, and the rewritten `ARCHITECTURE.md`
says so about itself in as many words.
"""

from __future__ import annotations

import pathlib
import re
import subprocess

import pytest

_ROOT = pathlib.Path(__file__).resolve().parent.parent
_DOCS = [_ROOT / 'README.md', *sorted((_ROOT / 'docs').glob('*.md'))]

#: Top-level directories a backticked path could plausibly refer to. A
#: prefix list rather than "anything with a slash", because documents
#: quote URL paths, glob patterns and provider paths in backticks too.
_REPO_DIRS = (
    'alembic',
    'app',
    'charts',
    'containers',
    'marketplace',
    'portal',
    'tests',
    'tools',
)

#: Sibling repositories a two-sided document legitimately names.
#:
#: A document describing a boundary with two ends quotes files on
#: both. Those paths are real and worth checking — they are how a reader finds the other half — but they resolve
#: against a different root, and only when that repository happens to
#: be checked out. Unqualified `tests/...` in such a document is
#: ambiguous to a reader as well as to this rule, which is why the
#: convention is to prefix them.
_SIBLINGS = ('igni__artifacts', 'igni__code-index')

_PATH = re.compile(rf'`((?:{"|".join(_REPO_DIRS)})/[\w./-]*[\w/])`')

#: The same shape, prefixed with a sibling repository name.
_SIBLING_PATH = re.compile(rf'`((?:{"|".join(_SIBLINGS)})/[\w./-]*[\w/])`')


def _sibling_misses(text: str) -> list[str]:
    """Sibling-qualified paths that are wrong, where we can tell.

    Skipped entirely when the sibling is not checked out — a developer
    with only this repository must not be failed by a rule about
    another one. When it *is* checked out the path is checked properly,
    so the reference cannot rot unnoticed on a machine that has both.
    """
    missing = []
    for quoted in _SIBLING_PATH.findall(text):
        repo, _, rest = quoted.partition('/')
        root = _ROOT.parent / repo
        if not root.is_dir():
            continue
        if not (root / rest).exists():
            missing.append(quoted)
    return sorted(set(missing))


#: `| `NAME` |` — a variable in a table cell, which is where a reader
#: copies from. Prose mentions are not checked: a sentence can name a
#: variable a customer sets on a *provider* or in the CLI, and this file
#: only knows about this application's settings.
_ENV_CELL = re.compile(r'\|\s*`([A-Z][A-Z0-9_]{3,})`\s*\|')

#: Documents whose subject is the past. A review log records what it
#: found at the time, including the name of a file that has since been
#: deleted — and rewriting that history to keep a path resolving would
#: destroy the evidence the log exists to hold.
#:
#: None needs it today. The review log and the documentation-verification
#: log both used it; neither is in this repository any more, and
#: `REVIEW_FINDINGS.md`, the public index of the review log, quotes no
#: path that is gone. Kept as an empty, asserted-empty mechanism so a new
#: dated record has to argue its way in.
#:
#: Keyed by the word its own opening prose must contain, so the
#: exemption cannot be claimed by a document that does not say what it
#: is. Checked below, per entry.
_HISTORICAL: dict[str, str] = {}


def _settings_env_names() -> set[str]:
    """Every name the application would read from the environment.

    Includes nested models: `Settings.ai` is an `AIConfig`, reached as
    `AI__UPSTREAM_TIMEOUT` through pydantic-settings' `__`
    delimiter.
    """
    from app.config import Settings

    names: set[str] = set()
    for name, field in Settings.model_fields.items():
        names.add(name.upper())
        inner = getattr(field.annotation, 'model_fields', None)
        if inner:
            names |= {f'{name.upper()}__{sub.upper()}' for sub in inner}
    return names


class TestTheRulesMeasureSomething:
    """Both rules compare against a set derived from the repository. An
    empty set on either side passes everything, so both are asserted
    non-empty before the rules that use them run."""

    def test_there_are_documents_to_check(self):
        assert len(_DOCS) > 10, [d.name for d in _DOCS]

    def test_the_settings_are_readable(self):
        names = _settings_env_names()

        assert len(names) > 40, sorted(names)
        assert 'SECRET_ENCRYPTION_KEY' in names
        assert (
            'AI__UPSTREAM_TIMEOUT' in names
        ), 'nested settings are not being expanded, so every AI__ row would read as unknown'

    def test_the_path_pattern_finds_paths(self):
        """A regex that matches nothing passes every document."""
        found = _PATH.findall((_ROOT / 'docs/ARCHITECTURE.md').read_text())

        assert len(found) > 10, found
        assert 'app/config.py' in found, found

    def test_the_pattern_would_have_caught_the_original(self):
        """The specific failure, replayed. A rule that cannot be shown to
        fail on the input that motivated it is a rule nobody should
        trust.
        """
        was_wrong = 'The worker lived in `app/tasks/process_git_event.py` and `app/celery_app.py`.'
        found = _PATH.findall(was_wrong)

        assert found == ['app/tasks/process_git_event.py', 'app/celery_app.py'], found
        assert not any((_ROOT / p).exists() for p in found)


def _tracked_paths() -> set[str]:
    """Every path git knows about, files and the directories above them.

    Asked of git rather than the filesystem, because the question these
    documents answer is "does this *repository* contain that path" and a
    working directory is not the repository.

    The distinction is not academic. `app/services/documents` was deleted
    with the rest of the documents feature and the contract still named
    it — which passed here and failed in CI, because deleting a package
    leaves its `__pycache__` behind and `Path.exists()` says yes to a
    directory containing nothing but compiled leftovers. A check that
    only fails on a clean checkout is a check that reports the opposite
    of the truth to whoever is in a position to fix it.
    """
    listing = subprocess.run(
        ['git', 'ls-files', '-z'], cwd=_ROOT, capture_output=True, text=True, check=True
    ).stdout.split('\0')
    paths = {entry for entry in listing if entry}
    for entry in list(paths):
        parent = pathlib.PurePosixPath(entry).parent
        while str(parent) != '.':
            paths.add(str(parent))
            parent = parent.parent
    return paths


def _is_ignored(path: str) -> bool:
    """Whether git is told to ignore this path — i.e. it is output.

    Tried with the trailing slash as well as without, and the slash is
    not cosmetic. A rule written `test-results/` matches directories
    only, and git decides whether a pathname is a directory by looking
    for it — so on a clean checkout, where the directory has never been
    built, `portal/test-results` matches nothing and
    `portal/test-results/` matches the rule.

    Stripping the slash the document wrote was therefore the same
    existence-dependence this function was added to remove, one layer
    down: it passed on a machine that had run the build and failed in
    CI, which is the wrong way round.
    """
    for candidate in (path, path.rstrip('/') + '/', path.rstrip('/')):
        if subprocess.run(['git', 'check-ignore', '-q', candidate], cwd=_ROOT, capture_output=True).returncode == 0:
            return True
    return False


@pytest.mark.parametrize('doc', _DOCS, ids=lambda d: d.name)
def test_every_repository_path_it_quotes_exists(doc):
    quoted = _PATH.findall(doc.read_text())
    # Wildcards and placeholders are illustrative rather than paths.
    concrete = [p for p in quoted if '*' not in p and '{' not in p and '...' not in p]

    tracked = _tracked_paths()
    # A quoted path must be tracked — unless git is deliberately
    # ignoring it, in which case the document is naming where output
    # lands rather than claiming a file exists. `portal/dist/` and
    # `portal/test-results/` are both of those, and neither is a
    # mistake; `app/services/documents` was ignored by nothing and had
    # simply been deleted out from under the sentence naming it.
    candidates = {p for p in concrete if p.rstrip('/') not in tracked}
    missing = sorted(p for p in candidates if not _is_ignored(p))
    missing += _sibling_misses(doc.read_text())

    if doc.name in _HISTORICAL:
        pytest.skip(f'{doc.name} records what was true at the time; see _HISTORICAL')

    assert not missing, (
        f'{doc.name} quotes paths that do not exist: {missing}. Either the document is '
        f'describing a version of this repository that is gone, or the file moved and '
        f'nothing followed it.'
    )


@pytest.mark.parametrize('doc', _DOCS, ids=lambda d: d.name)
def test_every_setting_in_a_table_is_real(doc):
    known = _settings_env_names()
    quoted = set(_ENV_CELL.findall(doc.read_text()))

    # Variables that are not settings: operator-facing knobs read by the
    # container command, the compose files or the test harness rather
    # than by `Settings`. Each is named because it is a real thing a
    # reader can set, and none of them is a typo.
    external = {
        'IGNI_API_BIND',
        'IGNI_API_IMAGE',
        'IGNI_ENV_FILE',
        'IGNI_PORTAL_IMAGE',
        'API_UPSTREAM',
        'LOG_LEVEL',
        'DATABASE_URL',
        'PGPASSWORD',
        # Release-signing secrets and variables, read by the release
        # (.github/workflows/release.yml) and `tools/release/igni-bundle`,
        # never by a deployment. OFFLINE_UPDATES.md tabulates them for
        # whoever sets up igni__release's GitHub environment.
        'COSIGN_PRIVATE_KEY',
        'COSIGN_PASSWORD',
        'COSIGN_PUBLIC_KEY',
        'MINISIGN_SECRET_KEY',
        'MINISIGN_PASSWORD',
        'MINISIGN_PUBLIC_KEY',
        # tools/airgap-rehearsal.sh's own knobs, tabulated in
        # AIR_GAP_REHEARSAL.md for whoever runs the rehearsal.
        'IGNI_AIRGAP_NAME',
        'IGNI_AIRGAP_SKIP_BUILD',
        'IGNI_AIRGAP_SUBNET',
        'IGNI_AIRGAP_WORK',
        'IGNI_CLI',
    }
    unknown = sorted(q for q in quoted - known - external if not q.startswith('F'))

    if doc.name in _HISTORICAL:
        pytest.skip(f'{doc.name} records what was true at the time; see _HISTORICAL')

    assert not unknown, (
        f'{doc.name} documents settings the application does not read: {unknown}. A reader '
        f'copies these into an env file and gets a deployment that ignores them silently.'
    )


class TestTheHistoricalExemptionIsNarrow:
    """An exemption is a hole, so it has to be worth its cost.

    `REVIEW_FINDINGS.md` indexes a review log: a finding names the file
    it was about, and a later change may have deleted that file. The
    finding is still the record of what was found, and editing the path
    out would leave a finding about nothing.

    What stops this becoming a general escape hatch is that the exemption
    is per-document and asserted small — not a per-path allow-list, which
    is the shape that grows one entry at a time until the rule is gone.
    """

    def test_only_dated_records_are_exempt(self):
        assert set(_HISTORICAL) == set(), (
            f'the exemption has grown to {sorted(_HISTORICAL)}. A document that needs it is '
            'either a dated record — in which case say so in its own text — or it is '
            'stale and should be fixed.'
        )

    @pytest.mark.parametrize('name, word', sorted(_HISTORICAL.items()))
    def test_the_exempt_document_says_what_it_is(self, name: str, word: str):
        """The exemption is claimed by the document, not granted to it:
        its own opening has to say it is a record of a moment."""
        prose = ' '.join((_ROOT / 'docs' / name).read_text().split())

        assert word in prose[:2000].lower(), (
            f'{name} does not present itself as a {word} record in its opening, so the '
            'exemption is not justified'
        )

    @pytest.mark.parametrize('name', sorted(_HISTORICAL))
    def test_the_exemption_is_still_needed(self, name: str):
        """If a log stops naming anything deleted, the hole should
        close rather than sit there waiting to hide the next one."""
        doc = _ROOT / 'docs' / name
        quoted = [p for p in _PATH.findall(doc.read_text()) if '*' not in p and '{' not in p and '...' not in p]
        missing = sorted({p for p in quoted if not (_ROOT / p).exists()})

        assert missing, (
            f'{name} no longer quotes any path that is gone, so the historical '
            'exemption is protecting nothing and should be removed from _HISTORICAL.'
        )
