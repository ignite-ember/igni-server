import asyncio
from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
import logging
import threading
from typing import ClassVar
import weakref

from pydantic import BaseModel, ConfigDict
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import TimeoutError as PoolTimeout
from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncEngine, AsyncSession, create_async_engine

from app.config import settings
from app.observability.otel import ProcessTelemetry
from app.utils.singleton import SingletonMeta

logger = logging.getLogger(__name__)


class EngineEntry(BaseModel):
    """One database's engine on one loop, and the session factory bound to it."""

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    engine: AsyncEngine
    factory: async_sessionmaker[AsyncSession]


class SQLAlchemySessionManager(metaclass=SingletonMeta):
    """One engine per event loop, shared by every repository.

        ## Why the registry is keyed on the loop

    asyncpg connections and SQLAlchemy's awaitables bind to the loop they
        were created on, so an engine shared across loops raises
        ``RuntimeError: Future attached to a different loop``. Keying on the
        running loop's id keeps each loop with its own engine and never
        crosses them.

        Two loops used to be routine: Celery's ``AsyncIOPool`` ran its
        coroutines on a dedicated loop in its own thread while the API ran on
        uvicorn's. DEC-13 removed that process, so in production there is now
        one loop — but the tests run several, and ``asyncio.run`` in a script
        is a second one, which is enough for the shared-engine bug to be
        reachable. Keyed rather than simplified.

        ## Why the key is the loop and not ``id(loop)``

        This keyed on ``id(asyncio.get_running_loop())``, and that is
        unsound: CPython reuses the address of a loop that has been
        collected. Measured, not argued — six sequential
        ``new_event_loop()``/``close()`` pairs produce four distinct ids:

            [4383334688, 4383624720, 4383624720,
             4383074784, 4383074784, 4383074784]

        So a loop that ends without calling ``dispose`` leaves its entry
        behind under an address the *next* loop is handed, and that loop
        is given an engine bound to a loop that no longer exists — which
        is the exact failure this registry is keyed to prevent. It was
        mostly hidden because the fixtures dispose; it was found in
        the artifact graph's driver registry (since deleted), where they
        did not.

        A ``WeakKeyDictionary`` on the loop object has no ambiguity: the
        key is that loop's identity for as long as it is alive, and the
        entry disappears when it is collected, so nothing accumulates
        either.

        ## Why the registry is on the class

        ``_engines`` is a ``ClassVar``, so all thirteen subclasses share one
        engine per loop. It was per-instance, and because ``SingletonMeta``
        gives every *subclass* its own singleton, that meant thirteen engines
        with thirteen independent pools — plus a fourteenth for the base class
        that ``/ready`` instantiates.

        That is not a tuning detail, it is the wrong shape:

        * the pool was ``pool_size=2, max_overflow=3``, so **any one
          repository was limited to five concurrent queries** no matter how
          much concurrency was in front of it. The worker runs
          ``--concurrency=200``. Measured: 200 concurrent one-second queries
          through a single repository, 117 succeeded and **83 failed** with
          ``QueuePool limit of size 2 overflow 3 reached``, after waiting the
          full 30-second default;
        * the ceiling was invisible. Thirteen pools of five is 65 connections
          per process, which no setting stated and nothing measured, so
          sizing a customer's Postgres was guesswork;
        * ``/ready`` held a *private* pool, and so answered ``ready`` in 40ms
          while every repository around it was timing out. A readiness probe
          insulated from the failure it exists to detect is worse than no
          probe: it keeps a pod in service precisely when it cannot serve.

        The old comment justified the small pool with "per-task pools are
        short-lived", which described a worker model that no longer exists —
        ``AsyncIOPool`` replaced loop-per-task with one shared loop, so the
        pools became process-lifetime and nobody revisited the number.

        ## One entry per database, too

        Each organisation has its own database (`app.db.sql.tenancy`), and
        an engine is bound to one database, so each loop's entry is a map
        from database name to engine. The shared plane is one key in it;
        :meth:`database_scope` reaches the others. Keeping them here rather
        than in a second registry means one place builds engines, one
        place disposes them, and every session gets the same commit,
        rollback and pool-exhaustion handling.

        ## What the numbers mean now

        ``DB_POOL_SIZE + DB_MAX_OVERFLOW`` is the shared plane's ceiling on
        concurrent queries, and ``DB_POOL_TIMEOUT`` is how long a caller waits
        for a slot before failing. `DatabaseChecks.check_pool_fits_the_server` compares the
        arithmetic against the server's own ``max_connections`` at startup.
        Each organisation's database has a pool of the same size of its own,
        which that arithmetic does not count yet.
    """

    # Shared by every subclass. See the class docstring.
    _engines: ClassVar[weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, dict[str, EngineEntry]]] = (
        weakref.WeakKeyDictionary()
    )
    # ``threading.Lock`` rather than ``asyncio.Lock``: this guards a
    # registry shared across loops, and an asyncio primitive binds to
    # the first loop that awaits it.
    _engines_lock: ClassVar[threading.Lock] = threading.Lock()

    @staticmethod
    def _shared_database() -> str:
        """The shared plane's database name: its key in each loop's entry."""
        return str(make_url(settings.postgres_url).database)

    def _get_or_create(self, database: str) -> EngineEntry:
        loop = asyncio.get_running_loop()
        entry = SQLAlchemySessionManager._engines.get(loop, {}).get(database)
        if entry is not None:
            return entry
        with SQLAlchemySessionManager._engines_lock:
            pools = SQLAlchemySessionManager._engines.setdefault(loop, {})
            entry = pools.get(database)
            if entry is not None:
                return entry
            engine = create_async_engine(
                make_url(settings.postgres_url).set(database=database),
                pool_pre_ping=True,
                echo=False,
                pool_size=settings.db_pool_size,
                max_overflow=settings.db_max_overflow,
                pool_timeout=settings.db_pool_timeout,
            )
            ProcessTelemetry().watch_engine(engine)
            factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
            entry = EngineEntry(engine=engine, factory=factory)
            pools[database] = entry
            logger.info(
                'Database pool ready for %s: size=%d overflow=%d timeout=%.1fs (ceiling %d concurrent queries)',
                database,
                settings.db_pool_size,
                settings.db_max_overflow,
                settings.db_pool_timeout,
                settings.db_pool_size + settings.db_max_overflow,
            )
            return entry

    @property
    def engine(self) -> AsyncEngine:
        """The shared plane's engine on this loop."""
        return self._get_or_create(self._shared_database()).engine

    @property
    def session_factory(self) -> async_sessionmaker[AsyncSession]:
        """The shared plane's session factory on this loop."""
        return self._get_or_create(self._shared_database()).factory

    async def dispose(self) -> None:
        """Dispose every engine bound to the current loop: the shared plane's and each organisation's.

        Shared state, so this closes the pools for **every** repository on
        this loop — which is what a test teardown or a worker shutting
        down its loop wants, and what nothing else should call while
        requests are in flight.
        """
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        with SQLAlchemySessionManager._engines_lock:
            pools = SQLAlchemySessionManager._engines.pop(loop, {})
        for entry in pools.values():
            await entry.engine.dispose()

    @classmethod
    async def dispose_all(cls) -> None:
        """Dispose this loop's engines. One registry, so one call."""
        await cls().dispose()

    @classmethod
    def pool_status(cls) -> tuple[int, int] | None:
        """``(checked_out, ceiling)`` for the current loop's shared-plane pool.

        ``None`` when no engine exists on this loop yet, which is a real
        answer rather than zero: a pool that has never been created is
        not an idle pool, and reporting 0/20 for it would make a
        just-started replica look healthy for a reason it has not earned.
        """
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return None
        entry = cls._engines.get(loop, {}).get(cls._shared_database())
        if entry is None:
            return None
        pool = entry.engine.pool
        checked_out = getattr(pool, 'checkedout', None)
        if checked_out is None:
            return None
        return checked_out(), settings.db_pool_size + settings.db_max_overflow

    async def ping(self) -> None:
        """The cheapest round trip that proves the database answers.

        Here rather than in the readiness route for the ordinary reason —
        a `SELECT` belongs to the persistence layer whatever its size —
        and deliberately on a normal session, so it goes through the same
        pool every repository uses. A probe with its own connection
        answered `ready` in 40ms while every query around it was failing
        on pool exhaustion.

        Raises whatever the driver raises, including `PoolTimeout`. The
        caller is the one that can tell a saturated pool from an absent
        server, and it needs the exception type to do it.
        """
        async with self.session_scope() as session:
            await session.execute(text('SELECT 1'))

    def session_scope(self) -> AbstractAsyncContextManager[AsyncSession]:
        """Provide a transactional scope on the shared plane around a series of operations."""
        return self.database_scope(self._shared_database())

    @asynccontextmanager
    async def database_scope(self, database: str) -> AsyncIterator[AsyncSession]:
        """A transaction on one database of this server: committed on success, rolled back on error."""
        session: AsyncSession = self._get_or_create(database).factory()
        try:
            yield session
            await session.commit()
        except PoolTimeout:
            # Counted separately because it is not a failure of the query.
            # Without it, pool exhaustion arrives as an unexplained cluster
            # of 500s across unrelated routes at once, and the shape of
            # that — everything failing, nothing in particular — is the
            # hardest kind of incident to place.
            # Imported here to break a cycle, and the direction is the
            # point: app.monitoring.metrics reaches back into this
            # module for pool_status(), so one of the two has to defer.
            # It is this one, because the alternative makes the
            # foundational persistence module import monitoring at load
            # time for the sake of a counter.
            from app.monitoring import DB_POOL_TIMEOUTS

            DB_POOL_TIMEOUTS.inc()
            logger.error(
                'Database pool exhausted: no connection within %.1fs (ceiling %d). '
                'Raise DB_POOL_SIZE/DB_MAX_OVERFLOW, or reduce concurrency in front of it.',
                settings.db_pool_timeout,
                settings.db_pool_size + settings.db_max_overflow,
            )
            await session.rollback()
            raise
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
