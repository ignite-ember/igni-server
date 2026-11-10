"""Who is in which group of an organisation."""

from __future__ import annotations

from datetime import datetime, timezone
import uuid

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.sql.models.org_group import OrgGroup
from app.db.sql.models.org_group_member import OrgGroupMember
from app.db.sql.schemas.group import (
    DefaultGroupKeepsItsPeople,
    GroupMembership,
    GroupSummary,
    MemberNotFound,
    NoDefaultGroup,
)
from app.db.sql.tenancy import TenantData


class GroupMembersDB(TenantData):
    """Group membership, scoped to an organisation.

    A person is in exactly one group of their organisation:
    `uq_org_group_members_user_one_group` is on `user_id` alone, and each
    organisation has its own database. So there is no "add" that could
    leave somebody in two groups. Putting a person in a group *is* moving
    them, one statement, and taking them out of a group puts them back in
    the Default one.
    """

    #: The constraint that keeps a person in one group.
    _ONE_GROUP_PER_PERSON = 'uq_org_group_members_user_one_group'

    async def list_members(self, org_id: str, group_id: str) -> list[OrgGroupMember]:
        async with self.session_scope(org_id) as session:
            rows = await session.execute(
                select(OrgGroupMember).where(OrgGroupMember.group_id == group_id).order_by(OrgGroupMember.added_at)
            )
            return list(rows.scalars().all())

    async def count_by_group(self, session: AsyncSession, *, group_ids: list[str]) -> dict[str, int]:
        """How many people each of these groups holds, in the caller's transaction. A group with none is absent."""
        rows = await session.execute(
            select(OrgGroupMember.group_id, func.count(OrgGroupMember.id))
            .where(OrgGroupMember.group_id.in_(group_ids))
            .group_by(OrgGroupMember.group_id)
        )
        return {str(group_id): int(count) for group_id, count in rows.all()}

    async def memberships(self, org_id: str) -> list[GroupMembership]:
        """Every person with a group in this organisation, and which group it is."""
        async with self.session_scope(org_id) as session:
            rows = await session.execute(
                select(OrgGroupMember.user_id, OrgGroup.id, OrgGroup.name, OrgGroup.is_default)
                .join(OrgGroup, OrgGroup.id == OrgGroupMember.group_id)
                .order_by(OrgGroupMember.user_id)
            )
            return [
                GroupMembership(
                    user_id=str(user_id), group_id=str(group_id), group_name=str(name), is_default=bool(flag)
                )
                for user_id, group_id, name, flag in rows.all()
            ]

    async def group_of(self, org_id: str, user_id: str) -> GroupSummary | None:
        """The person's group in this organisation, if they are in one."""
        async with self.session_scope(org_id) as session:
            group = (
                (
                    await session.execute(
                        select(OrgGroup)
                        .join(OrgGroupMember, OrgGroupMember.group_id == OrgGroup.id)
                        .where(OrgGroupMember.user_id == user_id, OrgGroup.org_id == org_id)
                    )
                )
                .scalars()
                .first()
            )
        return GroupSummary.of(group) if group is not None else None

    async def place(self, org_id: str, *, group_id: str, user_id: str, added_by_user_id: str | None) -> OrgGroupMember:
        """Put a person in this group, out of whichever they were in.

        One `INSERT … ON CONFLICT (user_id) DO UPDATE`: there is no
        moment at which they are in no group or in two, and two admins
        placing the same person at once leave them in whichever ran
        last rather than a 409 or a 500. Placing somebody where they
        already are changes nothing.
        """
        async with self.session_scope(org_id) as session:
            now = datetime.now(timezone.utc)
            await session.execute(
                insert(OrgGroupMember)
                .values(
                    id=str(uuid.uuid4()),
                    group_id=group_id,
                    user_id=user_id,
                    added_by_user_id=added_by_user_id,
                    added_at=now,
                )
                .on_conflict_do_update(
                    constraint=self._ONE_GROUP_PER_PERSON,
                    set_={'group_id': group_id, 'added_by_user_id': added_by_user_id, 'added_at': now},
                    where=OrgGroupMember.group_id != group_id,
                )
            )
            rows = await session.execute(select(OrgGroupMember).where(OrgGroupMember.user_id == user_id))
            return rows.scalars().one()

    async def place_if_ungrouped(
        self, org_id: str, *, group_id: str, user_ids: list[str], added_by_user_id: str | None
    ) -> int:
        """Put each of these people in this group unless they are in one already. How many were placed."""
        if not user_ids:
            return 0
        async with self.session_scope(org_id) as session:
            now = datetime.now(timezone.utc)
            result = await session.execute(
                insert(OrgGroupMember)
                .values(
                    [
                        {
                            'id': str(uuid.uuid4()),
                            'group_id': group_id,
                            'user_id': user_id,
                            'added_by_user_id': added_by_user_id,
                            'added_at': now,
                        }
                        for user_id in user_ids
                    ]
                )
                .on_conflict_do_nothing(constraint=self._ONE_GROUP_PER_PERSON)
                .returning(OrgGroupMember.user_id)
            )
            return len(result.all())

    async def return_to_default(self, org_id: str, *, group_id: str, user_id: str, by_user_id: str | None) -> None:
        """Take a person out of this group: they go back to the Default one.

        `MemberNotFound` when they are not in this group;
        `DefaultGroupKeepsItsPeople` when this *is* the Default group,
        which nobody leaves except by being placed in another, because
        there is nowhere further back to put them.
        """
        async with self.session_scope(org_id) as session:
            row = (
                (
                    await session.execute(
                        select(OrgGroupMember).where(
                            OrgGroupMember.group_id == group_id, OrgGroupMember.user_id == user_id
                        )
                    )
                )
                .scalars()
                .first()
            )
            if row is None:
                raise MemberNotFound()

            default = await self._default_group(session, org_id)
            if str(default.id) == group_id:
                raise DefaultGroupKeepsItsPeople()

            row.group_id = default.id
            row.added_by_user_id = by_user_id
            row.added_at = datetime.now(timezone.utc)
            await session.flush()

    # ------------------------------------------------------------------
    # Inside a transaction the caller opened
    # ------------------------------------------------------------------

    async def return_everyone_in(self, session: AsyncSession, *, org_id: str, from_group_id: str) -> None:
        """Move everybody in this group to the Default one, in the caller's transaction."""
        default = await self._default_group(session, org_id)
        await session.execute(
            update(OrgGroupMember).where(OrgGroupMember.group_id == from_group_id).values(group_id=default.id)
        )

    async def forget(self, session: AsyncSession, *, user_id: str) -> None:
        """Their membership goes; where they placed somebody, the record stays and their name is cleared."""
        await session.execute(delete(OrgGroupMember).where(OrgGroupMember.user_id == user_id))
        await session.execute(
            update(OrgGroupMember).where(OrgGroupMember.added_by_user_id == user_id).values(added_by_user_id=None)
        )

    async def _default_group(self, session: AsyncSession, org_id: str) -> OrgGroup:
        """The organisation's Default group, read in this transaction. `NoDefaultGroup` if there is none."""
        found = (
            (await session.execute(select(OrgGroup).where(OrgGroup.org_id == org_id, OrgGroup.is_default.is_(True))))
            .scalars()
            .first()
        )
        if found is None:
            raise NoDefaultGroup(org_id)
        return found
