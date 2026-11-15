"""Bring every organisation's own database to head — or back, for a rollback.

`python -m app.db.sql.tenant_migrations`, run straight after
`alembic upgrade head` wherever that runs — the chart's migrate hook, the
compose `migrate` service, `make migrate`. The shared plane goes first:
the registry of which database is whose lives there.

A database created later is migrated when it is created
(`TenantRegistryDB`); this is for the ones that already exist when a
release brings a new tenant revision, and for the template every new one
is copied from.

`python -m app.db.sql.tenant_migrations --downgrade <revision>` is the
other direction, for rolling back to an older image: the template and
every active organisation's database go back to `<revision>` of
`alembic_tenant/` (`t0029`, say). `alembic downgrade` alone moves only the
shared plane, and the older image's migrate step then refuses the newer
tenant revision it cannot locate, so the stack does not start.
"""

from __future__ import annotations

import argparse
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

    def downgrade(self, revision: str) -> int:
        """Take the template and every active organisation's database back to `revision`. Returns how many."""
        downgraded = asyncio.run(self._downgrade_all(revision))
        logger.info('Downgraded %d organisation database(s) and the template to %s', downgraded, revision)
        return downgraded

    async def _downgrade_all(self, revision: str) -> int:
        # The template first: a new organisation made mid-rollback is then
        # copied at the older revision, not the newer one.
        await asyncio.to_thread(self._migrator.downgrade_template, revision)
        tenants = await self._registry.active()
        for tenant in tenants:
            await asyncio.to_thread(self._migrator.downgrade, tenant.database, revision)
        return len(tenants)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog='python -m app.db.sql.tenant_migrations',
        description="Bring every organisation's database and the template to head, or back to a revision.",
    )
    parser.add_argument(
        '--downgrade',
        metavar='REVISION',
        help='take them back to this alembic_tenant revision (e.g. t0029) instead of to head',
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO)
    if args.downgrade:
        TenantMigrations().downgrade(args.downgrade)
    else:
        TenantMigrations().run()


if __name__ == '__main__':
    main()
