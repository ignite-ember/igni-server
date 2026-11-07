"""The registry: which database is each organisation's.

Every read and write of the `tenants` table is here, including the one
that adds a row when an organisation's database is first made.

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

The lock is held on one pooled connection, and the registry is read and
written on that same connection, so the process holding the lock never
waits for a second pool slot while others wait on the lock. The read is
committed before the database is made, so that connection is not left
idle in a transaction for the length of an alembic run.

Creating databases needs the `CREATEDB` privilege for the service's
Postgres role. An organisation is deleted by detaching it: its database
is kept, and nothing routes to it.
"""

from __future__ import annotations

import logging

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.sql import SQLAlchemySessionManager
from app.db.sql.advisory_locks import AdvisoryLockDB
from app.db.sql.models.tenant import ACTIVE, DETACHED, Tenant
from app.db.sql.schemas.tenancy import TenantRef
from app.db.sql.tenant_migrator import TenantMigrator
from app.db.sql.tenant_names import TenantNames
from app.utils.singleton import SingletonMeta

logger = logging.getLogger(__name__)

#: Serialises creating tenant databases across every process.
TENANT_LOCK = 0x54454E414E54


class TenantRegistryDB(SQLAlchemySessionManager, metaclass=SingletonMeta):
    """The `tenants` table, and nothing else."""

    def __init__(
        self,
        *,
        migrator: TenantMigrator | None = None,
        names: TenantNames | None = None,
        locks: AdvisoryLockDB | None = None,
    ) -> None:
        self._names = names or TenantNames()
        self._migrator = migrator or TenantMigrator(names=self._names)
        self._locks = locks or AdvisoryLockDB()

    @property
    def migrator(self) -> TenantMigrator:
        """The migrator that makes this registry's databases, for the migrate job to share."""
        return self._migrator

    async def database_of(self, org_id: str) -> str:
        """This organisation's database, provisioned on first use.

        Raises `OrganisationDetached` for a deleted organisation.
        """
        return (await self._resolve(org_id)).database

    async def number_of(self, org_id: str) -> int:
        """This organisation's tenant number, provisioned on first use."""
        return (await self._resolve(org_id)).number

    async def detach(self, org_id: str) -> None:
        """The organisation is deleted: keep its data, stop routing to it. Unknown organisations are a no-op."""
        async with self.session_scope() as session:
            row = await session.get(Tenant, org_id)
            if row is not None:
                row.state = DETACHED

    async def active(self) -> list[TenantRef]:
        """Every organisation that is routed to, in tenant-number order."""
        async with self.session_scope() as session:
            rows = await session.execute(select(Tenant).where(Tenant.state == ACTIVE).order_by(Tenant.number))
            return [TenantRef.from_row(row) for row in rows.scalars().all()]

    async def active_org_ids(self) -> list[str]:
        """Every active organisation's id. For deployment-wide lookups only."""
        return [tenant.org_id for tenant in await self.active()]

    async def _resolve(self, org_id: str) -> TenantRef:
        """The routable registry entry for this organisation, provisioning it if there is none."""
        async with self.session_scope() as session:
            row = await session.get(Tenant, org_id)
            found = TenantRef.from_row(row) if row is not None else None
        if found is None:
            return await self._provision(org_id)
        found.route()
        return found

    async def _provision(self, org_id: str) -> TenantRef:
        """Create, migrate and register an organisation's database. Idempotent."""
        async with (
            self._locks.holding(TENANT_LOCK) as connection,
            AsyncSession(bind=connection, expire_on_commit=False) as session,
        ):
            row = await session.get(Tenant, org_id)
            if row is not None:
                found = TenantRef.from_row(row)
                found.route()
                return found
            number = int((await session.execute(select(func.coalesce(func.max(Tenant.number), 0) + 1))).scalar_one())
            await session.commit()

            database = self._names.name(number)
            await self._migrator.prepare(database)

            tenant = Tenant(org_id=org_id, number=number, database=database, state=ACTIVE)
            session.add(tenant)
            await session.commit()
            provisioned = TenantRef.from_row(tenant)
        logger.info('tenancy: organisation provisioned as tenant %d', number)
        return provisioned
