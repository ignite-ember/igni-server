"""A release trusts Ignite Ember's release key and nothing else, whatever its settings say.

The test key's private half is in this repository, so a release that could
be told to trust it would let anybody sign their own licence. Nothing in
the shipped package names it (`app/services/licence/keys.py`): the suite hands it to
its gates itself, and a source checkout trusts it for local development
only in `dev`, only from the checkout's own `tests/`, and never once the
image build has written the release marker. `tools/licence/check_release_image.sh`
holds the built image to the same.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path

from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
import pytest

from app.services.licence import (
    keys,
    LicenceSignatureInvalid,
    LicenceUnknownKey,
    LicenceVerifier,
    Module,
    RELEASE_KEYS,
    TrustedKeys,
)
from app.services.licence.format import Entitlement
from tests.licence_kit import EXPIRES, FIXTURES, payload, signed, signing_key, TEST_KEY_ID, TEST_KEYS

ROOT = Path(__file__).resolve().parents[1]
#: Everything `containers/be/Dockerfile` copies from the repository into the image.
SHIPPED = [
    ROOT / 'app',
    ROOT / 'alembic',
    ROOT / 'alembic_tenant',
    ROOT / 'alembic.ini',
    ROOT / 'tools' / 'rotate_secret_key.py',
    ROOT / 'tools' / 'smoke_provider.py',
]


def _test_licence() -> bytes:
    return signed(payload({Module.CODEINDEX: Entitlement(users=50, expires=EXPIRES)}))


def _verify(app_env: str) -> str:
    return LicenceVerifier(TrustedKeys.for_environment(app_env)).verify(_test_licence()).licence_id


def _shipped_files() -> list[Path]:
    files: list[Path] = []
    for path in SHIPPED:
        files += (
            [path] if path.is_file() else [p for p in path.rglob('*') if p.is_file() and '__pycache__' not in p.parts]
        )
    return files


def test_the_dev_keys_file_is_the_public_half_of_the_test_signing_key() -> None:
    public = signing_key().public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)

    assert TEST_KEYS == {TEST_KEY_ID: base64.urlsafe_b64encode(public).rstrip(b'=').decode()}
    assert TEST_KEY_ID not in RELEASE_KEYS
    assert TEST_KEYS[TEST_KEY_ID] not in RELEASE_KEYS.values()


def test_nothing_the_image_copies_names_the_test_key() -> None:
    raw = base64.urlsafe_b64decode(TEST_KEYS[TEST_KEY_ID] + '=')
    needles = [TEST_KEY_ID.encode(), TEST_KEYS[TEST_KEY_ID].encode(), raw]
    files = _shipped_files()

    assert len(files) > 50, 'the scan found the shipped tree'
    assert [str(f.relative_to(ROOT)) for f in files if any(n in f.read_bytes() for n in needles)] == []


def test_the_image_build_writes_the_release_marker() -> None:
    dockerfile = (ROOT / 'containers' / 'be' / 'Dockerfile').read_text()
    marker = keys.RELEASE_MARKER.relative_to(ROOT).as_posix()

    assert f'RUN touch {marker}' in dockerfile
    assert dockerfile.index(f'RUN touch {marker}') > dockerfile.index('COPY ./app /app/app')
    assert dockerfile.index(f'RUN touch {marker}') < dockerfile.index('\nUSER ')


def test_a_checkout_in_development_trusts_the_test_key() -> None:
    assert not keys.RELEASE_MARKER.exists(), 'a source checkout is not a release'
    assert _verify('dev') == 'IE-TEST-0042'


@pytest.mark.parametrize('app_env', ['prod', 'stage', '', 'development', 'test'])
def test_no_other_environment_trusts_it(app_env: str) -> None:
    with pytest.raises(LicenceUnknownKey):
        _verify(app_env)


def test_a_release_never_trusts_it_even_in_development(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    marker = tmp_path / 'RELEASE'
    marker.touch()
    monkeypatch.setattr(keys, 'RELEASE_MARKER', marker)

    assert TrustedKeys.dev_keys_file() is not None, 'the checkout still has the file; the marker alone refuses it'
    with pytest.raises(LicenceUnknownKey):
        _verify('dev')


def test_outside_a_checkout_a_mounted_dev_keys_file_is_not_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The image's layout, /app/app/services/licence, with the file mounted where a checkout keeps it.
    package = tmp_path / 'app' / 'app' / 'services' / 'licence'
    package.mkdir(parents=True)
    mounted = tmp_path / 'app' / keys.DEV_KEYS_FILE
    mounted.parent.mkdir(parents=True)
    mounted.write_text(json.dumps(TEST_KEYS))
    monkeypatch.setattr(keys, 'PACKAGE_DIR', package)
    monkeypatch.setattr(keys, 'RELEASE_MARKER', package / 'RELEASE')

    assert TrustedKeys.dev_keys_file() is None
    with pytest.raises(LicenceUnknownKey):
        _verify('dev')


def test_a_dev_keys_file_cannot_stand_in_for_a_release_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    package = tmp_path / 'app' / 'services' / 'licence'
    package.mkdir(parents=True)
    (tmp_path / 'pyproject.toml').touch()
    dev = tmp_path / keys.DEV_KEYS_FILE
    dev.parent.mkdir(parents=True)
    dev.write_text(json.dumps({'ie-2026': TEST_KEYS[TEST_KEY_ID]}))
    monkeypatch.setattr(keys, 'PACKAGE_DIR', package)
    monkeypatch.setattr(keys, 'RELEASE_MARKER', package / 'RELEASE')

    assert TrustedKeys.development() == {}
    forged = signed(payload({Module.CODEINDEX: Entitlement(users=50, expires=EXPIRES)}), key_id='ie-2026')
    with pytest.raises(LicenceSignatureInvalid):
        LicenceVerifier(TrustedKeys.for_environment('dev')).verify(forged)


def test_the_suite_s_own_verifier_needs_no_environment() -> None:
    assert LicenceVerifier(TrustedKeys.parse(TEST_KEYS)).verify((FIXTURES / 'golden.licence.json').read_bytes())
