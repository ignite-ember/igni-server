"""Licences for tests: the test key pair, a fake clock, and a licence on disk.

The test key (`tests/fixtures/licence/test-signing-key.pem`) is the one
`app.services.licence.keys.TEST_KEYS` names. Anybody can sign with it,
which is why it is trusted only with `APP_ENV=dev`.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import load_pem_private_key

from app.services.licence import (
    Entitlement,
    LicenceFile,
    LicenceKind,
    LicencePayload,
    LicenceSigner,
    LicenceVerifier,
    Module,
    TEST_KEY_ID,
    TrustedKeys,
)
from app.services.licence.keys import TEST_KEYS

FIXTURES = Path(__file__).parent / 'fixtures' / 'licence'
#: When every test licence's modules expire unless a test says otherwise.
EXPIRES = datetime(2027, 10, 29, tzinfo=timezone.utc)


def signing_key() -> Ed25519PrivateKey:
    key = load_pem_private_key((FIXTURES / 'test-signing-key.pem').read_bytes(), password=None)
    assert isinstance(key, Ed25519PrivateKey)
    return key


def verifier() -> LicenceVerifier:
    return LicenceVerifier(TrustedKeys.parse(TEST_KEYS))


def payload(
    modules: dict[Module, Entitlement] | None = None,
    *,
    kind: LicenceKind = LicenceKind.COMMERCIAL,
    licence_id: str = 'IE-TEST-0042',
) -> LicencePayload:
    return LicencePayload(
        format=1,
        licence_id=licence_id,
        kind=kind,
        customer='Example Bank N.V.',
        issued=EXPIRES - timedelta(days=365),
        modules=modules or {Module.CODEINDEX: Entitlement(users=100, expires=EXPIRES)},
    )


def signed(licence: LicencePayload, *, key: Ed25519PrivateKey | None = None, key_id: str = TEST_KEY_ID) -> bytes:
    return LicenceSigner(key or signing_key(), key_id=key_id).sign(licence)


class FakeClock:
    """A clock a test moves by hand."""

    def __init__(self, now: datetime) -> None:
        self._now = now

    def now(self) -> datetime:
        return self._now

    def set(self, now: datetime) -> None:
        self._now = now

    def advance(self, by: timedelta) -> None:
        self._now += by


def licence_file(path: Path, clock: FakeClock) -> LicenceFile:
    return LicenceFile(path, verifier=verifier(), clock=clock)
