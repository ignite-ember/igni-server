"""Each organisation's own database, and the sessions that reach it.

The shared plane (`settings.postgres_db`) holds only what has to know
about every organisation. Everything that belongs to one organisation —
its groups, usage, notifications and audit trail — lives in that
organisation's own logical database on the same server: `<db>_t<number>`.

A session is opened *for an organisation* (`TenantSessions.session_scope`),
so code holding it cannot see another organisation's tables at all. A
query that forgot to filter by organisation has nothing to leak, which is
the point: separation by construction rather than by remembering.

## How a tenant database comes to exist

Lazily, the first time anything asks for one, under an advisory lock so
two processes cannot both create it:

1. a tenant number is assigned — one more than the highest ever given,
   so a number is never reused, even after an organisation is deleted;
2. the database is created from a template (`<db>_tenant_template`),
   which carries the tenant schema already migrated — fast, and identical
   for every organisation;
3. it is migrated to head (`alembic_tenant/`), which is a no-op unless the
   template was behind;
4. only then is it registered, so the registry never points at a database
   that does not exist.

A crash between 2 and 4 leaves an unregistered database under the next
number. The next attempt takes that same number, finds the database
there, migrates it and registers it.

Creating databases needs the `CREATEDB` privilege for the service's
Postgres role. An organisation is deleted by detaching it: its database
is kept, and nothing routes to it.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
import logging
from pathlib import Path
import threading
from typing import ClassVar
import weakref

from alembic.config import Config
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import make_url, URL
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from alembic import command

from app.config import settings
from app.db.sql import SQLAlchemySessionManager
from app.db.sql.models.org_membership import OrgMembership
from app.db.sql.models.tenant import ACTIVE, DETACHED, Tenant
from app.observability.otel import ServerTelemetry
from app.utils.singleton import SingletonMeta

logger = logging.getLogger(__name__)

#: Serialises creating tenant databases across every process.
_TENANT_LOCK = 0x54454E414E54

#: Where the tenant migration tree lives.
_TENANT_MIGRATIONS = Path(__file__).resolve().parents[3] / 'alembic_tenant'

#: The services that derive a Redis database from a tenant number, in a
#: fixed order. Never reorder: the index is part of every deployment.
REDIS_SERVICES = ('igni-server', 'code-index', 'artifacts')

#: The first Redis database index used by tenants. 0–15 are the shared
#: planes' and whatever else a deployment already uses them for.
REDIS_TENANT_BASE = 16


class OrganisationDetached(LookupError):
    """The organisation was deleted. Its data is kept; nothing routes to it."""


def redis_database(number: int, service: str) -> int:
    """The Redis database a service uses for one organisation.

    One formula for every service, so they never collide: a block of
    `len(REDIS_SERVICES)` indexes per organisation, above the shared ones.
    Redis must be configured with enough databases (`databases 64` allows
    sixteen organisations).
    """
    return REDIS_TENANT_BASE + len(REDIS_SERVICES) * (number - 1) + REDIS_SERVICES.index(service)


def _duplicate(exc: ProgrammingError) -> bool:
    """Whether Postgres refused because the database already exists (42P04)."""
    return getattr(exc.orig, 'pgcode', None) == '42P04' or 'already exists' in str(exc.orig)


class TenantDatabases:
    """Names, and the DDL that makes them. Synchronous: it runs in a thread."""

    #: Template databases this process has already brought to head. A new
    #: organisation's database is copied from it, so migrating it again
    #: for every organisation would be an alembic run apiece for nothing.
    _templates_ready: ClassVar[set[str]] = set()

    @staticmethod
    def _shared() -> URL:
        return make_url(settings.postgres_url)

    @classmethod
    def base_name(cls) -> str:
        return str(cls._shared().database)

    @classmethod
    def name(cls, number: int) -> str:
        return f'{cls.base_name()}_t{number}'

    @classmethod
    def template(cls) -> str:
        return f'{cls.base_name()}_tenant_template'

    @classmethod
    def async_url(cls, database: str) -> URL:
        return cls._shared().set(database=database)

    @classmethod
    def sync_url(cls, database: str) -> URL:
        return cls._shared().set(drivername='postgresql+psycopg2', database=database)

    @classmethod
    def _connect(cls, database: str):  # noqa: ANN206 — a SQLAlchemy Connection, used as a context manager
        """An autocommit connection: `CREATE DATABASE` cannot run inside a transaction."""
        engine = create_engine(cls.sync_url(database), isolation_level='AUTOCOMMIT', poolclass=NullPool)
        return engine.connect()

    @classmethod
    def migrate(cls, database: str) -> None:
        """Bring one tenant database to the head of `alembic_tenant/`."""
        config = Config()
        config.set_main_option('script_location', str(_TENANT_MIGRATIONS))
        config.set_main_option(
            'sqlalchemy.url', cls.sync_url(database).render_as_string(hide_password=False).replace('%', '%%')
        )
        command.upgrade(config, 'head')

    @classmethod
    def exists(cls, database: str) -> bool:
        with cls._connect(cls.base_name()) as conn:
            return (
                conn.execute(text('SELECT 1 FROM pg_database WHERE datname = :d'), {'d': database}).first() is not None
            )

    @classmethod
    def ensure_template(cls) -> None:
        """Create the template if it is missing, and migrate it to head. Once per process."""
        template = cls.template()
        if template in cls._templates_ready:
            return
        if not cls.exists(template):
            with cls._connect(cls.base_name()) as conn:
                try:
                    conn.execute(text(f'CREATE DATABASE "{template}"'))
                except ProgrammingError as exc:
                    if not _duplicate(exc):
                        raise
        cls.migrate(template)
        cls._templates_ready.add(template)

    @classmethod
    def create(cls, database: str) -> None:
        """Create a tenant database from the template; an existing one is kept."""
        with cls._connect(cls.base_name()) as conn:
            try:
                conn.execute(text(f'CREATE DATABASE "{database}" TEMPLATE "{cls.template()}"'))
            except ProgrammingError as exc:
                if not _duplicate(exc):
                    raise
                logger.warning('tenancy: %s already existed, unregistered; adopting it', database)

    @classmethod
    def provision(cls, org_id: str) -> str:
        """Create, migrate and register an organisation's database. Idempotent."""
        with cls._connect(cls.base_name()) as conn:
            conn.execute(text('SELECT pg_advisory_lock(:k)'), {'k': _TENANT_LOCK})
            try:
                found = conn.execute(
                    text('SELECT database, state FROM tenants WHERE org_id = :o'), {'o': org_id}
                ).first()
                if found is not None:
                    if found[1] == DETACHED:
                        raise OrganisationDetached(org_id)
                    return str(found[0])
                number = int(conn.execute(text('SELECT COALESCE(MAX(number), 0) + 1 FROM tenants')).scalar_one())
                database = cls.name(number)
                cls.ensure_template()
                cls.create(database)
                cls.migrate(database)
                conn.execute(
                    text(
                        'INSERT INTO tenants (org_id, number, database, state, created_at) '
                        'VALUES (:o, :n, :d, :s, now())'
                    ),
                    {'o': org_id, 'n': number, 'd': database, 's': ACTIVE},
                )
                logger.info('tenancy: organisation provisioned as tenant %d', number)
                return database
            finally:
                conn.execute(text('SELECT pg_advisory_unlock(:k)'), {'k': _TENANT_LOCK})


class Tenants(SQLAlchemySessionManager):
    """The registry: which database is each organisation's."""

    async def database_of(self, org_id: str) -> str:
        """This organisation's database, provisioned on first use."""
        async with self.session_scope() as session:
            row = await session.get(Tenant, org_id)
        if row is not None:
            if row.state == DETACHED:
                raise OrganisationDetached(org_id)
            return str(row.database)
        return await asyncio.to_thread(TenantDatabases.provision, org_id)

    async def number_of(self, org_id: str) -> int:
        await self.database_of(org_id)
        async with self.session_scope() as session:
            row = await session.get(Tenant, org_id)
        assert row is not None
        return int(row.number)

    async def detach(self, org_id: str) -> None:
        """The organisation is deleted: keep its data, stop routing to it."""
        async with self.session_scope() as session:
            row = await session.get(Tenant, org_id)
            if row is not None:
                row.state = DETACHED

    async def active(self) -> list[Tenant]:
        async with self.session_scope() as session:
            rows = await session.execute(select(Tenant).where(Tenant.state == ACTIVE).order_by(Tenant.number))
            return list(rows.scalars().all())


class TenantSessions(metaclass=SingletonMeta):
    """Sessions on one organisation's database. One pool per database and loop."""

    _engines: ClassVar[
        weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, dict[str, tuple[AsyncEngine, async_sessionmaker]]]
    ] = weakref.WeakKeyDictionary()
    _lock: ClassVar[threading.Lock] = threading.Lock()

    def _factory(self, database: str) -> async_sessionmaker:
        loop = asyncio.get_running_loop()
        with TenantSessions._lock:
            pools = TenantSessions._engines.setdefault(loop, {})
            entry = pools.get(database)
            if entry is None:
                engine = create_async_engine(
                    TenantDatabases.async_url(database),
                    pool_pre_ping=True,
                    pool_size=settings.db_pool_size,
                    max_overflow=settings.db_max_overflow,
                    pool_timeout=settings.db_pool_timeout,
                )
                ServerTelemetry.watch_engine(engine)
                entry = (engine, async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False))
                pools[database] = entry
            return entry[1]

    @asynccontextmanager
    async def session_scope(self, org_id: str) -> AsyncIterator[AsyncSession]:
        """A transaction on this organisation's database: committed on success, rolled back on error."""
        factory = self._factory(await Tenants().database_of(org_id))
        async with factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    async def dispose(self) -> None:
        """Close this loop's tenant pools."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        with TenantSessions._lock:
            pools = TenantSessions._engines.pop(loop, {})
        for engine, _ in pools.values():
            await engine.dispose()


async def migrate_every_tenant() -> int:
    """Bring the template and every active tenant to head. For the migrate job."""
    await asyncio.to_thread(TenantDatabases.ensure_template)
    tenants = await Tenants().active()
    for tenant in tenants:
        await asyncio.to_thread(TenantDatabases.migrate, str(tenant.database))
    return len(tenants)


class TenantData(metaclass=SingletonMeta):
    """Base for classes that read and write one organisation's own data.

    Every query goes through `session_scope(org_id)`, which opens that
    organisation's database and nothing else. The few methods that are
    deployment-wide by nature walk `each_tenant()`, so the one place that
    crosses organisations is visible as such.
    """

    def session_scope(self, org_id: str) -> AbstractAsyncContextManager[AsyncSession]:
        return TenantSessions().session_scope(org_id)

    async def each_tenant(self) -> list[str]:
        """Every active organisation. Deployment-wide lookups only."""
        return [str(t.org_id) for t in await Tenants().active()]

    @staticmethod
    async def orgs_of(user_id: str, *, active_only: bool = True) -> list[str]:
        """The organisations a person is a member of, from the shared plane.

        For data that starts from a person rather than an organisation —
        their notifications, their group. In practice one: a company
        address belongs to one organisation's domain.
        """
        query = select(OrgMembership.org_id).where(OrgMembership.user_id == user_id)
        if active_only:
            query = query.where(OrgMembership.status == 'active')
        async with SQLAlchemySessionManager().session_scope() as session:
            rows = await session.execute(query.order_by(OrgMembership.org_id))
            return [str(org_id) for org_id in rows.scalars().all()]

    @staticmethod
    async def provisioned_orgs_of(user_id: str) -> list[str]:
        """The organisations a person is an active member of that have a database.

        For *reading* what starts from a person — their notifications —
        across every organisation they belong to. An organisation whose
        database was never provisioned has nothing written to it yet, so
        it is not provisioned just to be read; a detached one is not
        routed to at all. A membership that has ended drops its
        organisation here, and with it everything that lives there.
        """
        query = (
            select(OrgMembership.org_id)
            .join(Tenant, Tenant.org_id == OrgMembership.org_id)
            .where(OrgMembership.user_id == user_id, OrgMembership.status == 'active', Tenant.state == ACTIVE)
            .order_by(OrgMembership.org_id)
        )
        async with SQLAlchemySessionManager().session_scope() as session:
            rows = await session.execute(query)
            return [str(org_id) for org_id in rows.scalars().all()]
