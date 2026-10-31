"""The issuing tool: what it signs verifies, and what would not work for a customer is refused."""

from __future__ import annotations

import os
from pathlib import Path
import stat

import pytest

from app.services.licence import LicenceReading, LicenceState, LicenceStatus, Module
from tests.licence_kit import EXPIRES, FIXTURES, verifier
from tools.licence.issue_licence import main

KEY = str(FIXTURES / 'test-signing-key.pem')


def _issue(out: Path, *modules: str, extra: tuple[str, ...] = ()) -> int:
    argv = ['issue', '--key-file', KEY, '--key-id', 'igni-test-1', '--licence-id', 'IE-T-1', '--customer', 'Example']
    argv += ['--issued', '2026-10-29', '--expires', '2027-10-29', '--out', str(out), *extra]
    for module in modules:
        argv += ['--module', module]
    return main(argv)


def test_an_issued_licence_verifies_with_its_bands_and_dates(tmp_path: Path) -> None:
    out = tmp_path / 'l.json'

    assert _issue(out, 'codeindex=100', 'artifacts=50@2026-10-29', 'factory', 'autopilot=200') == 0

    licence = verifier().verify(out.read_bytes())
    assert {module: (e.users, e.expires.date().isoformat()) for module, e in licence.modules.items()} == {
        Module.CODEINDEX: (100, '2027-10-29'),
        Module.ARTIFACTS: (50, '2026-10-29'),
        Module.FACTORY: (None, '2027-10-29'),
        Module.AUTOPILOT: (200, '2027-10-29'),
    }
    # Not renewed, kept in the licence with its old expiry: read-only, not off.
    assert (
        LicenceStatus.of(LicenceReading(payload=licence), Module.ARTIFACTS, now=EXPIRES).state is LicenceState.READ_ONLY
    )


@pytest.mark.parametrize(
    'modules',
    [('autopilot=50',), ('codeindex=60',), ('codeindex',), ('factory=50',), ('nonsense=50',)],
)
def test_a_licence_that_would_not_work_is_refused(tmp_path: Path, modules: tuple[str, ...]) -> None:
    out = tmp_path / 'l.json'

    assert _issue(out, *modules) == 2
    assert not out.exists()


def test_the_signing_key_never_goes_into_a_repository(tmp_path: Path) -> None:
    (tmp_path / 'repo' / '.git').mkdir(parents=True)

    assert main(['keygen', '--out', str(tmp_path / 'repo' / 'k.pem'), '--key-id', 'x']) == 2
    assert not (tmp_path / 'repo' / 'k.pem').exists()


def test_keygen_writes_a_private_key_only_its_owner_reads(tmp_path: Path, capsys) -> None:
    out = tmp_path / 'keys' / 'ie.pem'

    assert main(['keygen', '--out', str(out), '--key-id', 'ie-test']) == 0
    assert stat.S_IMODE(os.stat(out).st_mode) == 0o600
    assert "'ie-test': '" in capsys.readouterr().out
    assert main(['keygen', '--out', str(out), '--key-id', 'ie-test']) == 2  # never overwritten
