"""Storing igni's signing keys. See `app.db.sql.models.signing_key`."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging

from sqlalchemy import delete, select, text

from app.db.sql import SQLAlchemySessionManager
from app.db.sql.models.signing_key import SigningKey
from app.services.identity.jwk import RsaKeyPair
from app.services.secrets import encrypt_secret
from app.utils.singleton import SingletonMeta

logger = logging.getLogger(__name__)

#: Serialises key creation across replicas starting together. Any
#: constant nobody else uses; see `AdvisoryLockDB` for the others.
_ENSURE_LOCK = 0x1611_51_6E


class SigningKeyDB(SQLAlchemySessionManager, metaclass=SingletonMeta):
    async def ensure(self) -> None:
        """Make sure a key is signing; generate the first one if not.

        Under a transaction-scoped advisory lock, so two replicas booting
        against an empty table write one key between them rather than
        two that each sign half the tokens.
        """
        async with self.session_scope() as session:
            await session.execute(text('SELECT pg_advisory_xact_lock(:k)'), {'k': _ENSURE_LOCK})
            # After the lock, not before: a replica that waited must
            # compare against a clock later than the key the winner wrote.
            now = datetime.now(timezone.utc)
            active = (
                await session.execute(select(SigningKey.kid).where(SigningKey.activates_at <= now).limit(1))
            ).scalar()
            if active is not None:
                return
            session.add(self._new_key(activates_at=now))
            logger.info('Generated the first signing key for this deployment')

    async def stage(self, *, activates_in: timedelta) -> SigningKey:
        """Add a key that starts signing after `activates_in`.

        It is published at once, which is why the delay exists: see the
        model's note on rotation.
        """
        key = self._new_key(activates_at=datetime.now(timezone.utc) + activates_in)
        async with self.session_scope() as session:
            session.add(key)
        return key

    async def all(self) -> list[SigningKey]:
        async with self.session_scope() as session:
            result = await session.execute(select(SigningKey).order_by(SigningKey.activates_at))
            return list(result.scalars().all())

    async def prune(self, *, outlived_by: timedelta) -> list[str]:
        """Delete keys superseded longer ago than `outlived_by`.

        A key is superseded when a newer one activates. Once that was
        longer ago than the longest-lived token, nothing it signed can
        still be valid, and publishing it any longer only widens what a
        leaked old key could be used for.
        """
        now = datetime.now(timezone.utc)
        keys = await self.all()
        gone: list[str] = []
        for older, newer in zip(keys, keys[1:]):
            if newer.activates_at <= now - outlived_by:
                gone.append(older.kid)
        if gone:
            async with self.session_scope() as session:
                await session.execute(delete(SigningKey).where(SigningKey.kid.in_(gone)))
        return gone

    @staticmethod
    def _new_key(*, activates_at: datetime) -> SigningKey:
        pair = RsaKeyPair.generate()
        public = pair.public_jwk()
        return SigningKey(
            kid=public.kid,
            algorithm='RS256',
            encrypted_private_key=encrypt_secret(pair.private_pem()),
            public_jwk=public.as_dict(),
            activates_at=activates_at,
        )
