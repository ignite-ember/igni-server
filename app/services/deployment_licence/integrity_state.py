"""The deployment's one licence-integrity file: the high-water mark, sealed and tamper-evident.

This is the centralised version of the licence clock. igni server holds
the deployment's *single* mark — the latest time it has seen — and the
paid modules and the Fleet gateway read it from here (they no longer keep
their own). It is kept in one small binary file on a writable, persisted
volume (`settings.igni_state_dir`), not in a database table, so that:

* it survives a database restore to an old backup (which would otherwise
  wind every component's mark back together), and
* an operator who edits or deletes it to revive a lapsed licence leaves
  a trace that an auditor can see (`events`), rather than a clean row.

**At rest.** The file is AES-256-GCM. The key is derived in code
(`_derive_key`) from a per-deployment install secret — 16 random bytes
generated on the first start and written into the file's header — and a
constant compiled into this module (`_PEPPER`, `_SALT`). This is
*obfuscation plus integrity, not secrecy*: the application must read the
file to enforce the licence, so anyone holding both the file and this
binary can derive the key. What it buys is that the bytes carry no
readable marker, a single edited byte fails the GCM tag, and a replaced
or truncated file is detected — tamper leaves evidence. The honest
backstop against deliberate, coordinated tampering remains the contract
and the audit right (docs/licence-file.md, "The band").

**Replay.** The file keeps a short, bounded history and a write counter.
A deployment that clearly has a past — newer `licence:daily` audit rows
than the file's mark — but presents an older or empty file is treated as
tamper, never as a clean first start: the mark is taken from the audit
trail and a tamper event is recorded.

**Multi-replica.** The file is the single source of truth, so with more
than one API replica it must sit on a *shared* ReadWriteMany volume;
writes are serialised by a Postgres advisory lock so only one replica
rewrites it at a time. Where ReadWriteMany is not available the chart
pins `api.replicaCount: 1` (docs/licence-file.md, "The clock").
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import secrets

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.hashes import SHA256
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from sqlalchemy import func, select, text

from app.db.sql import SQLAlchemySessionManager
from app.db.sql.advisory_locks import AdvisoryLockDB
from app.db.sql.models.audit_event import DeploymentAuditEvent
from app.services.licence import HighWaterMark

logger = logging.getLogger(__name__)

#: The route of the daily licence record (`deployment_licence.daily_audit.DAILY_ROUTE`), spelt out so the
#: storage layer does not import the service layer.
_DAILY_ROUTE = 'licence:daily'

#: The file's name on disk. Deliberately non-descriptive: nothing on the volume says "licence" or "clock".
STATE_FILENAME = '.igni-dstate'

#: One format byte, then the install secret, then the GCM nonce, then ciphertext+tag. No readable marker.
_FORMAT = 1
_SECRET_LEN = 16
_NONCE_LEN = 12

#: Compiled-in halves of the at-rest key. Not secret (see the module docstring); they bind the key to this build.
_PEPPER = bytes.fromhex('a033deb89bede43c8e7b1cfce6965d8cb47bc62cf1cf834f02539236ec6aa15c')
_SALT = bytes.fromhex('8d2608c31d9af005b1de72260fbe5b33')
_KDF_INFO = b'igni licence integrity v1'

#: How many events the file keeps. Enough to show an auditor a run of tampering; small enough to stay tiny.
_MAX_EVENTS = 64
#: The advisory-lock key that serialises writers across replicas. Stable, and distinct from every other.
_LOCK_KEY = 0x1911_3_D57A7E


class IntegrityEventKind:
    """Why an event was written."""

    SEED = 'seed'
    ADVANCE = 'advance'
    TAMPER = 'tamper'


@dataclass(frozen=True)
class IntegrityEvent:
    at: datetime
    kind: str
    detail: str

    def as_json(self) -> dict[str, str]:
        return {'at': self.at.astimezone(timezone.utc).isoformat(), 'kind': self.kind, 'detail': self.detail}

    @staticmethod
    def from_json(raw: dict[str, str]) -> IntegrityEvent:
        return IntegrityEvent(at=_parse(raw['at']), kind=raw['kind'], detail=raw['detail'])


@dataclass
class IntegrityState:
    """What the file holds, once decrypted."""

    install_id: str
    mark: datetime | None = None
    seq: int = 0
    events: list[IntegrityEvent] = field(default_factory=list)
    #: True while this process has seen the file tampered with since it started. Not written to disk.
    tampered: bool = False

    def record(self, kind: str, detail: str, *, at: datetime | None = None) -> None:
        self.events.append(IntegrityEvent(at=at or _now(), kind=kind, detail=detail))
        if len(self.events) > _MAX_EVENTS:
            del self.events[: len(self.events) - _MAX_EVENTS]

    def recent_tamper(self) -> list[IntegrityEvent]:
        return [event for event in self.events if event.kind == IntegrityEventKind.TAMPER]


class IntegrityStateError(Exception):
    """The integrity file cannot be read or written where it is configured."""


class EncryptedIntegrityStore:
    """igni server's `ClockStore`: the mark in the encrypted file, with the database as a second witness.

    `database_now` and `seed` read Postgres — the mark only moves on while
    this process's clock and the database's agree (the 48-hour guard in
    `LicenceClock`), and the first mark is seeded from the audit trail.
    `stored` and `advance` read and write the file, and detect tamper.

    Composed rather than a `SQLAlchemySessionManager` subclass, so that it
    is bound to one state directory and is freely instantiable (the session
    manager it borrows is the process-wide singleton).
    """

    def __init__(
        self,
        directory: Path,
        *,
        sessions: SQLAlchemySessionManager | None = None,
        locks: AdvisoryLockDB | None = None,
    ) -> None:
        self._path = directory / STATE_FILENAME
        self._sessions = sessions or SQLAlchemySessionManager()
        self._locks = locks or AdvisoryLockDB()
        #: The last state `load` resolved, including any tamper finding, so the admin view reflects it.
        self._last: IntegrityState | None = None

    @property
    def path(self) -> Path:
        return self._path

    # ----- the ClockStore protocol ------------------------------------------------------------------

    async def database_now(self) -> datetime:
        async with self._sessions.session_scope() as session:
            value = (await session.execute(text('SELECT now()'))).scalar_one()
        return _utc(value)

    async def seed(self) -> datetime | None:
        """When the newest daily licence record was written, in the deployment's own trail."""
        query = select(func.max(DeploymentAuditEvent.occurred_at)).where(DeploymentAuditEvent.route == _DAILY_ROUTE)
        async with self._sessions.session_scope() as session:
            value = await session.scalar(query)
        return None if value is None else _utc(value)

    async def stored(self) -> datetime | None:
        """The mark the file holds, after checking it against the deployment's history for tamper."""
        return (await self.load()).mark

    async def advance(self, seen: datetime) -> datetime:
        """Raise the mark in the file to `seen` (never lower it). Serialised across replicas by the lock."""
        if not await self._locks.try_acquire(_LOCK_KEY):
            # Another replica owns the file this round; it will carry the mark forward. Read what is there.
            return (await self.load()).mark or seen
        try:
            state = self._read()
            before = state.mark
            if state.mark is None or seen > state.mark:
                state.mark = seen
                if before is not None:
                    at = seen.astimezone(timezone.utc)
                    state.record(IntegrityEventKind.ADVANCE, f'mark raised to {at:%Y-%m-%dT%H:%M:%SZ}')
                self._write(state)
            return state.mark
        finally:
            await self._locks.release(_LOCK_KEY)

    # ----- integrity -------------------------------------------------------------------------------

    async def load(self) -> IntegrityState:
        """Read and verify the file, seeding or recording tamper as the deployment's history demands."""
        seed = await self._trustworthy_seed()
        try:
            state = self._read()
        except _Missing:
            state = await self._first_sight(seed)
        except _Corrupt as exc:
            state = await self._after_corruption(seed, str(exc))
        else:
            state = await self._after_read(state, seed)
        self._last = state
        return state

    async def _trustworthy_seed(self) -> datetime | None:
        """The audit-trail seed, unless it is further ahead than the database's clock could have reached.

        A `licence:daily` row written by a clock set wrongly into the future
        is not history the deployment really has, so it neither seeds the
        mark nor counts as evidence that the file was tampered with (the
        48-hour guard, `HighWaterMark.believable`).
        """
        seed = await self.seed()
        if seed is None:
            return None
        if HighWaterMark.believable(seed, database_now=await self.database_now()):
            return seed
        return None

    def integrity(self) -> IntegrityState:
        """What the deployment's integrity looks like now: the last resolved state, else a fresh read.

        For the admin notice and the sealed report. Prefers the state `load`
        resolved (which carries the replay/tamper finding the keeper made
        against the audit trail) over a bare re-read of the bytes.
        """
        if self._last is not None:
            return self._last
        try:
            return self._read()
        except _Missing:
            return IntegrityState(install_id='', mark=None)
        except _Corrupt:
            state = IntegrityState(install_id='', mark=None, tampered=True)
            state.record(IntegrityEventKind.TAMPER, 'the integrity file could not be read')
            return state

    async def _first_sight(self, seed: datetime | None) -> IntegrityState:
        """No file. A clean start writes one; a deployment that already has history is being tampered with."""
        if seed is None:
            state = IntegrityState(install_id=secrets.token_hex(8))
            self._write_if_owner(state)
            return state
        state = IntegrityState(install_id=secrets.token_hex(8), mark=seed, tampered=True)
        state.record(
            IntegrityEventKind.TAMPER,
            'the integrity file was missing on a deployment with licence history; the mark was taken from the trail',
        )
        logger.warning('licence: the integrity file was missing but the deployment has history; treating as tamper')
        self._write_if_owner(state)
        return state

    async def _after_corruption(self, seed: datetime | None, why: str) -> IntegrityState:
        """The file would not decrypt: edited, truncated, or written by another build. Rebuild from the trail."""
        state = IntegrityState(install_id=secrets.token_hex(8), mark=seed, tampered=True)
        state.record(IntegrityEventKind.TAMPER, f'the integrity file did not verify ({why})')
        logger.warning('licence: the integrity file did not verify (%s); treating as tamper', why)
        self._write_if_owner(state)
        return state

    async def _after_read(self, state: IntegrityState, seed: datetime | None) -> IntegrityState:
        """A readable file. A mark behind what the audit trail proves is a replayed older copy: tamper."""
        if seed is not None and (state.mark is None or seed > state.mark):
            state.tampered = True
            state.record(
                IntegrityEventKind.TAMPER,
                'the integrity file was older than the audit trail (replayed copy); mark taken from the trail',
            )
            logger.warning('licence: the integrity file is behind the audit trail; treating as a replayed copy')
            state.mark = seed
            self._write_if_owner(state)
        return state

    # ----- the bytes -------------------------------------------------------------------------------

    def _read(self) -> IntegrityState:
        try:
            raw = self._path.read_bytes()
        except FileNotFoundError as exc:
            raise _Missing() from exc
        except OSError as exc:
            raise IntegrityStateError(f'cannot read {self._path}: {exc}') from exc
        if len(raw) < 1 + _SECRET_LEN + _NONCE_LEN + 16 or raw[0] != _FORMAT:
            raise _Corrupt('header')
        secret = raw[1 : 1 + _SECRET_LEN]
        nonce = raw[1 + _SECRET_LEN : 1 + _SECRET_LEN + _NONCE_LEN]
        body = raw[1 + _SECRET_LEN + _NONCE_LEN :]
        header = raw[: 1 + _SECRET_LEN + _NONCE_LEN]
        try:
            plain = AESGCM(_derive_key(secret)).decrypt(nonce, body, header)
        except InvalidTag as exc:
            raise _Corrupt('tag') from exc
        try:
            payload = json.loads(plain)
            state = IntegrityState(
                install_id=payload['install_id'],
                mark=_parse(payload['mark']) if payload.get('mark') else None,
                seq=int(payload.get('seq', 0)),
                events=[IntegrityEvent.from_json(event) for event in payload.get('events', [])],
            )
        except (ValueError, KeyError, TypeError) as exc:
            raise _Corrupt('payload') from exc
        # Remember the secret so a rewrite keeps the deployment's identity rather than minting a new one.
        state.__dict__['_secret'] = secret
        return state

    def _write(self, state: IntegrityState) -> None:
        secret = state.__dict__.get('_secret') or secrets.token_bytes(_SECRET_LEN)
        state.seq += 1
        payload = {
            'install_id': state.install_id,
            'mark': state.mark.astimezone(timezone.utc).isoformat() if state.mark else None,
            'seq': state.seq,
            'events': [event.as_json() for event in state.events],
        }
        nonce = secrets.token_bytes(_NONCE_LEN)
        header = bytes([_FORMAT]) + secret + nonce
        body = AESGCM(_derive_key(secret)).encrypt(nonce, json.dumps(payload).encode('utf-8'), header)
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_name(f'{STATE_FILENAME}.{secrets.token_hex(4)}')
            with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'wb') as handle:
                handle.write(header + body)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, self._path)
        except OSError as exc:
            raise IntegrityStateError(f'cannot write {self._path}: {exc}') from exc
        state.__dict__['_secret'] = secret

    def _write_if_owner(self, state: IntegrityState) -> None:
        """Rewrite the file after a seed or a tamper finding, best-effort: a read-only volume must not crash boot."""
        try:
            self._write(state)
        except IntegrityStateError:
            logger.warning('licence: could not write the integrity file; the mark is held in memory', exc_info=True)


class _Missing(Exception):
    """No file yet."""


class _Corrupt(Exception):
    """The file is there but does not verify."""


def _derive_key(secret: bytes) -> bytes:
    return HKDF(algorithm=SHA256(), length=32, salt=_SALT, info=_KDF_INFO).derive(secret + _PEPPER)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse(value: str) -> datetime:
    return _utc(datetime.fromisoformat(value))


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
