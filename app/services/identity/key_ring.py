"""The keys this process signs and verifies with, held in memory.

Signing happens on every sign-in and every token request, and verifying
on every authenticated request, so neither can afford a database read.
The ring is loaded at start-up and reloaded every few minutes; a staged
key (see `app.db.sql.models.signing_key`) activates later than that, so
every replica has it before any replica signs with it.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import logging

from app.db.sql.models.signing_key import SigningKey
from app.db.sql.signing_keys import SigningKeyDB
from app.services.identity.jwk import JwkSet, PublicJwk, RsaKeyPair
from app.services.secrets import decrypt_secret

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


class KeyRing:
    """One signing key and every key still published."""

    def __init__(self, *, kid: str, signer: RsaKeyPair, published: JwkSet) -> None:
        self.kid = kid
        self.signer = signer
        self.published = published

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
    """The process's ring: loaded once, refreshed in the background."""

    _ring: KeyRing | None = None

    @classmethod
    def ring(cls) -> KeyRing:
        if cls._ring is None:
            raise KeyRingNotLoaded('the signing keys are loaded at start-up; this ran before that')
        return cls._ring

    @classmethod
    def install(cls, ring: KeyRing | None) -> None:
        cls._ring = ring

    @classmethod
    async def load(cls) -> KeyRing:
        """Generate the first key if there is none, then read them all."""
        store = SigningKeyDB()
        await store.ensure()
        cls._ring = KeyRing.from_rows(await store.all())
        return cls._ring

    @classmethod
    async def reload_forever(cls) -> None:
        """Re-read the keys every `RELOAD_EVERY`, and prune old ones.

        A failed reload keeps the ring it had: the keys it holds were
        valid a few minutes ago, and refusing to sign because the
        database blinked would take sign-in down with it.
        """
        while True:
            await asyncio.sleep(RELOAD_EVERY.total_seconds())
            try:
                await SigningKeyDB().prune(outlived_by=KEEP_SUPERSEDED_FOR)
                cls._ring = KeyRing.from_rows(await SigningKeyDB().all())
            except Exception:  # noqa: BLE001 — a background loop must survive one bad pass
                logger.exception('Could not reload the signing keys; keeping the ones already loaded')
