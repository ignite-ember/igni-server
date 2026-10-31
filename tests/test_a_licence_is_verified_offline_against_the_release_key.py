"""A licence file is Ignite Ember's signature over a payload, checked with no network.

Pins the format every component reads (docs/licence-file.md): the golden
file in `tests/fixtures/licence/` is copied into each paid module's
tests and into the Fleet gateway's, and each must read it the same way.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import pytest

from app.services.licence import (
    Base64Url,
    Entitlement,
    LicenceKind,
    LicenceMalformed,
    LicencePayload,
    LicenceSignatureInvalid,
    LicenceUnknownKey,
    LicenceVerifier,
    Module,
    RELEASE_KEYS,
    TEST_KEY_ID,
    TrustedKeys,
)
from tests.licence_kit import EXPIRES, FIXTURES, payload, signed, verifier

GOLDEN_PAYLOAD = (
    '{"format":1,"licence_id":"IE-TEST-0001","kind":"commercial","customer":"Example Bank N.V.",'
    '"issued":"2026-10-29T00:00:00Z","modules":{"codeindex":{"users":100,"expires":"2027-10-29T00:00:00Z"},'
    '"artifacts":{"users":50,"expires":"2027-10-29T00:00:00Z"},"fleet":{"users":200,"expires":"2027-10-29T00:00:00Z"},'
    '"factory":{"users":null,"expires":"2027-10-29T00:00:00Z"},"autopilot":{"users":50,"expires":"2027-10-29T00:00:00Z"}}}'
)


def _tampered(raw: bytes, **changes: object) -> bytes:
    envelope = json.loads(raw)
    body = json.loads(Base64Url.decode(envelope['payload']))
    body.update(changes)
    envelope['payload'] = Base64Url.encode(json.dumps(body).encode())
    return json.dumps(envelope).encode()


def test_the_golden_licence_reads_the_same_everywhere() -> None:
    raw = (FIXTURES / 'golden.licence.json').read_bytes()
    envelope = json.loads(raw)

    assert envelope['igni_licence'] == 1
    assert envelope['key_id'] == TEST_KEY_ID
    assert Base64Url.decode(envelope['payload']).decode() == GOLDEN_PAYLOAD
    licence = verifier().verify(raw)
    assert licence == LicencePayload(
        format=1,
        licence_id='IE-TEST-0001',
        kind=LicenceKind.COMMERCIAL,
        customer='Example Bank N.V.',
        issued=datetime(2026, 10, 29, tzinfo=timezone.utc),
        modules={
            Module.CODEINDEX: Entitlement(users=100, expires=EXPIRES),
            Module.ARTIFACTS: Entitlement(users=50, expires=EXPIRES),
            Module.FLEET: Entitlement(users=200, expires=EXPIRES),
            Module.FACTORY: Entitlement(expires=EXPIRES),
            Module.AUTOPILOT: Entitlement(users=50, expires=EXPIRES),
        },
    )


def test_a_changed_payload_is_refused() -> None:
    raw = signed(payload())

    with pytest.raises(LicenceSignatureInvalid):
        verifier().verify(_tampered(raw, customer='Somebody Else'))
    with pytest.raises(LicenceSignatureInvalid):
        bigger = json.loads(Base64Url.decode(json.loads(raw)['payload']))
        bigger['modules']['codeindex']['users'] = 1000
        verifier().verify(_tampered(raw, modules=bigger['modules']))


def test_a_changed_signature_is_refused() -> None:
    envelope = json.loads(signed(payload()))
    signature = bytearray(Base64Url.decode(envelope['signature']))
    signature[0] ^= 1
    envelope['signature'] = Base64Url.encode(bytes(signature))

    with pytest.raises(LicenceSignatureInvalid):
        verifier().verify(json.dumps(envelope).encode())


def test_a_licence_signed_with_another_key_under_a_trusted_id_is_refused() -> None:
    forged = signed(payload(), key=Ed25519PrivateKey.generate(), key_id=TEST_KEY_ID)

    with pytest.raises(LicenceSignatureInvalid):
        verifier().verify(forged)


def test_a_licence_signed_with_a_key_the_release_does_not_know_is_refused() -> None:
    with pytest.raises(LicenceUnknownKey):
        verifier().verify(signed(payload(), key=Ed25519PrivateKey.generate(), key_id='someone-else'))


def test_the_test_key_is_trusted_in_development_only() -> None:
    raw = signed(payload())

    assert LicenceVerifier(TrustedKeys.for_environment('dev')).verify(raw).licence_id == 'IE-TEST-0042'
    for environment in ('prod', 'stage', ''):
        with pytest.raises(LicenceUnknownKey):
            LicenceVerifier(TrustedKeys.for_environment(environment)).verify(raw)
    assert TEST_KEY_ID not in RELEASE_KEYS


@pytest.mark.parametrize(
    'raw', [b'', b'not json', b'{}', b'{"igni_licence": 2, "key_id": "k", "payload": "e30", "signature": "AA"}']
)
def test_something_that_is_not_a_licence_is_malformed(raw: bytes) -> None:
    with pytest.raises(LicenceMalformed):
        verifier().verify(raw)


@pytest.mark.parametrize(
    ('module', 'users', 'kind'),
    [
        (Module.CODEINDEX, 60, LicenceKind.COMMERCIAL),  # not a band
        (Module.ARTIFACTS, 1500, LicenceKind.COMMERCIAL),  # above 1,000 only in thousands
        (Module.FLEET, None, LicenceKind.COMMERCIAL),  # a banded module needs its band
        (Module.FACTORY, 50, LicenceKind.COMMERCIAL),  # the factory is flat
        (Module.CODEINDEX, 50, LicenceKind.EVALUATION),  # an evaluation is for 25 at most
    ],
)
def test_a_payload_outside_the_bands_cannot_be_made(module: Module, users: int | None, kind: LicenceKind) -> None:
    with pytest.raises(ValueError):
        payload({module: Entitlement(users=users, expires=EXPIRES)}, kind=kind)


@pytest.mark.parametrize('users', [50, 100, 200, 500, 1000, 2000, 7000])
def test_every_band_in_the_catalogue_is_accepted(users: int) -> None:
    assert (
        payload({Module.CODEINDEX: Entitlement(users=users, expires=EXPIRES)}).modules[Module.CODEINDEX].users == users
    )
