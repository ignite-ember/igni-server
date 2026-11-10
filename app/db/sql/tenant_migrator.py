"""The DDL that makes an organisation's database, and the migrations that bring it to head.

`CREATE DATABASE` and alembic are both synchronous — alembic drives a
psycopg2 connection, and `CREATE DATABASE` cannot run inside the
transaction an async session opens. So this is the one named thread
boundary in tenancy: :meth:`TenantMigrator.prepare`,
:meth:`TenantMigrator.migrate_in_thread` and
:meth:`TenantMigrator.ensure_template_in_thread` each hop to a thread
once, and nothing else here touches `asyncio.to_thread`.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from contextlib import contextmanager
import logging
from pathlib import Path
import threading

from alembic.config import Config
from psycopg2.errors import DuplicateDatabase
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.pool import NullPool

from alembic import command

from app.db.sql.tenant_names import TenantNames

logger = logging.getLogger(__name__)

#: Where the tenant migration tree lives.
TENANT_MIGRATIONS = Path(__file__).resolve().parents[3] / 'alembic_tenant'


class TenantMigrator:
    """Creates tenant databases from the template and migrates them. Synchronous at heart."""

    def __init__(self, *, names: TenantNames | None = None) -> None:
        self._names = names or TenantNames()
        #: Template databases this instance has already brought to head. A
        #: new organisation's database is copied from it, so migrating it
        #: again for every organisation would be an alembic run apiece for
        #: nothing. Guarded by a thread lock because it is read and written
        #: from the thread boundary.
        self._templates_ready: set[str] = set()
        self._templates_lock = threading.Lock()

    # ── The thread boundary ──────────────────────────────────────────

    async def prepare(self, database: str) -> None:
        """Make `database` exist at head: template first, then copy, then migrate. One thread hop."""
        await asyncio.to_thread(self._prepare, database)

    async def migrate_in_thread(self, database: str) -> None:
        """:meth:`migrate`, off the event loop."""
        await asyncio.to_thread(self.migrate, database)

    async def ensure_template_in_thread(self) -> None:
        """:meth:`ensure_template`, off the event loop."""
        await asyncio.to_thread(self.ensure_template)

    def _prepare(self, database: str) -> None:
        self.ensure_template()
        self.create(database)
        self.migrate(database)

    # ── Synchronous DDL and migrations ───────────────────────────────

    def alembic_config(self, database: str) -> Config:
        """The alembic configuration that targets one tenant database."""
        config = Config()
        config.set_main_option('script_location', str(TENANT_MIGRATIONS))
        # `%` is interpolation syntax in alembic's ConfigParser.
        url = self._names.sync_url(database).render_as_string(hide_password=False)
        config.set_main_option('sqlalchemy.url', url.replace('%', '%%'))
        return config

    def migrate(self, database: str) -> None:
        """Bring one tenant database to the head of `alembic_tenant/`."""
        command.upgrade(self.alembic_config(database), 'head')

    def exists(self, database: str) -> bool:
        with self._connect(self._names.base_name()) as conn:
            found = conn.execute(text('SELECT 1 FROM pg_database WHERE datname = :d'), {'d': database})
            return found.first() is not None

    def ensure_template(self) -> None:
        """Create the template if it is missing, and migrate it to head. Once per instance."""
        template = self._names.template()
        with self._templates_lock:
            if template in self._templates_ready:
                return
            if not self.exists(template):
                with self._connect(self._names.base_name()) as conn:
                    try:
                        conn.execute(text(f'CREATE DATABASE {self._quote(conn, template)}'))
                    except ProgrammingError as exc:
                        if not self._is_duplicate_database(exc):
                            raise
            self.migrate(template)
            self._templates_ready.add(template)

    def create(self, database: str) -> None:
        """Create a tenant database from the template; an existing one is kept."""
        with self._connect(self._names.base_name()) as conn:
            statement = (
                f'CREATE DATABASE {self._quote(conn, database)} TEMPLATE {self._quote(conn, self._names.template())}'
            )
            try:
                conn.execute(text(statement))
            except ProgrammingError as exc:
                if not self._is_duplicate_database(exc):
                    raise
                logger.warning('tenancy: %s already existed, unregistered; adopting it', database)

    @contextmanager
    def _connect(self, database: str) -> Iterator[Connection]:
        """An autocommit connection: `CREATE DATABASE` cannot run inside a transaction."""
        engine = create_engine(self._names.sync_url(database), isolation_level='AUTOCOMMIT', poolclass=NullPool)
        try:
            with engine.connect() as conn:
                yield conn
        finally:
            engine.dispose()

    @staticmethod
    def _quote(conn: Connection, name: str) -> str:
        """`name` as a quoted identifier, escaped by the dialect rather than by an f-string."""
        return conn.dialect.identifier_preparer.quote_identifier(name)

    @staticmethod
    def _is_duplicate_database(exc: ProgrammingError) -> bool:
        """Whether Postgres refused because the database already exists (42P04)."""
        return isinstance(exc.orig, DuplicateDatabase)
