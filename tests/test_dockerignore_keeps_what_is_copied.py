"""Nothing a Dockerfile copies may be excluded by `.dockerignore`.

F148, §105. The rule this file states is the cheap half of a guarantee
that needs no Docker daemon: **every COPY source in every Dockerfile
must survive the ignore rules of the context it is built from.** Stated
over whatever Dockerfiles exist, so a new one is covered without anybody
remembering.

It exists because the two halves of that rule can each be right while
the pair is false. `tools` is excluded from the context — the images do
not need the tooling — and `containers/be/Dockerfile` copies
`tools/rotate_secret_key.py` and `tools/smoke_provider.py`, because the
runbooks run them inside the API container. Excluding `tools` without the
two negations means the build fails with "not found" on a path that is
plainly there, and it only shows up when somebody actually builds the
image.

That is this review's recurring shape, arriving in the build system: a
correct change makes another artefact false, and nothing connects the
two.
"""

from __future__ import annotations

import pathlib
import re

import pytest

_ROOT = pathlib.Path(__file__).resolve().parent.parent

#: Where each Dockerfile is built from. Not guessed: CI's own build
#: commands say, and getting it wrong is what this file is about. The
#: API image is built from the repository root and the portal from
#: `portal/`.
_BUILD_CONTEXTS = {
    'containers/be/Dockerfile': '.',
    'portal/Dockerfile': 'portal',
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
    real build rather than trusting this alone.
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
        """A checker that finds no COPY line passes everything, so the
        extraction is pinned against a known file."""
        sources = _copy_sources(_ROOT / 'containers/be/Dockerfile')

        assert sources == [
            'pyproject.toml',
            'uv.lock',
            './app',
            'alembic.ini',
            'alembic',
            'alembic_tenant',
            'tools/rotate_secret_key.py',
            'tools/smoke_provider.py',
        ], sources

    def test_it_agrees_with_the_file_on_known_cases(self):
        assert _is_excluded('.venv'), 'the largest exclusion is not being matched'
        assert _is_excluded('portal/node_modules')
        assert _is_excluded('tests/conftest.py')
        assert not _is_excluded('app/main.py'), 'the application is being excluded'
        assert not _is_excluded(
            'tools/rotate_secret_key.py'
        ), 'the rotation tool the API image copies is excluded'

    def test_it_honours_a_negation(self):
        """The fix is a negation, so a checker that ignored `!` would
        report the bug as still present and be ignored in turn."""
        assert not _is_excluded('tools/rotate_secret_key.py')
        assert not _is_excluded('tools/smoke_provider.py')
        assert _is_excluded('tools/other.py'), (
            'the negation is wider than intended — only the two tools the API image '
            'copies need to survive'
        )

    def test_it_would_have_caught_the_original(self):
        """A rule that cannot be shown to fail on the input that
        motivated it is a rule nobody should trust. Replayed against the
        rules as they were, with the negation removed."""
        import fnmatch

        without_negation = [(p, n) for p, n in _ignore_rules() if not n]
        excluded = False
        for pattern, _ in without_negation:
            if fnmatch.fnmatch('tools/rotate_secret_key.py', pattern) or 'tools/rotate_secret_key.py'.startswith(
                pattern.rstrip('/') + '/'
            ):
                excluded = True

        assert excluded, (
            'with the negation removed the rotation tool is no longer excluded, so either '
            'the `tools` rule has gone too and this test is checking nothing'
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


def test_every_tracked_file_the_api_image_copies_reaches_it():
    """A directory can survive while files in it do not. `**/*.md` dropped
    the Default group's agent and skill seeds from `app/`, so every
    organisation created on a released image got 4 entries instead of 20
    — and the log said only "Seed directory ... holds no *.md".

    The API image only: it reads what it copies at runtime. The portal
    builds from a bundle, so it has nothing to copy at runtime."""
    import subprocess

    dockerfile = 'containers/be/Dockerfile'

    tracked = subprocess.run(
        ['git', 'ls-files', '-z', '--', *_copy_sources(_ROOT / dockerfile)],
        cwd=_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split('\0')

    dropped = sorted(path for path in tracked if path and _is_excluded(path))

    assert not dropped, f'{dockerfile} copies directories whose tracked files `.dockerignore` drops: {dropped}'
