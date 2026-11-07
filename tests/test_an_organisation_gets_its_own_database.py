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

from app.db.sql.models.tenant import ACTIVE, DETACHED, Tenant
from app.db.sql.schemas.tenancy import OrganisationDetached, TenantRef
from app.db.sql.tenant_migrator import TenantMigrator
from app.db.sql.tenant_names import TenantNames
from app.db.sql.tenant_registry import TenantRegistryDB
from tests.conftest import TEST_DB_URL_SYNC


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
