"""The deployment's licence-integrity file: encrypted, tamper-evident, and sealed for the vendor.

igni server keeps the one high-water mark in a small encrypted file on a
persisted volume, not a database table (docs/licence-file.md, "The clock").
These tests fail on the old database-table design: an edited byte, a
deleted file on a deployment with history, and a replayed older copy are
each detected as tamper; the bytes carry no readable marker; and the
sealed integrity report opens only with the vendor's private key.
"""

from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.hashes import SHA256
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
import pytest

from app.services.deployment_licence.integrity_state import EncryptedIntegrityStore, IntegrityEventKind
from app.services.deployment_licence.sealed_report import (
    build_report,
    REPORT_FORMAT,
    ReportUnavailable,
    seal,
    SEAL_ALG,
    seal_to_key,
)

NOW = datetime(2027, 3, 1, tzinfo=timezone.utc)
_OAEP = padding.OAEP(mgf=padding.MGF1(algorithm=SHA256()), algorithm=SHA256(), label=None)


class _AlwaysLock:
    def __init__(self, grant: bool = True) -> None:
        self._grant = grant

    async def try_acquire(self, key: int) -> bool:
        return self._grant

    async def release(self, key: int) -> None:
        return None


class FakeStore(EncryptedIntegrityStore):
    """The real file logic, with the database replaced by injected values, so no Postgres is needed."""

    def __init__(self, directory: Path, *, seed: datetime | None = None, grant: bool = True) -> None:
        super().__init__(directory, locks=_AlwaysLock(grant))
        self._seed = seed

    async def database_now(self) -> datetime:
        return NOW

    async def seed(self) -> datetime | None:
        return self._seed


@pytest.fixture
def store(tmp_path: Path) -> FakeStore:
    return FakeStore(tmp_path / 'state')


async def test_a_clean_start_with_no_history_is_not_tamper(store) -> None:
    state = await store.load()
    assert not state.tampered
    assert state.recent_tamper() == []


async def test_the_mark_round_trips_across_a_restart(tmp_path) -> None:
    first = FakeStore(tmp_path / 'state')
    await first.load()
    assert await first.advance(NOW) == NOW

    restarted = FakeStore(tmp_path / 'state')
    state = await restarted.load()
    assert state.mark == NOW
    assert not state.tampered


async def test_the_file_has_no_readable_marker_and_is_0600(store) -> None:
    await store.load()
    await store.advance(NOW)
    raw = store.path.read_bytes()
    for marker in (b'mark', b'seen', b'licence', b'clock', b'seq', b'install', b'2027', b'event'):
        assert marker not in raw, f'the integrity file leaks a plaintext marker: {marker!r}'
    assert (store.path.stat().st_mode & 0o777) == 0o600


async def test_an_edited_byte_is_detected_as_tamper(store) -> None:
    await store.load()
    await store.advance(NOW)
    raw = bytearray(store.path.read_bytes())
    raw[-1] ^= 0x01  # flip one bit of the GCM tag
    store.path.write_bytes(bytes(raw))

    reopened = FakeStore(store.path.parent, seed=NOW)
    state = await reopened.load()
    assert state.tampered
    assert any(event.kind == IntegrityEventKind.TAMPER for event in state.events)
    assert state.mark == NOW  # rebuilt from the audit trail, not trusted from the edited file


async def test_a_deleted_file_with_history_is_tamper_but_a_clean_slate_is_not(tmp_path) -> None:
    with_history = FakeStore(tmp_path / 'a', seed=NOW)
    await with_history.load()
    await with_history.advance(NOW)
    with_history.path.unlink()
    state = await FakeStore(tmp_path / 'a', seed=NOW).load()
    assert state.tampered and state.mark == NOW

    fresh = await FakeStore(tmp_path / 'b', seed=None).load()
    assert not fresh.tampered


async def test_a_replayed_older_copy_is_detected(tmp_path) -> None:
    store = FakeStore(tmp_path / 'state')
    await store.load()
    await store.advance(NOW - timedelta(days=30))
    # The audit trail proves a later time than the (replayed, older) file holds.
    state = await FakeStore(tmp_path / 'state', seed=NOW).load()
    assert state.tampered
    assert state.mark == NOW


async def test_the_sealed_report_opens_only_with_the_vendor_private_key(store) -> None:
    await store.load()
    await store.advance(NOW)
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = private.public_key().public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo).decode()

    report = build_report(
        licence_id='IE-TEST-R519',
        customer='Example Bank N.V.',
        mark=NOW,
        clock_warning=None,
        state=store.integrity(),
        modules=[],
    )
    sealed = seal(report, public_key_pem=pem, key_hint='kms://report-key')
    assert sealed.format == REPORT_FORMAT and sealed.alg == SEAL_ALG

    # The right key opens it.
    aes_key = private.decrypt(base64.b64decode(sealed.wrapped_key), _OAEP)
    opened = json.loads(
        AESGCM(aes_key).decrypt(base64.b64decode(sealed.nonce), base64.b64decode(sealed.ciphertext), None)
    )
    assert opened['licence_id'] == 'IE-TEST-R519'
    assert opened['mark'] == NOW.isoformat()

    # Any other key cannot.
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    with pytest.raises(ValueError):
        other.decrypt(base64.b64decode(sealed.wrapped_key), _OAEP)


def test_the_report_cannot_be_sealed_without_a_key() -> None:
    with pytest.raises(ReportUnavailable):
        seal({'x': 1}, public_key_pem='')


async def test_the_report_carries_the_tamper_history(tmp_path) -> None:
    store = FakeStore(tmp_path / 'state', seed=NOW)
    await store.load()
    await store.advance(NOW - timedelta(days=30))
    await FakeStore(tmp_path / 'state', seed=NOW).load()  # triggers a replay tamper event on disk
    reopened = FakeStore(tmp_path / 'state', seed=NOW)
    state = await reopened.load()

    report = build_report(
        licence_id='IE-TEST',
        customer='Example',
        mark=state.mark,
        clock_warning=None,
        state=state,
        modules=[],
    )
    assert report['tampered'] is True
    assert any(event['kind'] == IntegrityEventKind.TAMPER for event in report['events'])


async def test_only_one_replica_writes_the_file(tmp_path) -> None:
    owner = FakeStore(tmp_path / 'state', grant=True)
    await owner.load()
    await owner.advance(NOW)

    # A replica that does not hold the lock reads the mark but does not advance the file.
    non_owner = FakeStore(tmp_path / 'state', grant=False)
    assert await non_owner.advance(NOW + timedelta(days=10)) == NOW
    assert (await FakeStore(tmp_path / 'state').load()).mark == NOW


async def test_the_vendor_tool_opens_a_sealed_report_with_the_private_key(store, tmp_path) -> None:
    from cryptography.hazmat.primitives.serialization import Encoding as E, NoEncryption, PrivateFormat

    from tools.licence.open_report import open_report

    await store.load()
    await store.advance(NOW)
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    report = build_report(
        licence_id='IE-X', customer='C', mark=NOW, clock_warning=None, state=store.integrity(), modules=[]
    )
    sealed = seal_to_key(report, private.public_key())

    key_file = tmp_path / 'report.key.pem'
    key_file.write_bytes(private.private_bytes(E.PEM, PrivateFormat.PKCS8, NoEncryption()))
    opened = open_report(json.loads(sealed.model_dump_json()), kms_key=None, key_file=str(key_file))
    assert opened['licence_id'] == 'IE-X'
