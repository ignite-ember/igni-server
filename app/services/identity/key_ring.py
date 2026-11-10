"""The keys this process signs and verifies with, held in memory.

Signing happens on every sign-in and every token request, and verifying
on every authenticated request, so neither can afford a database read.
The ring is loaded at start-up and reloaded every few minutes; a staged
key (see `app.db.sql.models.signing_key`) activates later than that, so
every replica has it before any replica signs with it.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import logging

from app.db.sql.models.signing_key import SigningKey
from app.db.sql.signing_keys import SigningKeyDB
from app.services.identity.jwk import JwkSet, PublicJwk, RsaKeyPair
from app.services.secrets import decrypt_secret
from app.utils.singleton import SingletonMeta

logger = logging.getLogger(__name__)

#: How often each replica re-reads the keys. A staged key activates
#: after twice this, and `tools/rotate_signing_key.py` says so.
RELOAD_EVERY = timedelta(minutes=5)

#: The longest any igni token lives — a session. A superseded key stays
#: published this long, plus a day for clocks and caches.
LONGEST_TOKEN = timedelta(days=30)
KEEP_SUPERSEDED_FOR = LONGEST_TOKEN + timedelta(days=1)


class KeyRingNotLoaded(RuntimeError):
    """A token was signed or checked before start-up loaded the keys."""


@dataclass(frozen=True, slots=True, kw_only=True)
class KeyRing:
    """One signing key and every key still published. Immutable: a reload
    builds a new ring rather than changing the one being signed with."""

    kid: str
    signer: RsaKeyPair
    published: JwkSet

    @classmethod
    def from_rows(cls, rows: list[SigningKey], *, now: datetime | None = None) -> KeyRing:
        now = now or datetime.now(timezone.utc)
        active = [row for row in rows if row.activates_at <= now]
        if not active:
            raise KeyRingNotLoaded('no signing key has activated yet')
        current = max(active, key=lambda row: row.activates_at)
        return cls(
            kid=current.kid,
            signer=RsaKeyPair.from_pem(decrypt_secret(current.encrypted_private_key)),
            published=JwkSet(keys=[PublicJwk.from_mapping(dict(row.public_jwk)) for row in rows]),
        )

    @classmethod
    def generated(cls) -> KeyRing:
        """A ring with one fresh key and no database. For tests and tools."""
        pair = RsaKeyPair.generate()
        public = pair.public_jwk()
        return cls(kid=public.kid, signer=pair, published=JwkSet(keys=[public]))

    def private_pem(self) -> str:
        return self.signer.private_pem()

    def verifying_key(self, kid: str) -> PublicJwk | None:
        return self.published.find(kid)


class SigningKeys:
    """A ring loaded from the store and refreshed in the background."""

    def __init__(self, *, store: SigningKeyDB | None = None, reload_every: timedelta = RELOAD_EVERY) -> None:
        self._store = store or SigningKeyDB()
        self._reload_every = reload_every
        self._ring: KeyRing | None = None

    def ring(self) -> KeyRing:
        if self._ring is None:
            raise KeyRingNotLoaded('the signing keys are loaded at start-up; this ran before that')
        return self._ring

    def install(self, ring: KeyRing | None) -> None:
        self._ring = ring

    async def load(self) -> KeyRing:
        """Generate the first key if there is none, then read them all."""
        await self._store.ensure()
        self._ring = KeyRing.from_rows(await self._store.all())
        return self._ring

    async def reload_forever(self) -> None:
        """Re-read the keys every `reload_every`, and prune old ones.

        A failed reload keeps the ring it had: the keys it holds were
        valid a few minutes ago, and refusing to sign because the
        database blinked would take sign-in down with it.
        """
        while True:
            await asyncio.sleep(self._reload_every.total_seconds())
            try:
                await self._store.prune(outlived_by=KEEP_SUPERSEDED_FOR)
                self._ring = KeyRing.from_rows(await self._store.all())
            except Exception:  # noqa: BLE001 — a background loop must survive one bad pass
                logger.exception('Could not reload the signing keys; keeping the ones already loaded')


class ProcessSigningKeys(SigningKeys, metaclass=SingletonMeta):
    """The process's one :class:`SigningKeys`.

    A singleton, like `ProcessTelemetry` and for the same reason: every
    token minted or checked anywhere in the process must use the ring
    start-up loaded, and token checks run deep in dependencies that have
    no app in hand to carry an instance.
    """
