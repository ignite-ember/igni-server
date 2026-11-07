"""Database layer for team management (org-scoped)."""

from datetime import datetime, timezone
import uuid

from sqlalchemy import func, Select, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.sql import SQLAlchemySessionManager
from app.db.sql.models.org_membership import OrgMembership
from app.db.sql.models.user import User
from app.db.sql.schemas.membership_errors import AlreadyAMember, InvitationPending, MembershipNotFound
from app.db.sql.schemas.team import MembershipReviewRow
from app.utils.singleton import SingletonMeta


def _membership(org_id: str, user_id: str) -> Select[OrgMembership]:
    """The one membership a person has in an org."""
    return select(OrgMembership).where(OrgMembership.org_id == org_id, OrgMembership.user_id == user_id)


class TeamDB(SQLAlchemySessionManager, metaclass=SingletonMeta):
    """Database operations for team members, scoped to an org."""

    async def list_members(self, org_id: str) -> tuple[list[OrgMembership], int]:
        """Return all members of an org ordered by creation date."""
        async with self.session_scope() as session:
            total = (
                await session.execute(select(func.count(OrgMembership.id)).where(OrgMembership.org_id == org_id))
            ).scalar() or 0

            rows = (
                (
                    await session.execute(
                        select(OrgMembership)
                        .where(OrgMembership.org_id == org_id)
                        .order_by(OrgMembership.created_at, OrgMembership.user_id)
                    )
                )
                .scalars()
                .all()
            )

            return list(rows), total

    async def access_review_rows(self, org_id: str) -> list[MembershipReviewRow]:
        """Every membership joined to what is known about the person.

        One query with a `LEFT OUTER JOIN` rather than `list_members`
        followed by a user lookup per row: an access review is read at
        audit time over the whole organisation, and the N+1 version gets
        slower exactly as the organisation becomes worth reviewing.

        `OUTER`, not inner: a row with `status='invited'` has an
        `invited_email` and **no user at all** until the invitation is
        claimed. Those rows are the most interesting ones in a review —
        access granted to somebody who never arrived — so dropping them
        would remove the finding the report exists to surface.
        """
        async with self.session_scope() as session:
            rows = (
                await session.execute(
                    select(
                        OrgMembership.user_id,
                        OrgMembership.role,
                        OrgMembership.status,
                        OrgMembership.invited_email,
                        OrgMembership.invited_by,
                        OrgMembership.created_at,
                        User.email,
                        User.full_name,
                        User.auth_provider,
                        User.last_sign_in_at,
                    )
                    .outerjoin(User, User.user_id == OrgMembership.user_id)
                    .where(OrgMembership.org_id == org_id)
                    .order_by(OrgMembership.created_at, OrgMembership.user_id)
                )
            ).all()

            return [MembershipReviewRow.model_validate(dict(row._mapping)) for row in rows]

    async def get_member(self, org_id: str, user_id: str) -> OrgMembership | None:
        """Get a single membership by org + user."""
        async with self.session_scope() as session:
            return (await session.execute(_membership(org_id, user_id))).scalars().first()

    async def get_active_member(self, org_id: str, user_id: str) -> OrgMembership | None:
        """Get an active membership by org + user."""
        async with self.session_scope() as session:
            result = await session.execute(_membership(org_id, user_id).where(OrgMembership.status == 'active'))
            return result.scalars().first()

    async def get_member_by_email(self, org_id: str, email: str) -> OrgMembership | None:
        """Get a membership by org + invited email."""
        async with self.session_scope() as session:
            result = await session.execute(
                select(OrgMembership).where(
                    OrgMembership.org_id == org_id,
                    OrgMembership.invited_email == email,
                )
            )
            return result.scalars().first()

    async def create_invited_member(self, org_id: str, *, email: str, role: str, invited_by: str) -> OrgMembership:
        """Create a new membership with status 'invited'. Raises `InvitationPending` for a second one."""
        async with self.session_scope() as session:
            now = datetime.now(timezone.utc)
            member = OrgMembership(
                id=str(uuid.uuid4()),
                org_id=org_id,
                user_id=f'invited-{uuid.uuid4().hex[:12]}',
                invited_email=email,
                role=role,
                status='invited',
                invited_by=invited_by,
                created_at=now,
                updated_at=now,
            )
            session.add(member)
            try:
                await session.flush()
            except IntegrityError as exc:
                if not InvitationPending.raised_by(exc):
                    raise
                raise InvitationPending(email) from exc
            return member

    async def add_existing(self, org_id: str, *, user_id: str, role: str, added_by: str) -> OrgMembership:
        """An existing account made a member: a new membership, and so a new incarnation of its producer."""
        now = datetime.now(timezone.utc)
        member = OrgMembership(
            id=str(uuid.uuid4()),
            org_id=org_id,
            user_id=user_id,
            role=role,
            status='active',
            invited_by=added_by,
            created_at=now,
            updated_at=now,
        )
        try:
            async with self.session_scope() as session:
                session.add(member)
        except IntegrityError as exc:
            raise AlreadyAMember() from exc
        return member

    async def update_role(self, org_id: str, *, user_id: str, role: str) -> str:
        """Update a member's role within an org. Returns the old role."""
        async with self.session_scope() as session:
            row = await self._held(session, org_id=org_id, user_id=user_id)
            old_role = row.role
            row.role = role
            row.updated_at = datetime.now(timezone.utc)
            await session.flush()
            return old_role

    async def update_status(self, org_id: str, *, user_id: str, status: str) -> str:
        """Update a member's status within an org. Returns the old status."""
        async with self.session_scope() as session:
            row = await self._held(session, org_id=org_id, user_id=user_id)
            old_status = row.status
            row.status = status
            row.updated_at = datetime.now(timezone.utc)
            await session.flush()
            return old_status

    async def move_status(self, user_id: str, *, from_status: str, to_status: str) -> list[str]:
        """Every membership of a person in *from_status* set to *to_status*. Answers their organisations."""
        async with self.session_scope() as session:
            moved = await session.execute(
                update(OrgMembership)
                .where(OrgMembership.user_id == user_id, OrgMembership.status == from_status)
                .values(status=to_status, updated_at=datetime.now(timezone.utc))
                .returning(OrgMembership.org_id)
            )
            return sorted(str(org_id) for org_id in moved.scalars().all())

    async def delete(self, org_id: str, user_id: str) -> None:
        """Permanently remove a member from an org."""
        async with self.session_scope() as session:
            await session.delete(await self._held(session, org_id=org_id, user_id=user_id))
            await session.flush()

    @staticmethod
    async def _held(session: AsyncSession, *, org_id: str, user_id: str) -> OrgMembership:
        """The membership to change, or `MembershipNotFound`."""
        row = (await session.execute(_membership(org_id, user_id))).scalars().first()
        if row is None:
            raise MembershipNotFound()
        return row
