"""One connection pool per process, sized, measured, and honest about it.

F116. The pool was `pool_size=2, max_overflow=3` **per repository class**,
because `SingletonMeta` gives every subclass its own singleton and the
engine registry was an instance attribute. Thirteen repositories plus the
base class the readiness probe uses is fourteen independent pools, none of
which any setting described.

Measured before the fix, on a real database: 200 concurrent one-second
queries through a single repository — the shape the worker's
`--concurrency=200` produces — and **83 of them failed** with `QueuePool
limit of size 2 overflow 3 reached`, each after waiting SQLAlchemy's
30-second default. `/ready`, holding its own private pool, answered
`ready` in 40 milliseconds throughout.

Four things are asserted here, and the third and fourth are the ones that
would be easy to break again:

* one engine per event loop, shared by every repository;
* the ceiling is the configured one, and exhaustion is counted rather
  than arriving as an unexplained cluster of 500s;
* `/ready` sees saturation — it must not be insulated from the failure it
  exists to detect;
* and it stays **ready** anyway, because draining a replica that is
  merely busy sends its load to the next one, which then saturates too.
  That is a burst becoming an outage.
"""

from __future__ import annotations

import asyncio
import weakref

import pytest
from sqlalchemy import text
from sqlalchemy.exc import TimeoutError as PoolTimeout
from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncEngine

from app.config import settings
from app.db.sql import SQLAlchemySessionManager

pytestmark = pytest.mark.anyio

# The `client` fixture is based at `/v1`; these endpoints are not under it.
_ABSOLUTE = 'http://test'


@pytest.fixture
def tiny_pool(monkeypatch):
    """One connection, no overflow, half a second of patience.

    The same shape as the real thing at a size a test can exhaust
    deliberately. The engine is cached, so the registry has to be cleared
    both before and after: before so this size takes effect, after so the
    next test does not inherit a one-connection pool.
    """
    monkeypatch.setattr(settings, 'db_pool_size', 1)
    monkeypatch.setattr(settings, 'db_max_overflow', 0)
    monkeypatch.setattr(settings, 'db_pool_timeout', 0.5)
    SQLAlchemySessionManager._engines.clear()
    yield
    SQLAlchemySessionManager._engines.clear()


class TestOneEnginePerProcess:
    async def test_two_repositories_share_one_engine(self):
        """The fix, stated as the property it produces.

        `UserDB` and `OrganizationDB` are separate singletons. If they hold
        separate engines they hold separate pools, and every pool
        configured is multiplied by the number of repository classes —
        which is how thirteen of them appeared without anybody choosing
        that number.
        """
        from app.db.sql.organizations import OrganizationDB
        from app.db.sql.user import UserDB

        async with UserDB().session_scope() as session:
            await session.execute(text('SELECT 1'))
        async with OrganizationDB().session_scope() as session:
            await session.execute(text('SELECT 1'))

        assert UserDB().engine is OrganizationDB().engine
        assert len(SQLAlchemySessionManager._engines) == 1, SQLAlchemySessionManager._engines

    async def test_the_readiness_probe_shares_it_too(self):
        """The base class is what `/ready` instantiates.

        It had its own pool, which is why the probe could report `ready`
        while every repository was timing out.
        """
        from app.db.sql.user import UserDB

        async with UserDB().session_scope() as session:
            await session.execute(text('SELECT 1'))

        assert SQLAlchemySessionManager().engine is UserDB().engine

    async def test_the_ceiling_is_the_configured_one(self, tiny_pool):
        from app.db.sql.user import UserDB

        async with UserDB().session_scope() as session:
            await session.execute(text('SELECT 1'))

        in_use, capacity = SQLAlchemySessionManager.pool_status()
        assert capacity == 1
        assert in_use == 0

    async def test_pool_status_is_absent_rather_than_zero_before_it_exists(self, tiny_pool):
        """A pool that has never been created is not an idle pool.

        Reporting 0-of-20 for it would make a replica that has served
        nothing look healthy for a reason it has not earned.
        """
        assert SQLAlchemySessionManager.pool_status() is None


class TestExhaustionIsCountedNotSilent:
    async def test_a_request_that_cannot_get_a_connection_fails_and_is_counted(self, tiny_pool):
        """Exhaustion is the failure nobody recognises from outside.

        Unrelated routes all start returning 500 at once with nothing in
        particular wrong, because they are queueing behind each other. The
        counter is what turns that into a one-line diagnosis, so it has to
        move when it happens.
        """
        from app.db.sql.user import UserDB
        from app.monitoring import DB_POOL_TIMEOUTS

        before = DB_POOL_TIMEOUTS._value.get()

        async def query(hold: float):
            async with UserDB().session_scope() as session:
                await session.execute(text(f'SELECT pg_sleep({hold})'))

        # One connection, two callers. The second waits out
        # `db_pool_timeout` and fails.
        results = await asyncio.gather(query(2), query(2), return_exceptions=True)

        failures = [r for r in results if isinstance(r, PoolTimeout)]
        assert len(failures) == 1, results
        assert 'QueuePool limit' in str(failures[0])
        assert DB_POOL_TIMEOUTS._value.get() == before + 1

    async def test_the_metric_reports_the_pool_at_scrape_time(self, tiny_pool):
        """Sampled when asked, not set when something last looked.

        A gauge written from a code path reports the pool as it was during
        that code path, which for a pool is almost never now.
        """
        from app.db.sql.user import UserDB
        from app.monitoring import render

        async def hold():
            async with UserDB().session_scope() as session:
                await session.execute(text('SELECT pg_sleep(1)'))

        task = asyncio.create_task(hold())
        await asyncio.sleep(0.2)
        exposition = render().decode()
        await task

        assert 'igni_db_pool_connections_in_use 1.0' in exposition, exposition
        assert 'igni_db_pool_capacity 1.0' in exposition


class TestReadinessUnderSaturation:
    """The cascade this endpoint could cause, and the insulation it had."""

    async def test_it_reports_saturation_rather_than_reporting_ok(self, client, tiny_pool, monkeypatch):
        """Not insulated. This is the half that was broken.

        With a private pool the probe answered `ready` in 40ms while the
        application could not serve — a readiness check that cannot
        observe the thing it is checking.
        """
        from app.services import readiness

        # The probe moved out of app.main into the readiness service;
        # this is the same knob it always was, read at call time.
        monkeypatch.setattr(readiness, 'READY_DB_TIMEOUT', 0.3)

        from app.db.sql.user import UserDB

        async def hold():
            async with UserDB().session_scope() as session:
                await session.execute(text('SELECT pg_sleep(1.5)'))

        task = asyncio.create_task(hold())
        await asyncio.sleep(0.2)
        response = await client.get(_ABSOLUTE + '/ready')
        await task

        assert 'saturated' in response.json()['checks']['postgres'], response.json()

    async def test_but_it_stays_ready(self, client, tiny_pool, monkeypatch):
        """And this is the half that would be easy to "fix" into an outage.

        A 503 here takes the pod out of the load balancer, which sends its
        traffic to the sibling replica, which saturates too. A burst that
        should have queued removes every replica instead. Postgres is
        fine; what is full is one process's pool.
        """
        from app.services import readiness

        # The probe moved out of app.main into the readiness service;
        # this is the same knob it always was, read at call time.
        monkeypatch.setattr(readiness, 'READY_DB_TIMEOUT', 0.3)

        from app.db.sql.user import UserDB

        async def hold():
            async with UserDB().session_scope() as session:
                await session.execute(text('SELECT pg_sleep(1.5)'))

        task = asyncio.create_task(hold())
        await asyncio.sleep(0.2)
        response = await client.get(_ABSOLUTE + '/ready')
        await task

        assert response.status_code == 200, response.json()
        assert response.json()['status'] == 'ready'

    async def test_the_dependency_gauge_does_not_blame_the_database(self, client, tiny_pool, monkeypatch):
        """`igni_dependency_up{dependency="postgres"}` reading 0 would send
        an operator to look at a database that is answering fine."""
        from app.services import readiness

        # The probe moved out of app.main into the readiness service;
        # this is the same knob it always was, read at call time.
        monkeypatch.setattr(readiness, 'READY_DB_TIMEOUT', 0.3)

        from app.db.sql.user import UserDB
        from app.monitoring import render

        async def hold():
            async with UserDB().session_scope() as session:
                await session.execute(text('SELECT pg_sleep(1.5)'))

        task = asyncio.create_task(hold())
        await asyncio.sleep(0.2)
        await client.get(_ABSOLUTE + '/ready')
        await task

        assert 'igni_dependency_up{dependency="postgres"} 1.0' in render().decode()

    async def test_a_database_that_is_actually_gone_is_still_a_503(self, client, monkeypatch):
        """The distinction has to cut both ways.

        `busy` must not become a way for an unreachable database to read
        as ready — which is the failure `/ready` was built for in the
        first place.
        """
        # Patched on the class, because `conftest` replaces
        # `Settings.postgres_url` with a property pointing at the test
        # database — patching the host and port underneath it does
        # nothing, which is how the first version of this test passed
        # against a perfectly reachable server.
        from app.config import Settings

        monkeypatch.setattr(
            Settings,
            'postgres_url',
            property(lambda self: 'postgresql+asyncpg://nobody:nothing@127.0.0.1:1/nowhere'),
        )
        SQLAlchemySessionManager._engines.clear()
        try:
            response = await client.get(_ABSOLUTE + '/ready')
        finally:
            SQLAlchemySessionManager._engines.clear()

        assert response.status_code == 503, response.json()
        assert response.json()['checks']['postgres'].startswith('unavailable')


class TestTheConnectionBudgetIsStated:
    async def test_it_warns_when_two_processes_would_not_fit(self, monkeypatch):
        """The failure this replaces is a deployment that boots, passes
        every check, and then refuses connections when the second replica
        starts taking load."""
        from app.security.startup_checks import check_pool_fits_the_server

        monkeypatch.setattr(settings, 'db_pool_size', 5)
        monkeypatch.setattr(settings, 'db_max_overflow', 15)
        monkeypatch.setattr(SQLAlchemySessionManager, 'session_scope', _fake_server(max_connections=25))

        findings = await check_pool_fits_the_server()

        assert len(findings) == 1, findings
        assert 'max_connections' in findings[0]
        # The numbers, not just the complaint: 20 per process against 22
        # usable is the thing somebody has to act on.
        assert '20' in findings[0] and '22' in findings[0]

    async def test_it_is_quiet_when_they_do(self, monkeypatch):
        from app.security.startup_checks import check_pool_fits_the_server

        monkeypatch.setattr(settings, 'db_pool_size', 5)
        monkeypatch.setattr(settings, 'db_max_overflow', 15)
        monkeypatch.setattr(SQLAlchemySessionManager, 'session_scope', _fake_server(max_connections=100))

        assert await check_pool_fits_the_server() == []

    async def test_it_never_refuses_to_start(self, monkeypatch):
        """A warning, deliberately. How many replicas share the server is
        not something one process can know, so an exact answer is not
        available and refusing on a guess would stop a deployment that is
        fine."""
        from app.security.startup_checks import check_pool_fits_the_server

        monkeypatch.setattr(settings, 'db_pool_size', 500)
        monkeypatch.setattr(settings, 'db_max_overflow', 500)
        monkeypatch.setattr(SQLAlchemySessionManager, 'session_scope', _fake_server(max_connections=10))

        findings = await check_pool_fits_the_server()
        assert findings, 'the impossible case has to be reported'

    async def test_a_database_it_cannot_ask_is_not_a_finding(self, monkeypatch):
        """ "Cannot tell" is not "is wrong". Failing startup on a
        transient connection error would turn a blip into an outage, which
        is the same rule `check_no_bypass_identity_in_database` follows."""
        from app.security.startup_checks import check_pool_fits_the_server

        def explode(self):
            raise OSError('connection refused')

        monkeypatch.setattr(SQLAlchemySessionManager, 'session_scope', explode)

        assert await check_pool_fits_the_server() == []


def _fake_server(*, max_connections: int, reserved: int = 3):
    """A `session_scope` standing in for a server of a given size.

    Faked rather than driven against a real Postgres because
    `max_connections` is set at server start: reproducing a 25-connection
    instance means running a second Postgres, and what is being tested is
    the arithmetic and the message, not the SHOW.
    """

    class Result:
        def __init__(self, value: str) -> None:
            self._value = value

        def scalar_one(self) -> str:
            return self._value

    class Session:
        async def execute(self, statement, params=None):
            return Result(str(max_connections) if 'max_connections' in str(statement) else str(reserved))

    class Scope:
        async def __aenter__(self):
            return Session()

        async def __aexit__(self, *exc):
            return False

    def session_scope(self):
        return Scope()

    return session_scope


class TestTheRegistryIsKeyedOnTheLoopItself:
    """One engine per loop — but `id(loop)` does not identify a loop.

    The registry keyed on `id(asyncio.get_running_loop())`. CPython
    reuses the address of a collected loop, so a loop that ends
    without calling `dispose` leaves its entry under an address the
    *next* loop is handed, and that loop is given an engine bound to
    a loop that no longer exists — `Future attached to a different
    loop`, which is the exact failure this registry prevents.

    Mostly hidden here because the fixtures dispose. It surfaced in
    the artifact graph's driver registry (since deleted), where they
    did not, and this is the same fix.

    The engine is faked: the point is which key the registry uses, and
    building real pools per loop would make this a slow test about
    something else.
    """

    @pytest.fixture
    def built(self, monkeypatch):
        from unittest.mock import MagicMock

        import app.db.sql as db_module
        from app.db.sql import SQLAlchemySessionManager

        made: list[object] = []

        def _engine(*_a, **_kw):
            engine = MagicMock(spec=AsyncEngine)
            made.append(engine)
            return engine

        monkeypatch.setattr(db_module, 'create_async_engine', _engine)
        monkeypatch.setattr(db_module, 'async_sessionmaker', lambda **_kw: MagicMock(spec=async_sessionmaker))
        monkeypatch.setattr(SQLAlchemySessionManager, '_engines', weakref.WeakKeyDictionary())
        return made

    def test_addresses_really_are_reused(self, built):
        """The premise, measured rather than asserted from memory. If
        CPython ever stopped reusing them this test would be checking
        nothing, and should say so."""
        import gc

        ids = []
        for _ in range(6):
            loop = asyncio.new_event_loop()
            ids.append(id(loop))
            loop.close()
            del loop
            gc.collect()

        assert len(set(ids)) < len(ids), f'no address was reused: {ids}'

    def test_one_loop_builds_one_engine(self, built):
        from app.db.sql import SQLAlchemySessionManager

        async def _ask_twice():
            manager = SQLAlchemySessionManager()
            return manager.engine, manager.engine

        first, second = asyncio.run(_ask_twice())

        assert first is second
        assert len(built) == 1

    def test_a_later_loop_does_not_inherit_the_engine(self, built):
        """Six loops, so a reused address is near-certain. With `id()`
        keying this fails; with the loop itself it does not."""
        from app.db.sql import SQLAlchemySessionManager

        async def _ask():
            return SQLAlchemySessionManager().engine

        engines = [asyncio.run(_ask()) for _ in range(6)]

        assert len({id(e) for e in engines}) == 6, 'a loop was handed another loop’s engine'
        assert len(built) == 6
