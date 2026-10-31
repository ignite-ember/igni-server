"""Storing revoked sign-ins. See `app.db.sql.models.revoked_grant`."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert

from app.db.sql import SQLAlchemySessionManager
from app.db.sql.models.revoked_grant import RevokedGrant
from app.utils.singleton import SingletonMeta


class RevokedGrantDB(SQLAlchemySessionManager, metaclass=SingletonMeta):
    async def revoke(self, *, grant_id: str, client_id: str, until: datetime) -> None:
        """Stop every refresh token of this sign-in until `until`. Idempotent.

        Rows nothing could still use are pruned on the way: revocations
        are rare, and pruning here needs no job of its own.
        """
        now = datetime.now(timezone.utc)
        async with self.session_scope() as session:
            await session.execute(delete(RevokedGrant).where(RevokedGrant.expires_at <= now))
            await session.execute(
                insert(RevokedGrant)
                .values(grant_id=grant_id, client_id=client_id, revoked_at=now, expires_at=until)
                .on_conflict_do_nothing(index_elements=['grant_id'])
            )

    async def is_revoked(self, grant_id: str) -> bool:
        async with self.session_scope() as session:
            found = await session.execute(select(RevokedGrant.grant_id).where(RevokedGrant.grant_id == grant_id))
            return found.first() is not None
