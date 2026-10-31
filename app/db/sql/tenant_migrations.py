"""Bring every organisation's own database to head.

`python -m app.db.sql.tenant_migrations`, run straight after
`alembic upgrade head` wherever that runs — the chart's migrate hook, the
compose `migrate` service, `make migrate`. The shared plane goes first:
the registry of which database is whose lives there.

A database created later is migrated when it is created
(`TenantDatabases.provision`); this is for the ones that already exist
when a release brings a new tenant revision, and for the template every
new one is copied from.
"""

from __future__ import annotations

import asyncio
import logging

from app.db.sql.tenancy import migrate_every_tenant

logger = logging.getLogger(__name__)


class TenantMigrations:
    """The migrate job's second step."""

    @staticmethod
    def run() -> int:
        """Migrate the template and every active organisation's database. Returns how many."""
        migrated = asyncio.run(migrate_every_tenant())
        logger.info('Migrated %d organisation database(s) and the template', migrated)
        return migrated


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    TenantMigrations.run()
