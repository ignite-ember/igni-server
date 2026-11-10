"""Revocations, numbered under the feed's lock; and the signal that wakes long polls."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, func, insert, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.sql import SQLAlchemySessionManager
from app.db.sql.feed_lock import FeedLock
from app.db.sql.models.revocation import Revocation
from app.db.sql.schemas.producer_feed import RevocationEntry
from app.utils.singleton import SingletonMeta

#: The channel a mark, a feed change or a revocation notifies.
CHANNEL = 'igni_producer_feed'


class RevocationsDB(SQLAlchemySessionManager, metaclass=SingletonMeta):
    @staticmethod
    async def add_in(session: AsyncSession, entries: list[RevocationEntry]) -> None:
        """Number and store *entries* in a transaction already holding the feed's lock."""
        if not entries:
            return
        numbers = (
            await session.execute(
                select(func.nextval('revocation_seq')).select_from(func.generate_series(1, len(entries)))
            )
        ).scalars()
        now = datetime.now(timezone.utc)
        await session.execute(
            insert(Revocation),
            [
                {**entry.model_dump(), 'seq': int(number), 'created_at': now}
                for entry, number in zip(entries, sorted(numbers), strict=True)
            ],
        )

    async def add(self, entries: list[RevocationEntry]) -> None:
        """Store *entries* in their own transaction, taking the feed's lock so numbers stay in commit order."""
        async with self.session_scope() as session:
            await FeedLock.take(session)
            await self.add_in(session, entries)
        await FeedSignals().notify()

    async def after(self, seq: int, *, org_ids: list[str] | None, limit: int) -> list[Revocation]:
        """Rows after *seq*: every one, or these organisations' and those for every organisation."""
        query = select(Revocation).where(Revocation.seq > seq)
        if org_ids is not None:
            query = query.where(or_(Revocation.org_id.in_(org_ids), Revocation.org_id.is_(None)))
        async with self.session_scope() as session:
            rows = await session.execute(query.order_by(Revocation.seq).limit(limit))
            return list(rows.scalars().all())

    async def purge(self, *, older_than: timedelta) -> int:
        """Rows older than any token they could stop."""
        async with self.session_scope() as session:
            result = await session.execute(
                delete(Revocation).where(Revocation.created_at < datetime.now(timezone.utc) - older_than)
            )
            return int(result.rowcount or 0)  # type: ignore[attr-defined]


class Signal:
    """One listener's wake-up: set by a notification, cleared when waited on."""

    def __init__(self) -> None:
        self._event = asyncio.Event()

    def ring(self, *_: object) -> None:
        self._event.set()

    async def wait(self, timeout: float) -> bool:
        """True when notified within *timeout* seconds."""
        try:
            await asyncio.wait_for(self._event.wait(), timeout)
        except TimeoutError:
            return False
        self._event.clear()
        return True


class FeedSignals(SQLAlchemySessionManager, metaclass=SingletonMeta):
    """`LISTEN`/`NOTIFY` on the shared plane: a mark or a revocation wakes every long poll at once."""

    @asynccontextmanager
    async def listening(self) -> AsyncIterator[Signal]:
        signal = Signal()
        async with self.engine.connect() as conn:
            driver = (await conn.get_raw_connection()).driver_connection
            assert driver is not None
            await driver.add_listener(CHANNEL, signal.ring)
            try:
                yield signal
            finally:
                await driver.remove_listener(CHANNEL, signal.ring)

    async def notify(self) -> None:
        async with self.session_scope() as session:
            await session.execute(text('SELECT pg_notify(:c, :p)'), {'c': CHANNEL, 'p': ''})
