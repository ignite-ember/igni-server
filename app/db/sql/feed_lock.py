"""The advisory lock that orders everything numbered for the gateway: the feed and revocations."""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


class FeedLock:
    """Held by the one transaction that numbers the feed, until it commits."""

    KEY = 0x50524F4446454544

    @classmethod
    async def take(cls, session: AsyncSession) -> None:
        """Wait for the lock; it is released when *session*'s transaction ends."""
        await session.execute(text('SELECT pg_advisory_xact_lock(:k)'), {'k': cls.KEY})
