"""Each organisation's own database, and the sessions that reach it.

The shared plane (`settings.postgres_db`) holds only what has to know
about every organisation. Everything that belongs to one organisation —
its groups, usage, notifications and audit trail — lives in that
organisation's own logical database on the same server: `<db>_t<number>`.

A session is opened *for an organisation* (`TenantSessions.session_scope`),
so code holding it cannot see another organisation's tables at all. A
query that forgot to filter by organisation has nothing to leak, which is
the point: separation by construction rather than by remembering.

Which database is whose, and how one comes to exist, is the registry's
business (`app.db.sql.tenant_registry`). The engines themselves are the
session manager's, one per database and loop (`app.db.sql`).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.sql import SQLAlchemySessionManager
from app.db.sql.tenant_registry import TenantRegistryDB
from app.utils.singleton import SingletonMeta


class TenantSessions(metaclass=SingletonMeta):
    """Sessions on one organisation's database: routes an organisation to the manager's scope for it."""

    def __init__(
        self,
        *,
        registry: TenantRegistryDB | None = None,
        manager: SQLAlchemySessionManager | None = None,
    ) -> None:
        self._registry = registry or TenantRegistryDB()
        self._manager = manager or SQLAlchemySessionManager()

    @asynccontextmanager
    async def session_scope(self, org_id: str) -> AsyncIterator[AsyncSession]:
        """A transaction on this organisation's database: committed on success, rolled back on error."""
        async with self._manager.database_scope(await self._registry.database_of(org_id)) as session:
            yield session


class TenantScoped:
    """Reads and writes one organisation's own data, and is not a singleton.

    Every query goes through `session_scope(org_id)`, which opens that
    organisation's database and nothing else. This base is for a class
    that holds collaborators of its own: each construction is its own
    object, so what a caller injects is what it gets.
    """

    def __init__(self, *, sessions: TenantSessions | None = None) -> None:
        self._sessions = sessions or TenantSessions()

    def session_scope(self, org_id: str) -> AbstractAsyncContextManager[AsyncSession]:
        return self._sessions.session_scope(org_id)


class TenantData(TenantScoped, metaclass=SingletonMeta):
    """Base for classes that read and write one organisation's own data.

    Every query goes through `session_scope(org_id)`, which opens that
    organisation's database and nothing else. A subclass whose method is
    deployment-wide by nature takes the registry as a dependency of its
    own, so the one place that crosses organisations is visible as such.
    One instance per class: these are stateless query façades.
    """
