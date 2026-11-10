"""Bring every organisation's own database to head.

`python -m app.db.sql.tenant_migrations`, run straight after
`alembic upgrade head` wherever that runs — the chart's migrate hook, the
compose `migrate` service, `make migrate`. The shared plane goes first:
the registry of which database is whose lives there.

A database created later is migrated when it is created
(`TenantRegistryDB`); this is for the ones that already exist when a
release brings a new tenant revision, and for the template every new one
is copied from.
"""

from __future__ import annotations

import asyncio
import logging

from app.db.sql.tenant_migrator import TenantMigrator
from app.db.sql.tenant_registry import TenantRegistryDB

logger = logging.getLogger(__name__)


class TenantMigrations:
    """The migrate job's second step."""

    def __init__(self, *, registry: TenantRegistryDB | None = None, migrator: TenantMigrator | None = None) -> None:
        self._registry = registry or TenantRegistryDB()
        self._migrator = migrator or self._registry.migrator

    def run(self) -> int:
        """Migrate the template and every active organisation's database. Returns how many."""
        migrated = asyncio.run(self._migrate_all())
        logger.info('Migrated %d organisation database(s) and the template', migrated)
        return migrated

    async def _migrate_all(self) -> int:
        await self._migrator.ensure_template_in_thread()
        tenants = await self._registry.active()
        for tenant in tenants:
            await self._migrator.migrate_in_thread(tenant.database)
        return len(tenants)


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    TenantMigrations().run()
