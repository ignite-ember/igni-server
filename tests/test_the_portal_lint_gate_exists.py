"""The portal's linter runs in CI, and the tree it guards is clean.

F104. `eslint` was configured, worked, and ran nowhere: `lint-portal` did
`tsc --noEmit`, `npm audit` and `npm test`, so the portal reported itself
green with 11 errors in it — three of them conditionally-called React
hooks, which is a crash rather than a style preference.

This is the same shape as F42, where `bandit` was installed and unrun,
and the same lesson: a check nobody runs is a check that does not exist.
Asserted from the server suite because that is what runs on every change
here; the portal's own suite cannot assert its absence from a workflow it
does not read.
"""

from __future__ import annotations

import pathlib
import shutil
import subprocess

import pytest
import yaml

_ROOT = pathlib.Path(__file__).resolve().parent.parent
_CI = _ROOT / '.github/workflows/ci.yml'
_PORTAL = _ROOT / 'portal'


def _lint_portal_steps() -> list[dict]:
    workflow = yaml.safe_load(_CI.read_text())
    return workflow['jobs']['lint-portal']['steps']


class TestTheGateIsWiredUp:
    def test_ci_runs_eslint_on_the_portal(self):
        commands = ' '.join(str(step.get('run', '')) for step in _lint_portal_steps())

        assert 'eslint' in commands, (
            'the lint-portal job does not run eslint. It runs tsc, npm audit and the '
            'test suite — none of which sees a conditionally-called hook.'
        )

    def test_a_warning_fails_the_build(self):
        """A warning nobody sees is an error nobody fixed."""
        commands = ' '.join(str(step.get('run', '')) for step in _lint_portal_steps())

        assert '--max-warnings 0' in commands, commands

    def test_it_runs_before_the_slow_steps(self):
        """Playwright installs a browser. Linting is seconds, so a
        syntax-level failure should not wait behind it."""
        names = [str(step.get('name', '') or step.get('run', '')) for step in _lint_portal_steps()]
        eslint_at = next(i for i, name in enumerate(names) if 'ESLint' in name or 'eslint' in name)
        playwright_at = next((i for i, name in enumerate(names) if 'Playwright' in name), len(names))

        assert eslint_at < playwright_at, names

    def test_the_config_exists_and_covers_the_source(self):
        """A gate pointed at nothing passes forever."""
        configs = list(_PORTAL.glob('eslint.config.*'))

        assert configs, 'no eslint config in the portal'
        commands = ' '.join(str(step.get('run', '')) for step in _lint_portal_steps())
        assert 'eslint src' in commands, 'eslint is run without naming the source tree'


@pytest.mark.skipif(shutil.which('npx') is None, reason='node toolchain is not installed')
class TestTheTreeIsActuallyClean:
    """Running it, not reading the workflow. The workflow assertions above
    would all pass on a tree with 11 errors in it — which is exactly the
    state this finding started from."""

    def test_eslint_reports_nothing(self):
        if not (_PORTAL / 'node_modules').is_dir():
            pytest.skip('portal dependencies are not installed')

        result = subprocess.run(
            ['npx', 'eslint', 'src', '--max-warnings', '0'],
            cwd=_PORTAL,
            capture_output=True,
            text=True,
            timeout=600,
        )

        assert result.returncode == 0, 'eslint is not clean:\n' + (result.stdout or result.stderr)[-4000:]
