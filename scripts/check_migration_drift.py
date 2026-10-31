"""Fail loudly if SQLAlchemy models drift from alembic migrations.

The root cause of repeated "works locally, breaks in dev" issues is
that ``tests/conftest.py`` builds the schema via
``Base.metadata.create_all(engine)`` — i.e. it pulls the schema from
whatever the **models** currently say — while production runs
``alembic upgrade head``, which pulls the schema from the
**migrations**. The two diverge whenever a developer adds a table or
column to a model and forgets to write the migration.

This script applies migrations against a temporary DB, then asks
alembic's autogenerate to compare that schema against the current
models. If autogenerate sees any work to do (a missing table, missing
column, dropped index, …) we fail with a diff.

Run in CI:
    python scripts/check_migration_drift.py

Run locally before pushing:
    python scripts/check_migration_drift.py

Set ``CHECK_MIGRATION_DRIFT_DB_URL`` to override the default test DB
URL.
"""

from __future__ import annotations

import os
import sys
from contextlib import contextmanager

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, text

from app.db.sql.models import Base, load_all_models


DB_URL = os.environ.get(
    'CHECK_MIGRATION_DRIFT_DB_URL',
    'postgresql+psycopg2://myuser:mysecretpassword@localhost:5432/ember_test_drift',
)


@contextmanager
def _fresh_db():
    """Yield a connection to an empty DB. We use a dedicated DB so the
    real test DB schema isn't disturbed."""
    admin_url = DB_URL.rsplit('/', 1)[0] + '/postgres'
    db_name = DB_URL.rsplit('/', 1)[-1]
    admin_engine = create_engine(admin_url, isolation_level='AUTOCOMMIT')
    with admin_engine.connect() as c:
        c.execute(text(f'DROP DATABASE IF EXISTS {db_name}'))
        c.execute(text(f'CREATE DATABASE {db_name}'))
    admin_engine.dispose()

    engine = create_engine(DB_URL)
    try:
        yield engine
    finally:
        engine.dispose()
        with create_engine(admin_url, isolation_level='AUTOCOMMIT').connect() as c:
            c.execute(text(f'DROP DATABASE IF EXISTS {db_name}'))


def main() -> int:
    load_all_models()

    with _fresh_db() as engine:
        cfg = Config('alembic.ini')
        cfg.set_main_option('sqlalchemy.url', DB_URL)
        command.upgrade(cfg, 'head')

        with engine.connect() as conn:
            mc = MigrationContext.configure(conn)
            diff = compare_metadata(mc, Base.metadata)

    if not diff:
        print('✓ migrations match models')
        return 0

    print('✗ migrations are out of sync with the models:')
    for entry in diff:
        print(f'  {entry}')
    print()
    print('Fix: write an alembic migration for the change, then re-run.')
    print('Quick autogenerate (review before committing):')
    print('  python -m alembic revision --autogenerate -m "describe change"')
    return 1


if __name__ == '__main__':
    sys.exit(main())
