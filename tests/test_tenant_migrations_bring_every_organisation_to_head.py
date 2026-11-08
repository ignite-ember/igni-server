"""The migrate job's second step brings every organisation's database to head.

`python -m app.db.sql.tenant_migrations` runs straight after `alembic
upgrade head` — in the chart's migrate hook, the compose `migrate`
service, `make migrate`. A release with a new tenant revision reaches
the organisations that already exist only through it: a database
created later is migrated when it is made, but one made last month is
not touched again by anything else.
"""

from __future__ import annotations

import asyncio
import runpy

from alembic.script import ScriptDirectory
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

from alembic import command

from app.db.sql.tenant_migrations import TenantMigrations
from app.db.sql.tenant_migrator import TenantMigrator
from app.db.sql.tenant_names import TenantNames
from app.db.sql.tenant_registry import TenantRegistryDB
from tests.conftest import create_org, create_user


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


async def test_organisations_behind_head_are_brought_to_it(db):
    """Two organisations, each left a revision behind as a release with a
    new tenant revision finds them: the job migrates both, and the
    template every new one is copied from, and says how many it did."""
    head = _head()
    databases = []
    for name in ('acme', 'globex'):
        await create_user(db, user_id=name, email=f'admin@{name}.test')
        org = await create_org(db, owner_user_id=name, slug=name, domains=(f'{name}.test',))
        databases.append(await TenantRegistryDB().database_of(str(org.id)))
    for database in databases:
        await asyncio.to_thread(command.downgrade, TenantMigrator().alembic_config(database), '-1')
        assert _revision(database) != head

    # `run` is the job's entry point and starts its own event loop, as
    # `python -m` does; a thread gives it one of its own here.
    migrated = await asyncio.to_thread(TenantMigrations().run)

    assert migrated == len(await TenantRegistryDB().active()) == 2
    assert [_revision(database) for database in databases] == [head, head]
    assert _revision(TenantNames().template()) == head


async def test_nothing_to_do_is_not_an_error(db):
    """A deployment with no organisations yet — every fresh install's
    first migrate — migrates the template and reports none."""
    assert await asyncio.to_thread(TenantMigrations().run) == 0
    assert _revision(TenantNames().template()) == _head()


async def _two_organisations_behind(db) -> tuple[str, str]:
    """Two organisations' databases, each a revision behind head, by their org id."""
    org_ids = []
    for name in ('acme', 'globex'):
        await create_user(db, user_id=name, email=f'admin@{name}.test')
        org = await create_org(db, owner_user_id=name, slug=name, domains=(f'{name}.test',))
        org_ids.append(str(org.id))
        database = await TenantRegistryDB().database_of(str(org.id))
        await asyncio.to_thread(command.downgrade, TenantMigrator().alembic_config(database), '-1')
    return org_ids[0], org_ids[1]


async def test_a_deleted_organisation_is_left_as_it_was(db):
    """A detached organisation is not routed to, so the job does not touch
    its database either: it stays at the revision it was deleted at."""
    acme, globex = await _two_organisations_behind(db)
    await TenantRegistryDB().detach(globex)

    migrated = await asyncio.to_thread(TenantMigrations().run)

    names = TenantNames()
    assert migrated == 1
    assert _revision(names.name(1)) == _head()
    assert _revision(names.name(2)) != _head()


class _FailsOnTheFirst(TenantMigrator):
    def migrate(self, database: str) -> None:
        if database == TenantNames().name(1):
            raise RuntimeError('the first organisation could not be migrated')
        super().migrate(database)


async def test_a_failure_stops_the_job_before_the_next_organisation(db):
    """The job fails loudly, and the release with it, rather than carrying
    on and leaving a deployment half on one schema and half on another."""
    await _two_organisations_behind(db)

    with pytest.raises(RuntimeError, match='could not be migrated'):
        await asyncio.to_thread(TenantMigrations(migrator=_FailsOnTheFirst()).run)

    assert _revision(TenantNames().name(2)) != _head()


# runpy warns that the module is already imported, which it is: by this file.
@pytest.mark.filterwarnings('ignore:.*found in sys.modules:RuntimeWarning')
async def test_python_dash_m_runs_the_job(db):
    """`python -m app.db.sql.tenant_migrations`, as the chart, compose and `make migrate` call it."""
    await _two_organisations_behind(db)

    await asyncio.to_thread(runpy.run_module, 'app.db.sql.tenant_migrations', run_name='__main__')

    names = TenantNames()
    assert [_revision(names.name(1)), _revision(names.name(2))] == [_head(), _head()]
