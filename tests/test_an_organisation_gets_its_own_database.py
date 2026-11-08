"""An organisation's database is made once, numbered once, and never reused.

The registry (`app.db.sql.tenant_registry`) is the one writer of
`tenants`. These pin what it promises: a deleted organisation is not
routed to, a database left behind by a crash is adopted under its
number, numbers only go up, and two first requests at once make one
database and one row.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import logging
import threading
import uuid

from alembic.script import ScriptDirectory
import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ProgrammingError, TimeoutError as PoolTimeout
from sqlalchemy.pool import NullPool

from alembic import command

from app.config import settings
from app.db.sql import SQLAlchemySessionManager
from app.db.sql.advisory_locks import AdvisoryLockDB
from app.db.sql.models.tenant import ACTIVE, DETACHED, Tenant
from app.db.sql.organizations import OrganizationDB
from app.db.sql.schemas.tenancy import OrganisationDetached, TenantRef
from app.db.sql.tenancy import TenantSessions
from app.db.sql.tenant_migrator import TenantMigrator
from app.db.sql.tenant_names import TenantNames
from app.db.sql.tenant_registry import TENANT_LOCK, TenantRegistryDB
from tests.conftest import add_member, create_org, create_user, TEST_DB_URL_SYNC


def _registry_rows() -> list[TenantRef]:
    engine = create_engine(TEST_DB_URL_SYNC, poolclass=NullPool)
    try:
        with engine.connect() as conn:
            rows = conn.execute(select(Tenant).order_by(Tenant.number)).all()
            return [TenantRef(org_id=r.org_id, number=r.number, database=r.database, state=r.state) for r in rows]
    finally:
        engine.dispose()


def _tenant_databases() -> list[str]:
    engine = create_engine(TEST_DB_URL_SYNC, poolclass=NullPool)
    try:
        with engine.connect() as conn:
            found = conn.execute(
                text('SELECT datname FROM pg_database WHERE datname ~ :p ORDER BY datname'),
                {'p': r'^ember_test_t[0-9]+$'},
            )
            return [str(name) for (name,) in found]
    finally:
        engine.dispose()


def _revision(database: str) -> str:
    engine = create_engine(TenantNames().sync_url(database), poolclass=NullPool)
    try:
        with engine.connect() as conn:
            return str(conn.execute(text('SELECT version_num FROM alembic_version')).scalar_one())
    finally:
        engine.dispose()


def _head() -> str:
    config = TenantMigrator().alembic_config(TenantNames().template())
    return str(ScriptDirectory.from_config(config).get_current_head())


@pytest.fixture
def tiny_pool(monkeypatch):
    """One connection per database, no overflow, half a second of patience.

    The engines are cached per loop, so the registry is cleared before
    (for this size to take effect) and after (so the next test does not
    inherit it).
    """
    monkeypatch.setattr(settings, 'db_pool_size', 1)
    monkeypatch.setattr(settings, 'db_max_overflow', 0)
    monkeypatch.setattr(settings, 'db_pool_timeout', 0.5)
    SQLAlchemySessionManager._engines.clear()
    yield
    SQLAlchemySessionManager._engines.clear()


class TestRouting:
    async def test_a_detached_organisation_is_not_routed_to(self):
        org_id = str(uuid.uuid4())
        await TenantRegistryDB().database_of(org_id)

        await TenantRegistryDB().detach(org_id)

        with pytest.raises(OrganisationDetached):
            await TenantRegistryDB().database_of(org_id)
        with pytest.raises(OrganisationDetached):
            await TenantRegistryDB().number_of(org_id)
        assert [(r.org_id, r.state) for r in _registry_rows()] == [(org_id, DETACHED)]

    async def test_detaching_an_unknown_organisation_writes_nothing(self):
        await TenantRegistryDB().detach(str(uuid.uuid4()))

        assert _registry_rows() == []

    async def test_number_of_is_the_registry_row_s_number(self):
        first, second = str(uuid.uuid4()), str(uuid.uuid4())

        numbers = [await TenantRegistryDB().number_of(first), await TenantRegistryDB().number_of(second)]

        names = TenantNames()
        assert numbers == [1, 2]
        assert _registry_rows() == [
            TenantRef(org_id=first, number=1, database=names.name(1), state=ACTIVE),
            TenantRef(org_id=second, number=2, database=names.name(2), state=ACTIVE),
        ]

    async def test_active_answers_only_routed_organisations_in_number_order(self):
        kept, gone = str(uuid.uuid4()), str(uuid.uuid4())
        await TenantRegistryDB().database_of(gone)
        await TenantRegistryDB().database_of(kept)
        await TenantRegistryDB().detach(gone)

        assert await TenantRegistryDB().active_org_ids() == [kept]
        assert await TenantRegistryDB().active() == [
            TenantRef(org_id=kept, number=2, database=TenantNames().name(2), state=ACTIVE)
        ]


class TestProvisioning:
    async def test_a_number_is_never_reused_after_detaching(self):
        first, second = str(uuid.uuid4()), str(uuid.uuid4())
        assert await TenantRegistryDB().number_of(first) == 1
        await TenantRegistryDB().detach(first)

        assert await TenantRegistryDB().number_of(second) == 2

    async def test_a_database_left_unregistered_by_a_crash_is_adopted(self):
        """The crash between creating and registering: `<db>_t1` exists, no row
        names it. The next organisation takes number 1, finds it there,
        migrates it and registers it."""
        names = TenantNames()
        migrator = TenantMigrator(names=names)
        await asyncio.to_thread(migrator.ensure_template)
        await asyncio.to_thread(migrator.create, names.name(1))
        assert _registry_rows() == []

        org_id = str(uuid.uuid4())
        database = await TenantRegistryDB().database_of(org_id)

        assert database == names.name(1)
        assert _registry_rows() == [TenantRef(org_id=org_id, number=1, database=database, state=ACTIVE)]
        assert _tenant_databases() == [database]
        assert _revision(database) == _head()

    async def test_two_first_requests_at_once_make_one_database_and_one_row(self):
        org_id = str(uuid.uuid4())

        first, second = await asyncio.gather(
            TenantRegistryDB().database_of(org_id), TenantRegistryDB().database_of(org_id)
        )

        assert first == second == TenantNames().name(1)
        assert _registry_rows() == [TenantRef(org_id=org_id, number=1, database=first, state=ACTIVE)]
        assert _tenant_databases() == [first]


def _registry(**collaborators) -> TenantRegistryDB:
    """A registry of its own, past the singleton, with these collaborators in place of the defaults."""
    return type.__call__(TenantRegistryDB, **collaborators)


class _RefusingMigrator(TenantMigrator):
    """Postgres refuses to make the database: no `CREATEDB`, a full disk."""

    async def prepare(self, database: str) -> None:
        raise RuntimeError('permission denied to create database')


class _WatchingMigrator(TenantMigrator):
    """Notes, as the database is being made, who holds the provisioning lock and on what."""

    def __init__(self) -> None:
        super().__init__()
        self.seen: tuple[bool, int] | None = None

    async def prepare(self, database: str) -> None:
        free = await asyncio.to_thread(TestTheProvisioningLock._another_process_can_take, TENANT_LOCK)
        holder = await asyncio.to_thread(TestTheProvisioningLock._holder_state, TENANT_LOCK)
        self.seen = (free, holder, AdvisoryLockDB().engine.pool.checkedout())  # type: ignore[attr-defined]
        await super().prepare(database)


class _AnotherProcessGetsThereFirst(AdvisoryLockDB):
    """While this process waits for the lock, another provisions the organisation and an admin deletes it."""

    org_id = ''

    @asynccontextmanager
    async def holding(self, key: int) -> AsyncIterator:
        await TenantRegistryDB().database_of(self.org_id)
        await TenantRegistryDB().detach(self.org_id)
        async with super().holding(key) as connection:
            yield connection


class TestWhatProvisioningLeavesBehind:
    async def test_a_database_that_cannot_be_made_registers_nothing_and_lets_go_of_the_lock(self):
        org_id = str(uuid.uuid4())

        with pytest.raises(RuntimeError, match='permission denied'):
            await _registry(migrator=_RefusingMigrator()).database_of(org_id)

        assert _registry_rows() == []
        assert TestTheProvisioningLock._another_process_can_take(TENANT_LOCK)
        # Nothing was taken by the failure: the next attempt gets the first number.
        assert await TenantRegistryDB().number_of(org_id) == 1

    async def test_the_lock_is_held_outside_the_shared_pool_while_the_database_is_made(self):
        """The lock's own connection, not a pool slot, for the whole
        create-and-migrate — and not left idle in the transaction that
        read the next number."""
        migrator = _WatchingMigrator()

        await _registry(migrator=migrator).database_of(str(uuid.uuid4()))

        assert migrator.seen == (False, 'idle', 0)
        assert TestTheProvisioningLock._another_process_can_take(TENANT_LOCK)

    async def test_waiting_for_the_lock_leaves_the_pool_to_everybody_else(self, tiny_pool):
        """Another process is making a database; two new organisations here
        wait for it. Before, each waiter sat on a pool slot for as long as it
        waited — a whole alembic run — and the request beside them timed out."""
        other_process = create_engine(TEST_DB_URL_SYNC, poolclass=NullPool)
        held = other_process.connect()
        held.execute(text('SELECT pg_advisory_lock(:k)'), {'k': TENANT_LOCK})
        held.commit()
        try:
            waiting = [asyncio.create_task(TenantRegistryDB().database_of(str(uuid.uuid4()))) for _ in range(2)]
            await asyncio.sleep(0.5)

            async with SQLAlchemySessionManager().session_scope() as session:
                assert (await session.execute(text('SELECT 1'))).scalar_one() == 1
            assert not any(task.done() for task in waiting)
        finally:
            held.execute(text('SELECT pg_advisory_unlock(:k)'), {'k': TENANT_LOCK})
            held.close()
            other_process.dispose()

        made = await asyncio.gather(*waiting)
        names = TenantNames()
        assert sorted(made) == [names.name(1), names.name(2)]

    async def test_an_organisation_deleted_while_waiting_for_the_lock_is_refused(self):
        org_id = str(uuid.uuid4())
        locks = type.__call__(_AnotherProcessGetsThereFirst)
        locks.org_id = org_id

        with pytest.raises(OrganisationDetached):
            await _registry(locks=locks).database_of(org_id)

        assert [(r.org_id, r.number, r.state) for r in _registry_rows()] == [(org_id, 1, DETACHED)]
        assert _tenant_databases() == [TenantNames().name(1)]

    async def test_the_row_is_stamped_by_the_api_s_clock_and_the_provisioning_logged(self, monkeypatch, caplog):
        stamped = datetime(2031, 2, 3, 4, 5, 6, tzinfo=timezone.utc)
        monkeypatch.setattr(
            'app.db.sql.models.tenant.datetime', type('Fixed', (), {'now': staticmethod(lambda tz=None: stamped)})
        )

        with caplog.at_level(logging.INFO, logger='app.db.sql.tenant_registry'):
            await TenantRegistryDB().database_of(str(uuid.uuid4()))

        engine = create_engine(TEST_DB_URL_SYNC, poolclass=NullPool)
        try:
            with engine.connect() as conn:
                created_at = conn.execute(select(Tenant.created_at)).scalar_one()
        finally:
            engine.dispose()
        assert created_at == stamped
        assert 'tenancy: organisation provisioned as tenant 1' in caplog.messages


class _CountingMigrator(TenantMigrator):
    def __init__(self) -> None:
        super().__init__()
        self.migrated: list[str] = []

    def migrate(self, database: str) -> None:
        self.migrated.append(database)
        super().migrate(database)


class TestTheTemplateIsMigratedOncePerMigrator:
    def test_a_second_ensure_does_not_run_alembic_again(self):
        migrator = _CountingMigrator()

        migrator.ensure_template()
        migrator.ensure_template()

        assert migrator.migrated == [TenantNames().template()]

    def test_another_migrator_migrates_it_again(self):
        """Once per migrator, not once per process: the migrate job's and the registry's are one."""
        first, second = _CountingMigrator(), _CountingMigrator()

        first.ensure_template()
        second.ensure_template()

        assert first.migrated == second.migrated == [TenantNames().template()]

    def test_two_threads_at_once_run_alembic_once(self):
        migrator = _MeetingMigrator()
        threads = [threading.Thread(target=migrator.ensure_template) for _ in range(2)]

        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert migrator.migrated == [TenantNames().template()]


class _MeetingMigrator(_CountingMigrator):
    """Both threads asking whether the template exists at once, if nothing stops them."""

    def __init__(self) -> None:
        super().__init__()
        self._both = threading.Barrier(2)

    def exists(self, database: str) -> bool:
        try:
            self._both.wait(timeout=1)
        except threading.BrokenBarrierError:
            pass
        return super().exists(database)


class _RacedMigrator(TenantMigrator):
    """Another process creates the template between the check and the `CREATE DATABASE`."""

    def exists(self, database: str) -> bool:
        return False


class _NoTemplateNames(TenantNames):
    def template(self) -> str:
        return f'{self.base_name()}_t_no_such_template'


def _drop(database: str) -> None:
    engine = create_engine(TEST_DB_URL_SYNC, isolation_level='AUTOCOMMIT', poolclass=NullPool)
    try:
        with engine.connect() as conn:
            quoted = conn.dialect.identifier_preparer.quote_identifier(database)
            conn.execute(text(f'DROP DATABASE IF EXISTS {quoted} WITH (FORCE)'))
    finally:
        engine.dispose()


def _databases_named(name: str) -> list[str]:
    engine = create_engine(TEST_DB_URL_SYNC, poolclass=NullPool)
    try:
        with engine.connect() as conn:
            found = conn.execute(text('SELECT datname FROM pg_database WHERE datname = :d'), {'d': name})
            return [str(d) for (d,) in found]
    finally:
        engine.dispose()


class TestMakingTheDatabases:
    def test_a_missing_template_is_created_and_brought_to_head(self):
        template = TenantNames().template()
        _drop(template)

        TenantMigrator().ensure_template()

        assert _revision(template) == _head()

    def test_a_template_left_behind_by_a_release_is_brought_to_head(self):
        template = TenantNames().template()
        migrator = TenantMigrator()
        migrator.ensure_template()
        command.downgrade(migrator.alembic_config(template), '-1')
        assert _revision(template) != _head()

        TenantMigrator().ensure_template()

        assert _revision(template) == _head()

    def test_a_template_another_process_made_first_is_used(self):
        TenantMigrator().ensure_template()

        _RacedMigrator().ensure_template()

        assert _revision(TenantNames().template()) == _head()

    def test_a_database_that_already_exists_is_adopted_and_said_so(self, caplog):
        migrator = TenantMigrator()
        migrator.ensure_template()
        database = TenantNames().name(1)
        migrator.create(database)

        with caplog.at_level(logging.WARNING, logger='app.db.sql.tenant_migrator'):
            migrator.create(database)

        assert _tenant_databases() == [database]
        assert f'tenancy: {database} already existed, unregistered; adopting it' in caplog.messages

    def test_any_other_refusal_is_raised(self):
        names = _NoTemplateNames()
        database = names.name(1)

        with pytest.raises(ProgrammingError, match='does not exist'):
            TenantMigrator(names=names).create(database)

        assert _tenant_databases() == []

    def test_a_name_with_a_double_quote_is_escaped_not_spliced(self):
        database = 'ember_test_t"quoted'
        migrator = TenantMigrator()
        migrator.ensure_template()
        try:
            migrator.create(database)

            assert _databases_named(database) == [database]
        finally:
            _drop(database)

    def test_alembic_is_pointed_at_the_database_with_a_percent_in_the_password_intact(self):
        names = TenantNames(shared_url=make_url('postgresql+asyncpg://u:p%40ss@db.internal:5433/igni'))

        config = TenantMigrator(names=names).alembic_config('igni_t7')

        assert config.get_main_option('sqlalchemy.url') == 'postgresql+psycopg2://u:p%40ss@db.internal:5433/igni_t7'
        assert str(config.get_main_option('script_location')).endswith('alembic_tenant')


class TestTenantNames:
    def test_names_derive_from_the_shared_plane_s_url(self):
        names = TenantNames(shared_url=make_url('postgresql+asyncpg://u:p%40ss@db.internal:5433/igni'))

        assert names.base_name() == 'igni'
        assert names.name(7) == 'igni_t7'
        assert names.template() == 'igni_tenant_template'
        assert names.sync_url('igni_t7').render_as_string(hide_password=False) == (
            'postgresql+psycopg2://u:p%40ss@db.internal:5433/igni_t7'
        )

    def test_without_a_url_it_follows_the_settings(self):
        assert TenantNames().base_name() == make_url(TEST_DB_URL_SYNC).database


class TestARegistryEntryRoutes:
    def test_an_active_entry_routes_to_its_database(self):
        ref = TenantRef(org_id='org-1', number=3, database='ember_test_t3', state=ACTIVE)

        assert ref.route() == 'ember_test_t3'

    def test_a_detached_entry_refuses_by_organisation(self):
        ref = TenantRef(org_id='org-1', number=3, database='ember_test_t3', state=DETACHED)

        with pytest.raises(OrganisationDetached) as refused:
            ref.route()
        assert refused.value.args == ('org-1',)


class TestASessionOpensItsOwnOrganisationsDatabase:
    async def test_each_organisation_s_session_is_on_its_own_database(self):
        first, second = str(uuid.uuid4()), str(uuid.uuid4())

        seen = []
        for org_id in (first, second):
            async with TenantSessions().session_scope(org_id) as session:
                seen.append(str((await session.execute(text('SELECT current_database()'))).scalar_one()))

        names = TenantNames()
        assert seen == [names.name(1), names.name(2)]

    async def test_a_detached_organisation_gets_no_session(self):
        org_id = str(uuid.uuid4())
        await TenantRegistryDB().database_of(org_id)
        await TenantRegistryDB().detach(org_id)

        with pytest.raises(OrganisationDetached):
            async with TenantSessions().session_scope(org_id):
                pass


def _trail_routes(database: str) -> list[str]:
    engine = create_engine(TenantNames().sync_url(database), poolclass=NullPool)
    try:
        with engine.connect() as conn:
            return [str(r) for r in conn.execute(text('SELECT route FROM audit_events ORDER BY route')).scalars()]
    finally:
        engine.dispose()


def _connections_to(database: str) -> int:
    engine = create_engine(TEST_DB_URL_SYNC, poolclass=NullPool)
    try:
        with engine.connect() as conn:
            return int(
                conn.execute(
                    text('SELECT count(*) FROM pg_stat_activity WHERE datname = :d AND pid <> pg_backend_pid()'),
                    {'d': database},
                ).scalar_one()
            )
    finally:
        engine.dispose()


_WRITE = (
    'INSERT INTO audit_events (id, occurred_at, method, route, status_code, outcome) '
    "VALUES (:i, now(), 'POST', :r, 200, 'success')"
)


class TestAnOrganisationsSessionIsTheManagersScope:
    """`TenantSessions` hands the organisation's database to the session manager's
    own scope, so an organisation's session commits, rolls back, runs out of
    connections and is closed exactly as the shared plane's is."""

    async def test_what_the_block_wrote_is_kept_when_it_ends_and_dropped_when_it_fails(self):
        org_id = str(uuid.uuid4())

        async with TenantSessions().session_scope(org_id) as session:
            await session.execute(text(_WRITE), {'i': str(uuid.uuid4()), 'r': '/kept'})
        with pytest.raises(RuntimeError):
            async with TenantSessions().session_scope(org_id) as session:
                await session.execute(text(_WRITE), {'i': str(uuid.uuid4()), 'r': '/dropped'})
                raise RuntimeError('the request failed after writing')

        assert _trail_routes(TenantNames().name(1)) == ['/kept']

    async def test_running_out_of_an_organisation_s_connections_is_counted_and_said(self, tiny_pool, caplog):
        from app.monitoring import DB_POOL_TIMEOUTS

        org_id = str(uuid.uuid4())
        await TenantRegistryDB().database_of(org_id)
        before = DB_POOL_TIMEOUTS._value.get()

        async def query():
            async with TenantSessions().session_scope(org_id) as session:
                await session.execute(text('SELECT pg_sleep(1.5)'))

        with caplog.at_level(logging.ERROR, logger='app.db.sql'):
            results = await asyncio.gather(query(), query(), return_exceptions=True)

        failures = [r for r in results if isinstance(r, PoolTimeout)]
        assert len(failures) == 1, results
        assert DB_POOL_TIMEOUTS._value.get() == before + 1
        assert [r.getMessage() for r in caplog.records if r.name == 'app.db.sql'] == [
            'Database pool exhausted: no connection within 0.5s (ceiling 1). '
            'Raise DB_POOL_SIZE/DB_MAX_OVERFLOW, or reduce concurrency in front of it.'
        ]

    async def test_each_database_s_pool_says_it_is_ready_and_which_it_is(self, caplog):
        org_id = str(uuid.uuid4())
        await TenantRegistryDB().database_of(org_id)
        ceiling = settings.db_pool_size + settings.db_max_overflow

        with caplog.at_level(logging.INFO, logger='app.db.sql'):
            async with TenantSessions().session_scope(org_id):
                pass

        assert f'Database pool ready for {TenantNames().name(1)}: size={settings.db_pool_size} ' in caplog.text
        assert f'(ceiling {ceiling} concurrent queries)' in caplog.text

    async def test_disposing_closes_every_organisation_s_connections_too(self):
        org_id = str(uuid.uuid4())
        async with TenantSessions().session_scope(org_id) as session:
            await session.execute(text('SELECT 1'))
        database = TenantNames().name(1)
        assert _connections_to(database) == 1

        await SQLAlchemySessionManager.dispose_all()

        for _ in range(20):
            if _connections_to(database) == 0:
                break
            await asyncio.sleep(0.1)
        assert _connections_to(database) == 0


class TestWhichOrganisationsAPersonReaches:
    """What `OrganizationDB` answers for data that starts from a person, not an organisation.

    A person is a current member of one organisation at most, so the
    other rows here are a membership that ended (still a row, not a
    current one) and other people in organisations without a database.
    """

    @pytest.fixture
    async def orgs(self, db):
        await create_user(db, user_id='owner', email='owner@example.com')
        for user_id in ('u-member', 'u-in-unprovisioned', 'u-in-detached'):
            await create_user(db, user_id=user_id, email=f'{user_id}@example.com')
        orgs = {}
        for slug in ('a-active', 'b-ended', 'c-unprovisioned', 'd-detached'):
            orgs[slug] = str((await create_org(db, owner_user_id='owner', slug=slug, display_name=slug)).id)
        await add_member(db, org_id=orgs['a-active'], user_id='u-member')
        await add_member(db, org_id=orgs['b-ended'], user_id='u-member', status='removed')
        await add_member(db, org_id=orgs['c-unprovisioned'], user_id='u-in-unprovisioned')
        await add_member(db, org_id=orgs['d-detached'], user_id='u-in-detached')
        await db.commit()
        for slug in ('a-active', 'b-ended', 'd-detached'):
            await TenantRegistryDB().database_of(orgs[slug])
        await TenantRegistryDB().detach(orgs['d-detached'])
        return orgs

    async def test_member_org_ids_are_the_current_memberships_unless_asked_for_all(self, orgs):
        assert await OrganizationDB().member_org_ids('u-member') == [orgs['a-active']]
        assert await OrganizationDB().member_org_ids('u-member', active_only=False) == sorted(
            [orgs['a-active'], orgs['b-ended']]
        )

    async def test_provisioned_ones_leave_out_the_unprovisioned_and_the_detached(self, orgs):
        assert await OrganizationDB().provisioned_member_org_ids('u-member') == [orgs['a-active']]
        assert await OrganizationDB().provisioned_member_org_ids('u-in-unprovisioned') == []
        assert await OrganizationDB().provisioned_member_org_ids('u-in-detached') == []
        # Though both are still current memberships.
        assert await OrganizationDB().member_org_ids('u-in-unprovisioned') == [orgs['c-unprovisioned']]
        assert await OrganizationDB().member_org_ids('u-in-detached') == [orgs['d-detached']]

    async def test_reading_them_provisions_nothing(self, orgs):
        await OrganizationDB().provisioned_member_org_ids('u-in-unprovisioned')

        assert orgs['c-unprovisioned'] not in {r.org_id for r in _registry_rows()}


class TestTheProvisioningLock:
    """`AdvisoryLockDB.holding`, which `TenantRegistryDB` provisions under."""

    @staticmethod
    def _holder_state(key: int) -> str | None:
        """What the connection holding this lock is doing, as Postgres reports it."""
        engine = create_engine(TEST_DB_URL_SYNC, poolclass=NullPool)
        try:
            with engine.connect() as conn:
                found = conn.execute(
                    text(
                        'SELECT a.state FROM pg_locks l JOIN pg_stat_activity a ON a.pid = l.pid '
                        "WHERE l.locktype = 'advisory' AND l.granted AND l.classid = :hi AND l.objid = :lo"
                    ),
                    {'hi': key >> 32, 'lo': key & 0xFFFFFFFF},
                ).scalar_one_or_none()
                return None if found is None else str(found)
        finally:
            engine.dispose()

    @staticmethod
    def _another_process_can_take(key: int) -> bool:
        engine = create_engine(TEST_DB_URL_SYNC, poolclass=NullPool)
        try:
            with engine.connect() as conn:
                taken = bool(conn.execute(text('SELECT pg_try_advisory_lock(:k)'), {'k': key}).scalar_one())
                if taken:
                    conn.execute(text('SELECT pg_advisory_unlock(:k)'), {'k': key})
                return taken
        finally:
            engine.dispose()

    async def test_it_is_held_for_the_block_and_given_back_after(self):
        key = TENANT_LOCK + 1

        async with AdvisoryLockDB().holding(key) as connection:
            assert not self._another_process_can_take(key)
            # The holder's own queries run on the connection that holds it.
            assert (await connection.execute(text('SELECT 1'))).scalar_one() == 1

        assert self._another_process_can_take(key)

    async def test_it_is_given_back_when_the_block_fails(self):
        key = TENANT_LOCK + 2

        with pytest.raises(RuntimeError):
            async with AdvisoryLockDB().holding(key):
                raise RuntimeError('provisioning failed')

        assert self._another_process_can_take(key)
