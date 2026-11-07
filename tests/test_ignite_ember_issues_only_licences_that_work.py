"""The issuing tool: what it signs verifies, and what would not work for a customer is refused."""

from __future__ import annotations

import os
from pathlib import Path
import stat
import sys

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
import pytest

from app.services.licence import (
    Base64Url,
    LicenceReading,
    LicenceState,
    LicenceStatus,
    LicenceVerifier,
    Module,
    RELEASE_KEYS,
    TEST_KEY_ID,
    TrustedKeys,
)
from app.services.licence.keys import TEST_KEYS
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


# ── The release key in Cloud KMS ────────────────────────────────────────
#
# `gcloud` is replaced by a stand-in that signs with the test key, the way
# KMS signs with the real one: the tool hands it a file and a key version,
# and reads a signature and a public key back. No network, no account.

_VERSION = 'projects/p/locations/europe-west4/keyRings/r/cryptoKeys/licence-test/cryptoKeyVersions/1'
_FAKE_GCLOUD = f"""#!{sys.executable}
import pathlib, sys
from cryptography.hazmat.primitives import serialization
key = serialization.load_pem_private_key(pathlib.Path({KEY!r}).read_bytes(), password=None)
args = dict(a.split('=', 1) for a in sys.argv[1:] if a.startswith('--') and '=' in a)
pathlib.Path(sys.argv[0] + '.log').open('a').write(' '.join(sys.argv[1:]) + '\\n')
if sys.argv[2] == 'asymmetric-sign':
    pathlib.Path(args['--signature-file']).write_bytes(key.sign(pathlib.Path(args['--input-file']).read_bytes()))
else:
    pathlib.Path(args['--output-file']).write_bytes(key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo))
"""


@pytest.fixture
def fake_gcloud(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    bin_dir = tmp_path / 'bin'
    bin_dir.mkdir()
    gcloud = bin_dir / 'gcloud'
    gcloud.write_text(_FAKE_GCLOUD)
    gcloud.chmod(0o755)
    monkeypatch.setenv('PATH', f'{bin_dir}{os.pathsep}{os.environ["PATH"]}')
    return gcloud


def _issue_with_kms(out: Path, key_id: str) -> int:
    return main(
        ['issue', '--kms-key', _VERSION, '--key-id', key_id, '--licence-id', 'IE-T-2', '--customer', 'Example']
        + ['--issued', '2026-10-29', '--expires', '2027-10-29', '--module', 'codeindex=100', '--out', str(out)]
    )


def test_the_kms_key_signs_a_licence_that_verifies(
    tmp_path: Path, fake_gcloud: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The key is named by its parts, which is all gcloud takes, and the
    licence is checked against RELEASE_KEYS before it is written."""
    monkeypatch.setitem(RELEASE_KEYS, 'ie-test', TEST_KEYS[TEST_KEY_ID])
    out = tmp_path / 'l.json'

    assert _issue_with_kms(out, 'ie-test') == 0

    assert LicenceVerifier(TrustedKeys.parse({'ie-test': TEST_KEYS[TEST_KEY_ID]})).verify(out.read_bytes())
    call = Path(f'{fake_gcloud}.log').read_text()
    for part in ('--project=p', '--location=europe-west4', '--keyring=r', '--key=licence-test', '--version=1'):
        assert part in call


def test_a_kms_key_that_is_not_the_release_key_it_claims_writes_nothing(
    tmp_path: Path, fake_gcloud: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A key id whose RELEASE_KEYS entry is another key: no customer
    release would accept the file, so it is never written."""
    other = Ed25519PrivateKey.generate().public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    monkeypatch.setitem(RELEASE_KEYS, 'ie-test', Base64Url.encode(other))
    out = tmp_path / 'l.json'

    assert _issue_with_kms(out, 'ie-test') == 2
    assert not out.exists()


def test_the_kms_key_signs_only_under_a_release_key_id(tmp_path: Path, fake_gcloud: Path) -> None:
    out = tmp_path / 'l.json'

    assert _issue_with_kms(out, TEST_KEY_ID) == 2
    assert not out.exists()
    assert not Path(f'{fake_gcloud}.log').exists()  # refused before anything was signed
