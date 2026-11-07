"""How a subject access read runs a statement: `SubjectReads` and `SubjectBinds`.

Shared by `SubjectAccessDB` (`app/db/sql/subject_access.py`) and
`CreatedThingsDB` (`app/db/sql/subject_access_created.py`). Each
statement opens its own session: the engine runs at READ COMMITTED, so
sharing one would not give a snapshot anyway.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text

from app.db.sql import SQLAlchemySessionManager
from app.db.sql.schemas.subject_access import SubjectRow
from app.db.sql.tenancy import TenantSessions


class SubjectBinds(BaseModel):
    """The bind parameters a subject access statement can take, under the names its SQL uses.

    Dumped without the unset ones, so each statement receives exactly
    the parameters it names.
    """

    model_config = ConfigDict(frozen=True, populate_by_name=True, serialize_by_alias=True)

    user_id: str | None = Field(default=None, alias='u')
    email: str | None = Field(default=None, alias='e')
    org_id: str | None = Field(default=None, alias='o')
    limit: int | None = Field(default=None, alias='lim')

    def params(self) -> dict[str, str | int]:
        return self.model_dump(exclude_none=True)


class SubjectReads(SQLAlchemySessionManager):
    """Runs one statement on the shared plane or in one organisation's database."""

    def __init__(self, *, tenant_sessions: TenantSessions | None = None) -> None:
        self._tenant_sessions = tenant_sessions or TenantSessions()

    async def _rows[R: SubjectRow](self, row: type[R], sql: str, binds: SubjectBinds) -> list[R]:
        """One statement on the shared plane, each row as `row`."""
        async with self.session_scope() as session:
            result = await session.execute(text(sql), binds.params())
            return [row.model_validate(dict(found._mapping)) for found in result.all()]

    async def _org_rows[R: SubjectRow](self, org_id: str, row: type[R], sql: str, binds: SubjectBinds) -> list[R]:
        """`_rows`, in one organisation's own database."""
        async with self._tenant_sessions.session_scope(org_id) as session:
            result = await session.execute(text(sql), binds.params())
            return [row.model_validate(dict(found._mapping)) for found in result.all()]
