"""Nothing a Dockerfile copies may be excluded by `.dockerignore`.

F148, §105. `marketplace/Dockerfile` copies `marketplace/schema.yaml`, and
`.dockerignore` excluded `marketplace`. So the deployer image could not
be built at all — `"/marketplace/schema.yaml": not found` — with the
exact command its own header documents.

Neither change was wrong. §92 added the ignore file to stop shipping a
1.3 GB context to the daemon on every build, and listed `marketplace`
because the API image does not need it. The deployer was written before
that and copies from the repository root because the chart is a sibling
directory. A correct change made another artefact false, and nothing
connected the two — which is this review's recurring shape, arriving in
the build system.

It survived because **the deployer image had never been built**. The
listing tests check the join between `schema.yaml` and the chart
thoroughly, and check nothing about whether the image assembles;
`tools/marketplace/verify.sh` now does that, and found this on its first
run.

The rule here is the cheap half of the same guarantee, and it needs no
Docker daemon: **every COPY source in every Dockerfile must survive the
ignore rules of the context it is built from.** Stated over whatever
Dockerfiles exist, so a new one is covered without anybody remembering.
"""

from __future__ import annotations

import pathlib
import re

import pytest

_ROOT = pathlib.Path(__file__).resolve().parent.parent

#: Where each Dockerfile is built from. Not guessed: each Dockerfile's
#: own header says, and getting it wrong is what this file is about.
#: `containers/be` and the portal are built from the root by CI; the
#: deployer says so in its own build instructions.
_BUILD_CONTEXTS = {
    'containers/be/Dockerfile': '.',
    'marketplace/Dockerfile': '.',
    'portal/Dockerfile': '.',
}


def _copy_sources(dockerfile: pathlib.Path) -> list[str]:
    """The build-context paths a Dockerfile copies.

    `--from=` copies come from an earlier stage rather than the context,
    so they are not the context's problem and are skipped.
    """
    sources: list[str] = []
    for line in dockerfile.read_text().splitlines():
        stripped = line.strip()
        if not re.match(r'^COPY\s', stripped, re.I):
            continue
        if '--from=' in stripped:
            continue
        parts = [p for p in stripped.split()[1:] if not p.startswith('--')]
        # The last argument is the destination inside the image.
        sources.extend(parts[:-1])
    return sources


def _ignore_rules() -> list[tuple[str, bool]]:
    """`(pattern, is_negation)` in file order. Last match wins."""
    rules = []
    for line in (_ROOT / '.dockerignore').read_text().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith('#'):
            continue
        if stripped.startswith('!'):
            rules.append((stripped[1:], True))
        else:
            rules.append((stripped, False))
    return rules


def _is_excluded(path: str) -> bool:
    """Whether `.dockerignore` drops `path` from the context.

    A deliberately simple reading of docker's rules — leading-directory
    match plus `fnmatch` — because the question being asked is coarse:
    *does anything exclude a path we copy.* A checker that modelled every
    subtlety of docker's matcher and got one wrong would be worse than
    one that is obviously approximate, so the caller compares against a
    real build (`tools/marketplace/verify.sh`) rather than trusting this
    alone.
    """
    import fnmatch

    excluded = False
    for pattern, negated in _ignore_rules():
        hit = (
            fnmatch.fnmatch(path, pattern)
            or path.startswith(pattern.rstrip('/') + '/')
            or fnmatch.fnmatch(path, pattern.rstrip('/') + '/*')
        )
        if hit:
            excluded = not negated
    return excluded


class TestTheCheckerWorks:
    """It is an approximation of docker's matcher, so its answers are
    pinned against cases taken from the real file."""

    def test_it_finds_the_dockerfiles(self):
        missing = [p for p in _BUILD_CONTEXTS if not (_ROOT / p).exists()]

        assert not missing, f'{missing} are listed here and not in the repository'

    def test_it_finds_copy_sources(self):
        sources = _copy_sources(_ROOT / 'marketplace/Dockerfile')

        assert sources == ['charts/igni', 'marketplace/schema.yaml'], sources

    def test_it_agrees_with_the_file_on_known_cases(self):
        assert _is_excluded('.venv'), 'the largest exclusion is not being matched'
        assert _is_excluded('portal/node_modules')
        assert _is_excluded('tests/conftest.py')
        assert not _is_excluded('app/main.py'), 'the application is being excluded'
        assert not _is_excluded('charts/igni'), 'the chart the deployer copies is excluded'

    def test_it_honours_a_negation(self):
        """The fix for F148 is a negation, so a checker that ignored `!`
        would report the bug as still present and be ignored in turn."""
        assert not _is_excluded('marketplace/schema.yaml')
        assert _is_excluded(
            'marketplace/README.md'
        ), 'the negation is wider than intended — only the schema needs to survive'

    def test_it_would_have_caught_the_original(self):
        """A rule that cannot be shown to fail on the input that
        motivated it is a rule nobody should trust. Replayed against the
        rules as they were, with the negation removed."""
        import fnmatch

        without_negation = [(p, n) for p, n in _ignore_rules() if not n]
        excluded = False
        for pattern, _ in without_negation:
            if fnmatch.fnmatch('marketplace/schema.yaml', pattern) or 'marketplace/schema.yaml'.startswith(
                pattern.rstrip('/') + '/'
            ):
                excluded = True

        assert excluded, (
            'with the negation removed the schema is no longer excluded, so either the '
            '`marketplace` rule has gone too and this test is checking nothing'
        )


@pytest.mark.parametrize('dockerfile', sorted(_BUILD_CONTEXTS), ids=lambda p: p)
def test_nothing_it_copies_is_excluded_from_the_context(dockerfile):
    sources = _copy_sources(_ROOT / dockerfile)
    assert sources, f'{dockerfile} copies nothing from its context — has it changed shape?'

    excluded = sorted(s for s in sources if _is_excluded(s))

    assert not excluded, (
        f'{dockerfile} copies {excluded}, which `.dockerignore` drops from the build '
        f'context. The build fails with "not found" on a path that is plainly there, '
        f'which is a confusing enough error to cost an afternoon — and it only shows up '
        f'when somebody actually builds that image.'
    )
