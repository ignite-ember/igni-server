"""Database layer for team management (org-scoped)."""

from collections.abc import Sequence
from datetime import datetime, timezone
import logging
import uuid

from fastapi import HTTPException, status
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from app.db.sql import SQLAlchemySessionManager
from app.db.sql.models.org_membership import OrgMembership
from app.db.sql.models.user import User
from app.db.sql.schemas.team import MembershipReviewRow
from app.utils.singleton import SingletonMeta

logger = logging.getLogger(__name__)

#: The unique index a duplicate violates.
_PENDING_INVITE_INDEX = 'uq_org_memberships_pending_invite'


def newest_invite_per_org(
    rows: Sequence[OrgMembership],
) -> tuple[dict[str, OrgMembership], list[OrgMembership]]:
    """Split pending invitations into the one to keep per org, and the rest.

    Used by :meth:`Admission.admit` (``app.db.sql.org_domains``), and kept here so it can be
    tested without a database. The state it guards against — two pending
    rows for one address in one org — can no longer be *created*, because
    ``uq_org_memberships_pending_invite`` refuses it; and reproducing it in
    a test by dropping the index deadlocks, since the DDL lock blocks the
    session the claim opens. Pure logic, tested directly.

    Newest wins. Re-inviting somebody is an admin restating current
    intent, so the last invite is the live one. Keeping the
    higher-privilege row instead would turn a corrective demotion into a
    silent grant, and keeping the oldest would ignore the admin's most
    recent decision.

    Ties on ``created_at`` are broken by ``id`` so the outcome is
    deterministic rather than dependent on scan order.
    """
    keep: dict[str, OrgMembership] = {}
    superseded: list[OrgMembership] = []
    for row in sorted(rows, key=lambda r: (r.created_at, r.id), reverse=True):
        org_id = str(row.org_id)
        if org_id in keep:
            superseded.append(row)
        else:
            keep[org_id] = row
    return keep, superseded


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
            result = await session.execute(
                select(OrgMembership).where(
                    OrgMembership.org_id == org_id,
                    OrgMembership.user_id == user_id,
                )
            )
            return result.scalars().first()

    async def get_active_member(self, org_id: str, user_id: str) -> OrgMembership | None:
        """Get an active membership by org + user."""
        async with self.session_scope() as session:
            result = await session.execute(
                select(OrgMembership).where(
                    OrgMembership.org_id == org_id,
                    OrgMembership.user_id == user_id,
                    OrgMembership.status == 'active',
                )
            )
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

    async def create_invited_member(
        self,
        org_id: str,
        email: str,
        role: str,
        invited_by: str,
    ) -> OrgMembership:
        """Create a new membership with status 'invited'."""
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
                # ``uq_org_memberships_pending_invite`` (migration
                # 9f2b7c41ad86). ``TeamService.invite_member`` pre-checks
                # the address and raises this same 409, but that is a
                # check-then-act: eight concurrent invites for one address
                # produced five pending rows before the index existed, and
                # racing member against admin left one of each.
                #
                # The synthetic ``user_id`` above is why the older
                # ``(org_id, user_id)`` index could not catch this — every
                # invite mints a fresh one.
                # Matched on the index name rather than a substring of the
                # driver's message — the latter was safe only because no column is named `pending_invite`. The index
                # is what is actually being detected, so it is what is named.
                if _PENDING_INVITE_INDEX not in str(getattr(exc, 'orig', exc)):
                    raise
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f'{email} already has a pending invitation to this organization.',
                ) from exc
            return member

    async def add_existing(self, org_id: str, user_id: str, *, role: str, added_by: str) -> OrgMembership:
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
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail='This account is already in this organization.'
            ) from exc
        return member

    async def update_role(self, org_id: str, user_id: str, new_role: str, assigned_by: str) -> str:
        """Update a member's role within an org. Returns the old role."""
        async with self.session_scope() as session:
            row = (
                (
                    await session.execute(
                        select(OrgMembership).where(
                            OrgMembership.org_id == org_id,
                            OrgMembership.user_id == user_id,
                        )
                    )
                )
                .scalars()
                .first()
            )

            if not row:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Member not found')

            old_role: str = row.role  # type: ignore[assignment]
            row.role = new_role  # type: ignore[assignment]
            row.updated_at = datetime.now(timezone.utc)  # type: ignore[assignment]
            await session.flush()
            return old_role

    async def update_status(self, org_id: str, user_id: str, new_status: str) -> str:
        """Update a member's status within an org. Returns the old status."""
        async with self.session_scope() as session:
            row = (
                (
                    await session.execute(
                        select(OrgMembership).where(
                            OrgMembership.org_id == org_id,
                            OrgMembership.user_id == user_id,
                        )
                    )
                )
                .scalars()
                .first()
            )

            if not row:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Member not found')

            old_status: str = row.status  # type: ignore[assignment]
            row.status = new_status  # type: ignore[assignment]
            row.updated_at = datetime.now(timezone.utc)  # type: ignore[assignment]
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
            row = (
                (
                    await session.execute(
                        select(OrgMembership).where(
                            OrgMembership.org_id == org_id,
                            OrgMembership.user_id == user_id,
                        )
                    )
                )
                .scalars()
                .first()
            )

            if not row:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Member not found')

            await session.delete(row)
            await session.flush()
