"""The values :class:`~app.db.sql.tenant_registry.TenantRegistryDB` speaks in.

:class:`TenantRef` comes out — one registry row flattened off its
session. :class:`OrganisationDetached` is what routing to a deleted
organisation raises. Both live here, beside the models only, so a
service that catches the error or reads a tenant number does not import
the engine code that routes sessions.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from app.db.sql.models.tenant import DETACHED, Tenant


class OrganisationDetached(LookupError):
    """The organisation was deleted. Its data is kept; nothing routes to it."""


class TenantRef(BaseModel):
    """Which database is one organisation's, and whether anything routes to it."""

    model_config = ConfigDict(frozen=True)

    org_id: str
    number: int
    database: str
    state: str

    @classmethod
    def from_row(cls, row: Tenant) -> TenantRef:
        return cls(org_id=str(row.org_id), number=int(row.number), database=str(row.database), state=str(row.state))

    def route(self) -> str:
        """The database to open for this organisation. Refuses a detached one."""
        if self.state == DETACHED:
            raise OrganisationDetached(self.org_id)
        return self.database
