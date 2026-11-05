"""`journey.sh` stops at once when the portal's Chromium is not installed.

From a fresh HOME the journey started everything, then every browser
step failed with a stack trace from browser.mjs and sixteen steps after
them failed for want of the sign-ins those steps make. CI installs it
(`npx playwright install chromium`); a person running the journey had
nothing telling them to. `chromium_ready.mjs` is the check `up` makes.
"""

from __future__ import annotations

from pathlib import Path
import shutil
import subprocess

import pytest

_CHECK = Path(__file__).resolve().parent.parent / 'tools' / 'e2e_journey' / 'chromium_ready.mjs'

pytestmark = pytest.mark.skipif(shutil.which('node') is None, reason='needs node')


def _portal(tmp_path: Path, executable: Path) -> Path:
    """A portal whose @playwright/test says Chromium is at `executable`."""
    package = tmp_path / 'portal' / 'node_modules' / '@playwright' / 'test'
    package.mkdir(parents=True)
    (package / 'package.json').write_text('{"name": "@playwright/test", "main": "index.js"}')
    (package / 'index.js').write_text('module.exports = { chromium: { executablePath: () => %r } }\n' % str(executable))
    (tmp_path / 'portal' / 'package.json').write_text('{"name": "portal"}')
    return tmp_path / 'portal'


def _check(portal: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(['node', str(_CHECK), str(portal)], capture_output=True, text=True, timeout=30)


def test_a_missing_chromium_is_named_with_the_command_that_installs_it(tmp_path):
    done = _check(_portal(tmp_path, tmp_path / 'nowhere' / 'chrome'))

    assert done.returncode != 0
    assert 'npx playwright install chromium' in done.stderr


def test_an_installed_chromium_passes(tmp_path):
    chrome = tmp_path / 'chrome'
    chrome.write_text('')

    assert _check(_portal(tmp_path, chrome)).returncode == 0


def test_up_makes_the_check_before_it_starts_anything():
    script = (_CHECK.parent / 'journey.sh').read_text()
    up = script[script.index('\nup() {') :]
    assert up.index('chromium_ready.mjs') < up.index('docker run')
