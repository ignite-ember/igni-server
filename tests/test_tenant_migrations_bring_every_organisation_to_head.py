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

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

from alembic import command

from app.db.sql.tenancy import _TENANT_MIGRATIONS, TenantDatabases, Tenants
from app.db.sql.tenant_migrations import TenantMigrations
from tests.conftest import create_org, create_user


def _config(database: str) -> Config:
    config = Config()
    config.set_main_option('script_location', str(_TENANT_MIGRATIONS))
    config.set_main_option(
        'sqlalchemy.url',
        TenantDatabases.sync_url(database).render_as_string(hide_password=False).replace('%', '%%'),
    )
    return config


def _revision(database: str) -> str:
    engine = create_engine(TenantDatabases.sync_url(database), poolclass=NullPool)
    try:
        with engine.connect() as conn:
            return str(conn.execute(text('SELECT version_num FROM alembic_version')).scalar_one())
    finally:
        engine.dispose()


def _head() -> str:
    return str(ScriptDirectory.from_config(_config(TenantDatabases.template())).get_current_head())


async def test_organisations_behind_head_are_brought_to_it(db):
    """Two organisations, each left a revision behind as a release with a
    new tenant revision finds them: the job migrates both, and the
    template every new one is copied from, and says how many it did."""
    head = _head()
    databases = []
    for name in ('acme', 'globex'):
        await create_user(db, user_id=name, email=f'admin@{name}.test')
        org = await create_org(db, owner_user_id=name, slug=name, domains=(f'{name}.test',))
        databases.append(await Tenants().database_of(str(org.id)))
    for database in databases:
        await asyncio.to_thread(command.downgrade, _config(database), '-1')
        assert _revision(database) != head

    # `run` is the job's entry point and starts its own event loop, as
    # `python -m` does; a thread gives it one of its own here.
    migrated = await asyncio.to_thread(TenantMigrations.run)

    assert migrated == len(await Tenants().active()) == 2
    assert [_revision(database) for database in databases] == [head, head]
    assert _revision(TenantDatabases.template()) == head


async def test_nothing_to_do_is_not_an_error(db):
    """A deployment with no organisations yet — every fresh install's
    first migrate — migrates the template and reports none."""
    assert await asyncio.to_thread(TenantMigrations.run) == 0
    assert _revision(TenantDatabases.template()) == _head()
