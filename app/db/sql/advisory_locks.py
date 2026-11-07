"""Postgres advisory locks, as queries rather than as inline SQL.

Taken by work that must happen on exactly one API replica — retention is
the only such job today. Advisory rather than a row because it releases
on its own if the process dies, so there is no stale-lock state anybody
has to clean up afterwards.

The key is the caller's to choose and to keep distinct; this class does
not own a registry of them, because a lock key means something only to
the one piece of work that takes it.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.db.sql import SQLAlchemySessionManager
from app.utils.singleton import SingletonMeta

logger = logging.getLogger(__name__)


class AdvisoryLockDB(SQLAlchemySessionManager, metaclass=SingletonMeta):
    """Session-level advisory locks, each held on a connection of its own.

    A session-level lock belongs to the connection that took it. Taken
    through a pooled session it went back to the pool with the lock, and
    the release ran on whichever connection the pool handed out next —
    often another, so the release did nothing and the lock stayed held
    by a pooled connection nobody tracked, stopping every later round.
    The connection is kept here from acquiring to releasing instead.
    """

    def __init__(self) -> None:
        self._held: dict[int, AsyncConnection] = {}

    async def try_acquire(self, key: int) -> bool:
        """Take the lock if it is free. False means somebody else has it.

        Does not wait. Every caller so far is periodic work where "another
        replica is already doing it" and "it got done" are the same
        outcome, so blocking would only serialise a duplicate.
        """
        connection = await self.engine.connect()
        got = bool((await connection.execute(text('SELECT pg_try_advisory_lock(:k)'), {'k': key})).scalar())
        await connection.commit()
        if not got:
            await connection.close()
            return False
        self._held[key] = connection
        return True

    @asynccontextmanager
    async def holding(self, key: int) -> AsyncIterator[AsyncConnection]:
        """Wait for the lock, hold it for the block, and yield the connection holding it.

        Unlike :meth:`try_acquire`, the connection is local to this call
        rather than kept in the shared ``_held`` map, so two coroutines
        waiting on one key in one process cannot clobber each other's
        connection. The caller may run its own queries on the yielded
        connection — and should, so that holding the lock never needs a
        second pool slot — but must leave it with no open transaction.
        """
        connection = await self.engine.connect()
        try:
            await connection.execute(text('SELECT pg_advisory_lock(:k)'), {'k': key})
            await connection.commit()
            try:
                yield connection
            finally:
                await connection.rollback()
                await connection.execute(text('SELECT pg_advisory_unlock(:k)'), {'k': key})
                await connection.commit()
        finally:
            await connection.close()

    async def release(self, key: int) -> bool:
        """Give the lock back. False if this process did not hold it.

        The boolean is Postgres's own answer and is worth returning: a
        release that quietly did nothing means the lock is still held by
        a session nobody is tracking, and the caller is the only one in a
        position to say whether that matters.
        """
        connection = self._held.pop(key, None)
        if connection is None:
            return False
        try:
            released = (await connection.execute(text('SELECT pg_advisory_unlock(:k)'), {'k': key})).scalar()
            await connection.commit()
            return bool(released)
        finally:
            await connection.close()
