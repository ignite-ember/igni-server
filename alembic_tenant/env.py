"""Migrations for one organisation's own database.

Run per tenant by `app.db.sql.tenancy.TenantDatabases.migrate`, which
passes the tenant's URL in `sqlalchemy.url` — this tree never targets the
shared plane, and `alembic/` never targets a tenant. Online only: a
tenant database is always there to connect to, because it was just made
from the template or already existed.
"""

from sqlalchemy import engine_from_config, pool

from alembic import context

from app.db.sql.models import load_all_models, TenantBase

load_all_models()
target_metadata = TenantBase.metadata


def run_migrations_online() -> None:
    connectable = engine_from_config(
        context.config.get_section(context.config.config_ini_section, {}),
        prefix='sqlalchemy.',
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()
    connectable.dispose()


run_migrations_online()
