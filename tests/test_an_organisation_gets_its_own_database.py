"""An organisation's database is made once, numbered once, and never reused.

The registry (`app.db.sql.tenant_registry`) is the one writer of
`tenants`. These pin what it promises: a deleted organisation is not
routed to, a database left behind by a crash is adopted under its
number, numbers only go up, and two first requests at once make one
database and one row.
"""

from __future__ import annotations

import asyncio
import uuid

from alembic.script import ScriptDirectory
import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.pool import NullPool

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
