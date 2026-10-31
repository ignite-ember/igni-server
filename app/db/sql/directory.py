"""One organisation's people, for the services that serve it.

Every query takes the organisation and joins through its memberships, so
nothing here can name somebody outside it. Former members — deactivated
memberships — are included where a service is naming an author from the
past, and excluded where it is listing who is here now.
"""

from __future__ import annotations

from sqlalchemy import func, or_, select

from app.db.sql import SQLAlchemySessionManager
from app.db.sql.models.org_membership import OrgMembership
from app.db.sql.models.user import User
from app.utils.singleton import SingletonMeta

ACTIVE = 'active'
DEACTIVATED = 'deactivated'


class OrgDirectoryDB(SQLAlchemySessionManager, metaclass=SingletonMeta):
    async def members(
        self, org_id: str, *, query: str = '', limit: int, include_former: bool = False
    ) -> list[tuple[User, OrgMembership]]:
        statuses = [ACTIVE, DEACTIVATED] if include_former else [ACTIVE]
        statement = (
            select(User, OrgMembership)
            .join(OrgMembership, OrgMembership.user_id == User.user_id)
            .where(OrgMembership.org_id == org_id, OrgMembership.status.in_(statuses))
        )
        if query.strip():
            prefix = f'{query.strip().lower()}%'
            statement = statement.where(
                or_(func.lower(User.email).like(prefix), func.lower(func.coalesce(User.full_name, '')).like(prefix))
            )
        async with self.session_scope() as session:
            result = await session.execute(statement.order_by(func.lower(User.email)).limit(limit))
            return [(row[0], row[1]) for row in result.all()]

    async def person(self, org_id: str, user_id: str) -> tuple[User, OrgMembership] | None:
        async with self.session_scope() as session:
            result = await session.execute(
                select(User, OrgMembership)
                .join(OrgMembership, OrgMembership.user_id == User.user_id)
                .where(
                    OrgMembership.org_id == org_id,
                    OrgMembership.user_id == user_id,
                    OrgMembership.status.in_([ACTIVE, DEACTIVATED]),
                )
            )
            row = result.first()
            return (row[0], row[1]) if row else None

    async def resolve(
        self, org_id: str, identities: list[str], *, include_former: bool = True
    ) -> list[tuple[User, OrgMembership]]:
        """Members named by user id or address — present and former, or
        with ``include_former=False`` only the people who are here now: an
        active membership of an account that is not deactivated."""
        emails = [value.lower() for value in identities if '@' in value]
        ids = [value for value in identities if '@' not in value]
        statement = (
            select(User, OrgMembership)
            .join(OrgMembership, OrgMembership.user_id == User.user_id)
            .where(
                OrgMembership.org_id == org_id,
                or_(User.user_id.in_(ids), func.lower(User.email).in_(emails)),
            )
        )
        if include_former:
            statement = statement.where(OrgMembership.status.in_([ACTIVE, DEACTIVATED]))
        else:
            statement = statement.where(OrgMembership.status == ACTIVE, User.deactivated_at.is_(None))
        async with self.session_scope() as session:
            result = await session.execute(statement)
            return [(row[0], row[1]) for row in result.all()]
